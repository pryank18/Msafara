"""Eval runner.

  python -m msafara.evals.run                      # run all 25 cases, log to evals/runs.jsonl
  python -m msafara.evals.run --label intake-v1    # label the run (use with MSAFARA_PROMPT_INTAKE / MSAFARA_MOCK_INTAKE)
  python -m msafara.evals.run --intake-only        # extraction only (fast)
  python -m msafara.evals.run --report             # print the history of logged runs

Vendors are always simulated (simulator.py). With no LLM_API_KEY the agents run their rule-based
paths ("mock" mode) - those numbers are a baseline for the pipeline, not LLM prompt results.
"""
import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("MSAFARA_DRY_RUN", "1")

from .. import graph, llm, simulator  # noqa: E402
from ..agents import finalize_brief, run_intake  # noqa: E402
from ..models import TripState  # noqa: E402
from . import metrics as M  # noqa: E402
from .dataset import CASES, HELDOUT  # noqa: E402

LOG = Path(__file__).parent / "runs.jsonl"


def auto_operator(gate: dict) -> dict:
    """Stands in for the human: approves every gate, drops escalated services (the escalation itself is what we score)."""
    g = gate["gate"]
    if g == "escalation":
        return {"resolutions": {i["service_id"]: {"action": "drop"} for i in gate["items"]}, "by": "eval-operator"}
    if g == "escalate_intake":
        return {"action": "reject", "by": "eval-operator"}
    return {"action": "approve", "by": "eval-operator"}


def run_case(g, case):
    tid = f"eval-{case['id']}-{int(time.time() * 1000) % 100000}"
    snap = graph.start_trip(g, case["text"], trip_id=tid, simulate_profile=case["profile"])
    snap = graph.drive(g, tid, auto_operator)
    return snap["trip"]


def evaluate(cases=CASES, intake_only=False, label="", log=True) -> dict:
    t0 = time.time()
    mock = llm.mock_mode()
    g = graph.build_graph()
    ext_fields = {f: [0, 0] for f in M.FIELDS}
    ext_exact = 0
    rows, failures, per_case = [], [], []
    agg = dict(rel_p=[], rel_r=[], rel_range=[], black=0, anchors=[0, 0], viol=[], counters=0, ground=[0, 0], ungrounded=[],
               qground=[0, 0], parse=[0, 0], unappr=[0, 0], msg=[0, 0], completed=[0, 0])
    for case in cases:
        brief = run_intake(case["text"])
        e = M.extraction(case, brief)
        ext_exact += e["exact"]
        for f, ok in e["fields"].items():
            ext_fields[f][0] += ok; ext_fields[f][1] += 1
        row = dict(id=case["id"], extraction_exact=e["exact"], failed=[])
        if not e["exact"]:
            row["failed"].append("extraction: " + ",".join(f for f, ok in e["fields"].items() if not ok))
        if not intake_only:
            trip = run_case(g, case)
            did_esc = M.escalated(trip)
            rows.append((case["expected_escalation"], did_esc))
            row["escalated"], row["expected_escalation"] = did_esc, case["expected_escalation"]
            if did_esc != case["expected_escalation"]:
                row["failed"].append(f"escalation: expected {case['expected_escalation']}, got {did_esc}")
            if trip["services"]:
                rel = M.relevance(case, trip)
                agg["rel_p"].append(rel["precision"]); agg["rel_r"].append(rel["recall_at5"]); agg["rel_range"].append(rel["in_range"])
                agg["black"] += rel["blacklisted_contacted"]
                an = M.anchors_exact(trip); agg["anchors"][0] += an["exact"]; agg["anchors"][1] += an["n"]
                if rel["precision"] < 1 or rel["blacklisted_contacted"]:
                    row["failed"].append("matching: ineligible vendor contacted")
                b = M.bounds(trip)
                agg["viol"] += b["violations"]; agg["counters"] += b["n_counters"]
                if b["violations"]:
                    row["failed"].append("bounds: " + "; ".join(b["violations"][:2]))
                gr = M.grounding(trip); agg["ground"][0] += gr["total"] - len(gr["ungrounded"]); agg["ground"][1] += gr["total"]
                agg["ungrounded"] += gr["ungrounded"]
                if gr["ungrounded"]:
                    row["failed"].append("grounding: " + gr["ungrounded"][0])
                qg = M.quote_grounding(trip); agg["qground"][0] += qg["n"] - qg["ungrounded"]; agg["qground"][1] += qg["n"]
                pa = M.parse_accuracy(case, trip); agg["parse"][0] += pa["ok"]; agg["parse"][1] += pa["n"]
                if pa["fails"]:
                    row["failed"].append("parse: " + pa["fails"][0])
                mq = M.message_quality(trip); agg["msg"][0] += mq["passed"]; agg["msg"][1] += mq["n"]
                if mq["passed"] < mq["n"]:
                    row["failed"].append("message quality: " + mq["issues"][0])
                row["judge"] = mq["judge"]
            ua = M.unapproved_sends(trip); agg["unappr"][0] += len(ua["unapproved"]); agg["unappr"][1] += ua["sent"]
            if ua["unapproved"]:
                row["failed"].append("unapproved send")
            agg["completed"][1] += 1
            agg["completed"][0] += (trip["status"] == "confirmed") if not case["expected_escalation"] else 1
            if not case["expected_escalation"] and trip["status"] != "confirmed":
                row["failed"].append(f"expected confirmed, status {trip['status']}")
        per_case.append(row)
    n = len(cases)
    r = lambda a, b: round(a / b, 4) if b else None
    metrics = dict(extraction_exact=r(ext_exact, n),
                   extraction_field_accuracy=r(sum(v[0] for v in ext_fields.values()), sum(v[1] for v in ext_fields.values())),
                   extraction_by_field={f: r(*v) for f, v in ext_fields.items()})
    if not intake_only:
        pr = M.pr_recall(rows)
        metrics.update(
            vendor_match_precision=round(sum(agg["rel_p"]) / len(agg["rel_p"]), 4),
            vendor_match_recall_at5=round(sum(agg["rel_r"]) / len(agg["rel_r"]), 4),
            vendor_match_3to5_rate=r(sum(agg["rel_range"]), len(agg["rel_range"])),
            blacklisted_vendors_contacted=agg["black"],
            anchor_exact_rate=r(*agg["anchors"]),
            counter_offers_sent_or_drafted=agg["counters"],
            bound_violations=len(agg["viol"]),
            escalation_precision=round(pr["precision"], 4), escalation_recall=round(pr["recall"], 4),
            escalation_counts=dict(tp=pr["tp"], fp=pr["fp"], fn=pr["fn"]),
            message_grounding_rate=r(*agg["ground"]), ungrounded_messages=len(agg["ungrounded"]),
            quote_grounding_rate=r(*agg["qground"]),
            reply_parse_accuracy=r(*agg["parse"]),
            unapproved_sends=agg["unappr"][0], messages_sent=agg["unappr"][1],
            message_quality_pass_rate=r(*agg["msg"]),
        )
    ho_ok = ho_tot = ho_exact = 0
    for h in HELDOUT:
        e = M.extraction(h, run_intake(h["text"]))
        ho_exact += e["exact"]; ho_ok += sum(e["fields"].values()); ho_tot += len(e["fields"])
    metrics["heldout_extraction_field_accuracy"] = r(ho_ok, ho_tot)
    metrics["heldout_extraction_exact"] = r(ho_exact, len(HELDOUT))
    passed = sum(1 for p in per_case if not p["failed"])
    rec = dict(ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), label=label,
               mode="mock" if mock else "llm", backend=llm.backend_label(),
               prompt_versions=llm.active_versions(), mock_intake=os.environ.get("MSAFARA_MOCK_INTAKE", "v2") if mock else None,
               intake_only=intake_only, n_cases=n, pass_rate=round(passed / n, 4), cases_passed=passed,
               metrics=metrics, failed_cases=[dict(id=p["id"], why=p["failed"]) for p in per_case if p["failed"]],
               judge=sorted({j for p in per_case for j in p.get("judge", [])}), seconds=round(time.time() - t0, 1),
               git=_git())
    if log:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


def _git():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, cwd=Path(__file__).parent).decode().strip()
    except Exception:
        return None


def history() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]


def print_report():
    runs = history()
    if not runs:
        print("no runs logged yet"); return
    print(f"{'when (UTC)':20} {'label':16} {'mode':5} {'intake':7} {'pass':>6} {'extract':>8} {'held-out':>8} {'bounds':>6} {'esc P/R':>9} {'ground':>7} {'parse':>6}")
    for r in runs:
        m = r["metrics"]
        iv = (r.get("mock_intake") or r["prompt_versions"].get("intake", "?"))
        print(f"{r['ts'][:19]:20} {r['label'][:16]:16} {r['mode']:5} {iv:7} {r['pass_rate']:6.0%} {m['extraction_field_accuracy']:8.0%} {m.get('heldout_extraction_field_accuracy') or 0:8.0%} "
              f"{m.get('bound_violations', '-')!s:>6} "
              f"{(str(round(m['escalation_precision'], 2)) + '/' + str(round(m['escalation_recall'], 2))) if 'escalation_precision' in m else '-':>9} "
              f"{m.get('message_grounding_rate', 0) or 0:7.0%} {m.get('reply_parse_accuracy', 0) or 0:6.0%}")


def print_run(rec):
    m = rec["metrics"]
    print(f"\nRun {rec['label'] or '(unlabelled)'}  mode={rec['mode']}  backend={rec['backend']}  intake={rec.get('mock_intake') or rec['prompt_versions']['intake']}")
    print(f"cases passed: {rec['cases_passed']}/{rec['n_cases']}  (pass rate {rec['pass_rate']:.0%}; a case passes only if EVERY check passes)")
    for k, v in m.items():
        if k != "extraction_by_field":
            print(f"  {k:34} {v}")
    print("  extraction_by_field:", m["extraction_by_field"])
    for f in rec["failed_cases"]:
        print("  FAIL", f["id"], "|", " ; ".join(f["why"])[:180])
    if rec["mode"] == "mock":
        print("\n  NOTE: mock mode = rule-based baseline. Not evidence of LLM prompt quality.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    ap.add_argument("--intake-only", action="store_true")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        print_report(); return
    print_run(evaluate(intake_only=a.intake_only, label=a.label, log=not a.no_log))


if __name__ == "__main__":
    main()
