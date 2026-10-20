"""Eval runner.

  python -m msafara.evals.run                         # 100 trips (25 core + 75 messy/adversarial), logged to runs.jsonl
  python -m msafara.evals.run --set core              # the original 25
  python -m msafara.evals.run --workers 8             # trips in parallel processes
  python -m msafara.evals.run --label intake-v1       # label the run (use with MSAFARA_PROMPT_INTAKE / MSAFARA_MOCK_INTAKE)
  python -m msafara.evals.run --intake-only           # extraction only (fast)
  python -m msafara.evals.run --report                # print the history of logged runs

Vendors are always simulated (simulator.py). With no model configured the agents run their rule-based
paths ("mock" mode): that is the rules baseline. With a model configured the same trips run on the LLM path.
The public log records only a generic model class (MSAFARA_MODEL_CLASS); the exact model id goes to
runs.local.jsonl, which is git-ignored.
"""
import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("MSAFARA_DRY_RUN", "1")

from .. import graph, llm  # noqa: E402
from ..agents import run_intake  # noqa: E402
from . import metrics as M  # noqa: E402
from .dataset import ALL, CASES, HELDOUT  # noqa: E402

LOG = Path(__file__).parent / "runs.jsonl"
LOCAL_LOG = Path(__file__).parent / "runs.local.jsonl"
SETS = {"core": CASES, "all": ALL}


def auto_operator(gate: dict) -> dict:
    """Stands in for the human: approves every gate, drops escalated services (the escalation itself is what we score)."""
    g = gate["gate"]
    if g == "escalation":
        return {"resolutions": {i["service_id"]: {"action": "drop"} for i in gate["items"]}, "by": "eval-operator"}
    if g == "escalate_intake":
        return {"action": "reject", "by": "eval-operator"}
    return {"action": "approve", "by": "eval-operator"}


def run_case(g, case):
    tid = f"eval-{case['id']}-{time.time_ns() % 10_000_000}"
    snap = graph.start_trip(g, case["text"], trip_id=tid, simulate_profile=case["profile"])
    snap = graph.drive(g, tid, auto_operator)
    return snap["trip"]


# ------------------------------------------------------------------ one trip -> row + additive counters
def score_case(case, g=None, intake_only=False) -> dict:
    from ..models import TripBrief
    g = g or graph.build_graph()
    llm.METER.reset()
    t0 = time.time()
    if intake_only:
        brief, trip = run_intake(case["text"]), None
    else:
        trip = run_case(g, case)
        brief = TripBrief.model_validate(trip["brief"])         # score the brief the trip actually ran on
    e = M.extraction(case, brief)
    row = dict(id=case["id"], extraction_exact=e["exact"], fields=e["fields"], failed=[],
               seconds=round(time.time() - t0, 2), usage=llm.METER.snapshot())
    if not e["exact"]:
        row["failed"].append("extraction: " + ",".join(f for f, ok in e["fields"].items() if not ok))
    if intake_only:
        return row
    did_esc = M.escalated(trip)
    row.update(escalated=did_esc, expected_escalation=case["expected_escalation"], status=trip["status"])
    if did_esc != case["expected_escalation"]:
        row["failed"].append(f"escalation: expected {case['expected_escalation']}, got {did_esc}")
    viol = []
    if trip["services"]:
        rel = M.relevance(case, trip)
        row["rel"] = dict(p=rel["precision"], r=rel["recall_at5"], in_range=rel["in_range"], black=rel["blacklisted_contacted"])
        an = M.anchors_exact(trip); row["anchors"] = [an["exact"], an["n"]]
        if rel["precision"] < 1 or rel["blacklisted_contacted"]:
            row["failed"].append("matching: ineligible vendor contacted")
        b = M.bounds(trip)
        viol = b["violations"] + M.accept_ceiling_violations(trip)
        row["counters"] = b["n_counters"]
        if viol:
            row["failed"].append("price limits: " + "; ".join(viol[:2]))
        gr = M.grounding(trip); row["ground"] = [gr["total"] - len(gr["ungrounded"]), gr["total"]]
        row["ungrounded"] = len(gr["ungrounded"])
        if gr["ungrounded"]:
            row["failed"].append("grounding: " + gr["ungrounded"][0])
        qg = M.quote_grounding(trip); row["qground"] = [qg["n"] - qg["ungrounded"], qg["n"]]
        pa = M.parse_accuracy(case, trip); row["parse"] = [pa["ok"], pa["n"]]
        if pa["fails"]:
            row["failed"].append("parse: " + pa["fails"][0])
        mq = M.message_quality(trip); row["msg"] = [mq["passed"], mq["n"]]; row["judge"] = mq["judge"]
        if mq["passed"] < mq["n"]:
            row["failed"].append("message quality: " + mq["issues"][0])
    row["violations"] = viol
    ua = M.unapproved_sends(trip)
    row["unapproved"], row["sent"] = len(ua["unapproved"]), ua["sent"]
    if ua["unapproved"]:
        row["failed"].append("unapproved send")
    row["svc_esc"] = M.service_escalations(case, trip)
    row["completed"] = M.task_completed(case, trip, len(viol), len(ua["unapproved"]))
    if not row["completed"]:
        row["failed"].append(f"not completed (status {trip['status']})")
    row["decided_by"] = [s["decided_by"] for s in trip["services"] if s["decision"] or s["status"] in ("escalated", "dropped", "confirmed")]
    row["refusals"] = sum(a["detail"].get("refused", 0) for a in trip["audit"] if a["event"] == "tool_trace")
    row["fallbacks"] = sum(1 for a in trip["audit"] if "fallback" in a["event"])
    row["guardrail_events"] = sum(1 for a in trip["audit"] if a["actor"] == "guardrail")
    return row


_G = None


def _worker_init():
    global _G
    _G = graph.build_graph()


def _worker(args):
    case, intake_only = args
    try:
        return score_case(case, _G, intake_only)
    except Exception as e:  # a crashed trip is a failed trip, never a silent skip
        return dict(id=case["id"], extraction_exact=False, fields={}, failed=[f"crash: {type(e).__name__}: {str(e)[:200]}"],
                    completed=False, crashed=True, usage=llm.METER.snapshot(), seconds=0, violations=[], unapproved=0, sent=0,
                    svc_esc=[], escalated=None, expected_escalation=case["expected_escalation"])


# ------------------------------------------------------------------ aggregate
def _r(a, b):
    return round(a / b, 4) if b else None


def aggregate(rows: list[dict], cases: list[dict], intake_only: bool) -> dict:
    n = len(rows)
    by_field = {f: [0, 0] for f in M.FIELDS}
    for r in rows:
        for f, ok in (r.get("fields") or {}).items():
            by_field[f][0] += ok; by_field[f][1] += 1
    m = dict(extraction_exact=_r(sum(r["extraction_exact"] for r in rows), n),
             extraction_field_accuracy=_r(sum(v[0] for v in by_field.values()), sum(v[1] for v in by_field.values())),
             extraction_by_field={f: _r(*v) for f, v in by_field.items()})
    calls = sum(r["usage"]["calls"] for r in rows)
    tin = sum(r["usage"]["input_tokens"] for r in rows)
    tout = sum(r["usage"]["output_tokens"] for r in rows)
    cost = llm.cost_usd(tin, tout)
    m["per_trip"] = dict(llm_calls=round(calls / n, 1), input_tokens=round(tin / n), output_tokens=round(tout / n),
                         cost_usd=round(cost / n, 5) if cost is not None else None,
                         seconds=round(sum(r.get("seconds", 0) for r in rows) / n, 2))
    m["total_cost_usd"] = round(cost, 4) if cost is not None else None
    if intake_only:
        return m
    ran = [r for r in rows if not r.get("crashed")]
    esc = M.pr_recall([(r["expected_escalation"], r["escalated"]) for r in ran])
    sesc = M.pr_recall([p for r in ran for p in r["svc_esc"]])
    rel = [r["rel"] for r in ran if "rel" in r]
    s = lambda k: [sum(r[k][0] for r in ran if k in r), sum(r[k][1] for r in ran if k in r)]
    decided = [d for r in ran for d in r.get("decided_by", [])]
    m.update(
        task_completion_rate=_r(sum(r["completed"] for r in rows), n),
        price_limit_violations=sum(len(r["violations"]) for r in rows),
        bound_violations=sum(len(r["violations"]) for r in rows),          # kept for older reports
        counter_offers_sent_or_drafted=sum(r.get("counters", 0) for r in ran),
        unapproved_sends=sum(r["unapproved"] for r in rows), messages_sent=sum(r["sent"] for r in rows),
        escalation_precision=round(esc["precision"], 4), escalation_recall=round(esc["recall"], 4),
        escalation_counts=dict(tp=esc["tp"], fp=esc["fp"], fn=esc["fn"]),
        service_escalation_precision=round(sesc["precision"], 4), service_escalation_recall=round(sesc["recall"], 4),
        service_escalation_counts=dict(tp=sesc["tp"], fp=sesc["fp"], fn=sesc["fn"]),
        vendor_match_precision=round(sum(x["p"] for x in rel) / len(rel), 4) if rel else None,
        vendor_match_recall_at5=round(sum(x["r"] for x in rel) / len(rel), 4) if rel else None,
        vendor_match_3to5_rate=_r(sum(x["in_range"] for x in rel), len(rel)),
        blacklisted_vendors_contacted=sum(x["black"] for x in rel),
        anchor_exact_rate=_r(*s("anchors")),
        message_grounding_rate=_r(*s("ground")), ungrounded_messages=sum(r.get("ungrounded", 0) for r in ran), quote_grounding_rate=_r(*s("qground")),
        reply_parse_accuracy=_r(*s("parse")), message_quality_pass_rate=_r(*s("msg")),
        decisions=dict(llm=decided.count("llm"), rules=decided.count("rules"), rules_fallback=decided.count("rules(fallback)")),
        tool_refusals=sum(r.get("refusals", 0) for r in ran), llm_fallbacks=sum(r.get("fallbacks", 0) for r in ran),
        guardrail_events=sum(r.get("guardrail_events", 0) for r in ran), crashed=sum(1 for r in rows if r.get("crashed")),
    )
    ids = {c["id"] for c in CASES}
    core = [r for r in rows if r["id"] in ids]
    ext = [r for r in rows if r["id"] not in ids]
    m["completion_core25"] = _r(sum(r["completed"] for r in core), len(core))
    m["completion_extended"] = _r(sum(r["completed"] for r in ext), len(ext))
    return m


def evaluate(cases=CASES, intake_only=False, label="", log=True, workers: int = 1, heldout: bool = True) -> dict:
    t0 = time.time()
    mock = llm.mock_mode()
    if workers > 1:
        import multiprocessing as mp
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"), initializer=_worker_init) as ex:
            rows = list(ex.map(_worker, [(c, intake_only) for c in cases]))
    else:
        g = graph.build_graph()
        rows = [score_case(c, g, intake_only) for c in cases]
    metrics = aggregate(rows, cases, intake_only)
    if heldout:
        ho_ok = ho_tot = ho_exact = 0
        for h in HELDOUT:
            e = M.extraction(h, run_intake(h["text"]))
            ho_exact += e["exact"]; ho_ok += sum(e["fields"].values()); ho_tot += len(e["fields"])
        metrics["heldout_extraction_field_accuracy"] = _r(ho_ok, ho_tot)
        metrics["heldout_extraction_exact"] = _r(ho_exact, len(HELDOUT))
    n = len(rows)
    passed = sum(1 for p in rows if not p["failed"])
    rec = dict(ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), label=label,
               mode="mock" if mock else "llm", system="rules engine" if mock else "LLM agents + rules guardrails",
               model_class=llm.model_class(), backend="rules" if mock else "llm",
               prompt_versions=llm.active_versions(), mock_intake=os.environ.get("MSAFARA_MOCK_INTAKE", "v2") if mock else None,
               intake_only=intake_only, n_cases=n, pass_rate=round(passed / n, 4), cases_passed=passed,
               metrics=metrics, failed_cases=[dict(id=p["id"], why=p["failed"]) for p in rows if p["failed"]],
               judge=sorted({j for p in rows for j in p.get("judge", [])}), seconds=round(time.time() - t0, 1),
               workers=workers, git=_git())
    if log:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        if not mock:
            with LOCAL_LOG.open("a") as f:
                f.write(json.dumps(dict(ts=rec["ts"], label=label, backend=llm.backend_label(),
                                        rows=[{k: v for k, v in r.items() if k != "fields"} for r in rows])) + "\n")
    return rec


def _git():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, cwd=Path(__file__).parent).decode().strip()
    except Exception:
        return None


def history() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]


def print_report():
    runs = history()
    if not runs:
        print("no runs logged yet"); return
    print(f"{'when (UTC)':20} {'label':22} {'mode':5} {'n':>4} {'complete':>8} {'extract':>8} {'limits':>6} {'unappr':>6} {'esc P/R':>10} {'$/trip':>8} {'s/trip':>7}")
    for r in runs:
        m = r["metrics"]
        pt = m.get("per_trip") or {}
        comp = m.get("task_completion_rate")
        print(f"{r['ts'][:19]:20} {r['label'][:22]:22} {r['mode']:5} {r['n_cases']:>4} "
              f"{(f'{comp:.0%}' if comp is not None else '-'):>8} {m['extraction_field_accuracy']:8.0%} "
              f"{m.get('price_limit_violations', m.get('bound_violations', '-'))!s:>6} {m.get('unapproved_sends', '-')!s:>6} "
              f"{(str(round(m['escalation_precision'], 2)) + '/' + str(round(m['escalation_recall'], 2))) if 'escalation_precision' in m else '-':>10} "
              f"{(pt.get('cost_usd') if pt.get('cost_usd') is not None else '-')!s:>8} {pt.get('seconds', '-')!s:>7}")


def print_run(rec):
    m = rec["metrics"]
    print(f"\nRun {rec['label'] or '(unlabelled)'}  system={rec['system']}  model={rec['model_class']}  prompts={rec['prompt_versions']}")
    print(f"cases passed every check: {rec['cases_passed']}/{rec['n_cases']}")
    for k, v in m.items():
        if k != "extraction_by_field":
            print(f"  {k:34} {v}")
    print("  extraction_by_field:", m["extraction_by_field"])
    for f in rec["failed_cases"]:
        print("  FAIL", f["id"], "|", " ; ".join(f["why"])[:200])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    ap.add_argument("--set", choices=list(SETS), default="all")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--intake-only", action="store_true")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        print_report(); return
    cases = SETS[a.set]
    if a.only:
        ids = set(a.only.split(","))
        cases = [c for c in ALL if c["id"] in ids]
    print_run(evaluate(cases, intake_only=a.intake_only, label=a.label, log=not a.no_log, workers=a.workers))


if __name__ == "__main__":
    main()
