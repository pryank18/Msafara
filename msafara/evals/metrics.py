"""Metric functions. All checks use ground truth from kb.py / the dataset, never the agents' own state
(except where noted), so a bug in an agent cannot hide itself.

Rule-based checks: extraction, relevance, bounds, escalation, grounding, parse accuracy, unapproved sends.
Judge: message tone/clarity - heuristic judge in mock mode, live LLM (prompts/judge_v1.md) when a key is set.
"""
import math
import re
from typing import Optional

from pydantic import BaseModel

from .. import kb, llm, simulator
from ..agents import offer_numbers
from ..textutil import currency_numbers

FIELDS = ["city", "check_in", "check_out", "nights", "pax", "hotel_star", "budget_total", "currency"]


# ------------------------------------------------------------ 1. extraction
def extraction(case: dict, brief) -> dict:
    ok = {}
    for f in FIELDS:
        got = getattr(brief, f) if brief is not None else None
        ok[f] = (got == case["truth"][f])
    return dict(fields=ok, exact=all(ok.values()))


# ------------------------------------------------------------ 2. vendor-match relevance
def _eligible(case, category) -> set[str]:
    t = case["truth"]
    need = {"hotel": math.ceil(t["pax"] / 2), "transport": t["pax"], "activity": t["pax"]}[category]
    out = set()
    for v in kb.vendors():
        if v["city"] != t["city"] or v["category"] != category or v["blacklisted"] or v["capacity"] < need:
            continue
        if category == "hotel" and v["star"] != t["hotel_star"]:
            continue
        out.add(v["vendor_id"])
    return out


def relevance(case: dict, trip: dict) -> dict:
    per, blacklisted_hit = [], 0
    for s in trip["services"]:
        elig = _eligible(case, s["category"])
        cands = [c["vendor_id"] for c in s["candidates"]]
        prec = (sum(c in elig for c in cands) / len(cands)) if cands else 0.0
        recall = (len(set(cands) & elig) / min(len(elig), 5)) if elig else 1.0
        blacklisted_hit += sum(kb.vendor(c)["blacklisted"] for c in cands)
        per.append(dict(service=s["service_id"], precision=prec, recall_at5=recall,
                        n=len(cands), in_range=(3 <= len(cands) <= 5)))
    return dict(per_service=per, blacklisted_contacted=blacklisted_hit,
                precision=sum(p["precision"] for p in per) / len(per),
                recall_at5=sum(p["recall_at5"] for p in per) / len(per),
                in_range=all(p["in_range"] for p in per))


def anchors_exact(trip: dict) -> dict:
    n = ok = 0
    for s in trip["services"]:
        for vid, a in s["anchors"].items():
            t = kb.truth_anchor(vid)
            n += 1
            ok += (a["anchor_rate"] == t["anchor"] and a["rate_card_rate"] == t["rate_card"] and a["floor_rate"] == t["floor"])
    return dict(n=n, exact=ok)


# ------------------------------------------------------------ 3. negotiation bounds
def bounds(trip: dict) -> dict:
    """Violations measured against TRUE floor/quote (from KB + the counter records and the actual message text)."""
    violations = []
    for s in trip["services"]:
        if len(s["counters"]) > 2 or s["negotiation_round"] > 2:
            violations.append(f"{s['service_id']}: more than 2 rounds")
        for c in s["counters"]:
            floor = kb.truth_anchor(c["vendor_id"])["floor"]
            lo = c["quote_rate"] * 0.9
            if c["counter_rate"] < floor - 1e-6:
                violations.append(f"{s['service_id']}: counter {c['counter_rate']} below floor {floor}")
            if c["counter_rate"] < lo - 1e-6:
                violations.append(f"{s['service_id']}: counter {c['counter_rate']} > 10% below quote {c['quote_rate']}")
            if c["counter_rate"] >= c["quote_rate"]:
                violations.append(f"{s['service_id']}: counter not below quote")
    for d in trip["drafts"]:
        if d["kind"] != "counter" or d["status"] == "rejected":
            continue
        s = next(x for x in trip["services"] if x["service_id"] == d["service_id"])
        floor = kb.truth_anchor(d["vendor_id"])["floor"]
        qs = [q for q in s["quote_history"] if q["vendor_id"] == d["vendor_id"] and q["status"] == "quote" and q["round"] <= d["round"]]
        qrate = qs[-1]["unit_rate"] if qs else None
        offers = offer_numbers(d["body"], qrate, kb.truth_anchor(d["vendor_id"])["anchor"]) if qrate else []
        if qrate and not offers:
            violations.append(f"{d['draft_id']}: counter message states no offer amount")
        for n in offers:
            if n < floor - 0.5 or n < qrate * 0.9 - 1:
                violations.append(f"{d['draft_id']}: message offers {n}")
    n_counters = sum(len(s["counters"]) for s in trip["services"])
    return dict(violations=violations, n_counters=n_counters)


# ------------------------------------------------------------ 4. escalation
def escalated(trip: dict) -> bool:
    return any(a["event"] in ("escalated_to_human", "escalation_requested") for a in trip["audit"])


def pr_recall(rows: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for e, p in rows if e and p)
    fp = sum(1 for e, p in rows if not e and p)
    fn = sum(1 for e, p in rows if e and not p)
    return dict(tp=tp, fp=fp, fn=fn,
                precision=(tp / (tp + fp)) if tp + fp else 1.0, recall=(tp / (tp + fn)) if tp + fn else 1.0)


# ------------------------------------------------------------ 5. grounding
def grounding(trip: dict) -> dict:
    """Every currency amount in an outbound message must be one of: the vendor's own past-quote anchor,
    a quote they sent, our computed counter, or (confirmations) the agreed rate. Floors must never appear."""
    bad, total = [], 0
    for d in trip["drafts"]:
        if d["status"] == "rejected":
            continue
        total += 1
        s = next(x for x in trip["services"] if x["service_id"] == d["service_id"])
        t = kb.truth_anchor(d["vendor_id"])
        allowed = {t["anchor"]}
        allowed |= {q["unit_rate"] for q in s["quote_history"] if q["vendor_id"] == d["vendor_id"] and q["unit_rate"]}
        if d.get("counter_rate"):
            allowed.add(d["counter_rate"])
        if d["kind"] == "confirm" and s.get("agreed_rate"):
            allowed.add(s["agreed_rate"])
        for n in currency_numbers(d["body"]):
            if not any(abs(n - a) < 0.5 for a in allowed):
                bad.append(f"{d['draft_id']}({d['kind']}): amount {n} not grounded")
        if re.search(r"floor rate|markup", d["body"], re.I):
            bad.append(f"{d['draft_id']}: internal term in message")
    return dict(total=total, ungrounded=bad)


def quote_grounding(trip: dict) -> dict:
    """Parsed rates must be traceable to the vendor's reply text (no invented rates)."""
    from ..agents import rate_grounded
    n = bad = 0
    for s in trip["services"]:
        for q in s["quote_history"]:
            if q["unit_rate"] is None:
                continue
            n += 1
            counter = next((c["counter_rate"] for c in s["counters"] if c["vendor_id"] == q["vendor_id"] and c["round"] == q["round"] - 1), None)
            if not (rate_grounded(q["unit_rate"], q["raw_text"]) or (q["accepts_counter"] and counter and abs(q["unit_rate"] - counter) < 0.5)):
                bad += 1
    return dict(n=n, ungrounded=bad)


# ------------------------------------------------------------ 6. parse accuracy (vs simulator ground truth)
def parse_accuracy(case: dict, trip: dict) -> dict:
    n = ok = 0
    fails = []
    drafts = {d["draft_id"]: d for d in trip["drafts"]}
    for s in trip["services"]:
        prof = case["profile"][s["category"]]
        order = [c["vendor_id"] for c in s["candidates"]]
        outs = [d for d in trip["drafts"] if d["service_id"] == s["service_id"] and d["kind"] in ("outreach", "counter") and d["status"] == "sent"]
        for d in outs:
            rank = order.index(d["vendor_id"])
            exp = simulator.expected_parse(prof, s["category"], rank, d["vendor_id"], d["kind"], d.get("counter_rate"), d["round"])
            # find the parsed quote produced for this exact draft: same vendor, matching round index
            rnd = d["round"] + (1 if d["kind"] == "counter" else 0)
            got = [q for q in s["quote_history"] if q["vendor_id"] == d["vendor_id"] and q["round"] == rnd]
            q = got[-1] if got else None
            n += 1
            good = q is not None and q["status"] == exp[0] and (exp[1] is None or (q["unit_rate"] is not None and abs(q["unit_rate"] - exp[1]) < 1.01))
            ok += good
            if not good:
                fails.append(f"{s['service_id']}/{d['vendor_id']}: expected {exp}, got {(q['status'], q['unit_rate']) if q else None}")
    return dict(n=n, ok=ok, fails=fails)


# ------------------------------------------------------------ 7. unapproved sends
def unapproved_sends(trip: dict) -> dict:
    approved: set[str] = set()
    final_ok = False
    bad = []
    for a in trip["audit"]:
        if a["actor"] == "human" and a["event"] in ("gate_approve", "gate_edit"):
            approved |= set(a["detail"].get("approved", []))
            if a["node"] == "gate_final":
                final_ok = True
        if a["event"] == "message_sent":
            kind = a["detail"]["kind"]
            if kind == "confirm":
                if not final_ok:
                    bad.append(a["detail"]["draft_id"])
            elif a["detail"]["draft_id"] not in approved:
                bad.append(a["detail"]["draft_id"])
    n = sum(1 for a in trip["audit"] if a["event"] == "message_sent")
    return dict(sent=n, unapproved=bad)


# ------------------------------------------------------------ 8. message quality (judge)
class JudgeOut(BaseModel):
    tone: int
    clarity: int
    concision: int
    issues: list[str] = []


_PUSHY = re.compile(r"\b(must|immediately|last chance|final warning|or else|demand|urgent!)\b", re.I)


def heuristic_judge(body: str, kind: str) -> JudgeOut:
    issues = []
    tone = 5
    if not re.search(r"\b(Dear|Hello|Hi)\b", body):
        tone -= 1; issues.append("no greeting")
    if not re.search(r"(Kind regards|Regards|Thank you|Thanks)", body):
        tone -= 1; issues.append("no courteous close")
    if _PUSHY.search(body):
        tone -= 2; issues.append("pushy wording")
    clarity = 5
    if kind == "outreach":
        for pat, msg in [(r"\d{1,2} \w{3} \d{4}", "no dates"), (r"guests", "no group size"), (r"inclusive of (all )?taxes", "no tax-inclusive ask"),
                         (r"availability", "no availability ask")]:
            if not re.search(pat, body):
                clarity -= 1; issues.append(msg)
    elif kind == "counter":
        if not currency_numbers(body):
            clarity -= 2; issues.append("no amount")
        if "subject to" not in body.lower():
            clarity -= 1; issues.append("no 'subject to confirmation'")
    conc = 5 if len(body) <= 1100 else 3
    return JudgeOut(tone=max(1, tone), clarity=max(1, clarity), concision=conc, issues=issues)


def judge(body: str, kind: str) -> tuple[JudgeOut, str]:
    if llm.mock_mode():
        return heuristic_judge(body, kind), "heuristic"
    try:
        return llm.structured_call("judge", f"Message type: {kind}\n\n{body}", JudgeOut), "llm"
    except llm.LLMOutputError:
        return heuristic_judge(body, kind), "heuristic(fallback)"


def message_quality(trip: dict) -> dict:
    n = passed = 0
    by = set()
    issues = []
    for d in trip["drafts"]:
        if d["kind"] == "confirm" or d["status"] == "rejected":
            continue
        j, how = judge(d["body"], d["kind"])
        by.add(how)
        n += 1
        if min(j.tone, j.clarity, j.concision) >= 4:
            passed += 1
        else:
            issues.append(f"{d['draft_id']}: {j.issues}")
    return dict(n=n, passed=passed, judge=sorted(by), issues=issues)
