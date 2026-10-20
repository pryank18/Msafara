"""Metric functions. All checks use ground truth from kb.py / the dataset, never the agents' own state
(except where noted), so a bug in an agent cannot hide itself.

Rule-based checks: extraction, relevance, bounds, escalation, grounding, parse accuracy, unapproved sends.
Judge: message tone/clarity - heuristic judge in mock mode, live LLM (prompts/judge_v1.md) when a key is set.
"""
import math
import os
import re
from typing import Optional

from pydantic import BaseModel

from .. import kb, llm, simulator
from ..agents import offer_numbers
from ..textutil import currency_numbers, money_mentions

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
    if t["city"] is None or t["pax"] is None or t["hotel_star"] is None:
        return set()          # the trip should never have reached matching: no vendor counts as eligible
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
        recall = (len(set(cands) & elig) / min(len(elig), 5)) if elig else (0.0 if case.get("incomplete") else 1.0)
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
        anchor = kb.truth_anchor(d["vendor_id"])["anchor"]
        offers = [n for n in money_mentions(d["body"]) if qrate and not any(abs(n - r) < 0.5 for r in (qrate, anchor))]
        if d.get("counter_rate") and any(abs(n - d["counter_rate"]) < 0.5 for n in money_mentions(d["body"])):
            offers = offers + [d["counter_rate"]]          # the counter may equal the anchor; it is still the offer
        if qrate and not offers:
            violations.append(f"{d['draft_id']}: counter message states no offer amount "
                              f"(counter {d.get('counter_rate')}, quote {qrate}, anchor {anchor})")
        for n in offers:
            if n < floor - 0.5 or n < qrate * 0.9 - 1:
                violations.append(f"{d['draft_id']}: message offers {n} (quote {qrate}, floor {floor})")
    n_counters = sum(len(s["counters"]) for s in trip["services"])
    return dict(violations=violations, n_counters=n_counters)


# ------------------------------------------------------------ ground-truth replies (for limit checks)
def _true_replies(case: dict, trip: dict, svc: dict, vid: str) -> list[dict]:
    """What the vendor REALLY said to each message we sent it, in order, from the simulator's ground truth.
    Each item: {round, kind, status, rate} where round is the round of the reply (0 = reply to outreach)."""
    prof = case["profile"][svc["category"]]
    order = [c["vendor_id"] for c in svc["candidates"]]
    rank = order.index(vid)
    out = []
    for d in trip["drafts"]:
        if d["vendor_id"] != vid or d["service_id"] != svc["service_id"] or d["status"] != "sent" or d["kind"] == "confirm":
            continue
        st, rate = simulator.expected_parse(prof, svc["category"], rank, vid, d["kind"], d.get("counter_rate"), d["round"])
        out.append(dict(round=d["round"] + (1 if d["kind"] == "counter" else 0), kind=d["kind"], status=st, rate=rate,
                        counter_sent=d.get("counter_rate")))
    return out


def true_bound_violations(case: dict, trip: dict) -> list[str]:
    """Every counter checked against the vendor's TRUE latest quote (not the agent's reading of it)."""
    bad = []
    for s in trip["services"]:
        for c in s["counters"]:
            vid, floor = c["vendor_id"], kb.truth_anchor(c["vendor_id"])["floor"]
            prior = [r for r in _true_replies(case, trip, s, vid) if r["round"] <= c["round"] and r["status"] == "quote" and r["rate"]]
            if not prior:
                bad.append(f"{s['service_id']}: counter {c['counter_rate']} to {vid}, who never gave a usable quote")
                continue
            q = prior[-1]["rate"]
            if c["counter_rate"] < max(floor, q * 0.9) - 1e-6 or c["counter_rate"] >= q:
                bad.append(f"{s['service_id']}: counter {c['counter_rate']} vs true quote {q} (floor {floor})")
    return bad


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
        for n in money_mentions(d["body"]):
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
    """MSAFARA_JUDGE picks one judge for every system so comparisons are like for like:
    'heuristic' (rule-based), 'llm' (the model grades both columns, even when the agents run on rules)."""
    mode = os.environ.get("MSAFARA_JUDGE", "")
    if mode == "heuristic" or (mode != "llm" and llm.mock_mode()):
        return heuristic_judge(body, kind), "heuristic"
    try:
        return llm.structured_call("judge", f"Message type: {kind}\n\n{body}", JudgeOut, judge=True), "llm"
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


# ------------------------------------------------------------ 9. price limits beyond counters
def accept_ceiling_violations(trip: dict, tolerance_pct: float = 10.0, case: Optional[dict] = None) -> list[str]:
    """Every automatic accept must cost at most the TRUE anchor x (1 + tolerance), unless the vendor truly agreed to a
    counter we sent. With `case`, the price is the vendor's TRUE final rate from the simulator, so a tax-exclusive
    'agreed' read as inclusive is caught. Operator accepts are exempt (in evals the operator never accepts)."""
    bad = []
    human_accepts = {a["detail"].get("service") for a in trip["audit"] if a["event"] == "resolved_accept"}
    for s in trip["services"]:
        if s["status"] not in ("accepted", "confirmed") or not s["agreed_rate"] or s["service_id"] in human_accepts:
            continue
        vid = s["selected_vendor_id"]
        ceiling = kb.truth_anchor(vid)["anchor"] * (1 + tolerance_pct / 100.0)
        if case is not None:
            replies = _true_replies(case, trip, s, vid)
            last = replies[-1] if replies else None
            if not last or last["status"] != "quote" or last["rate"] is None:
                bad.append(f"{s['service_id']}: accepted {vid} whose last true reply was {last and last['status']}")
                continue
            true_rate = last["rate"]
            agreed_counter = last["kind"] == "counter" and last["counter_sent"] and abs(true_rate - last["counter_sent"]) < 0.5
            if abs(true_rate - s["agreed_rate"]) >= 1.01:
                bad.append(f"{s['service_id']}: booked at {s['agreed_rate']} but the vendor's true rate is {true_rate}")
            elif true_rate > ceiling + 0.5 and not agreed_counter:
                bad.append(f"{s['service_id']}: accepted {true_rate} above ceiling {ceiling:.0f}")
            continue
        q = next((q for q in reversed(s["quote_history"]) if q["vendor_id"] == vid and q["unit_rate"]
                  and abs(q["unit_rate"] - s["agreed_rate"]) < 0.5), None)
        matched = q and q["accepts_counter"] and s["counters"] and s["counters"][-1]["vendor_id"] == vid \
            and abs(s["counters"][-1]["counter_rate"] - s["agreed_rate"]) < 0.5
        if not matched and s["agreed_rate"] > ceiling + 0.5:
            bad.append(f"{s['service_id']}: accepted {s['agreed_rate']} above ceiling {ceiling:.0f}")
    return bad


# ------------------------------------------------------------ 10. service-level escalation + task completion
def service_escalations(case: dict, trip: dict) -> list[tuple[bool, bool]]:
    """(expected, got) per service, for trips that got past intake."""
    if not trip["services"]:
        return []
    esc = {a["detail"].get("service") for a in trip["audit"] if a["event"] == "escalated_to_human"}
    return [(case["profile"][s["category"]] in simulator.ESCALATING, s["service_id"] in esc) for s in trip["services"]]


def task_completed(case: dict, trip: dict, violations: int, unapproved: int) -> bool:
    """A trip is complete when it reaches the RIGHT end state with no guardrail breach:
    confirmed with no escalation when none was needed; when one was needed, every service that needed a human
    got one (and intake-incomplete trips stopped at intake)."""
    if violations or unapproved:
        return False
    if case["incomplete"]:
        return any(a["event"] == "escalation_requested" for a in trip["audit"]) and not trip["services"]
    pairs = service_escalations(case, trip)
    if not case["expected_escalation"]:
        return trip["status"] == "confirmed" and not escalated(trip)
    if not (pairs and all(got for exp, got in pairs if exp) and not any(got for exp, got in pairs if not exp)):
        return False
    must_book = [s for s in trip["services"] if case["profile"][s["category"]] not in simulator.ESCALATING]
    return all(s["status"] == "confirmed" for s in must_book)
