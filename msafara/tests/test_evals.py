"""The eval harness itself must be able to FAIL. These tests sabotage the system and expect the metrics to notice."""
import json

import pytest

from msafara import agents as A, graph, simulator
from msafara.evals import metrics as M, run
from msafara.evals.dataset import CASES


def test_dataset_shape():
    assert len(CASES) == 25
    assert 8 <= sum(c["expected_escalation"] for c in CASES) <= 12
    assert {c["truth"]["city"] for c in CASES if c["truth"]["city"]} == {"Jaipur", "Goa", "Zanzibar", "Arusha"}
    assert len({p for c in CASES for p in c["profile"].values()}) >= 8


def test_clean_run_passes_everything_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "LOG", tmp_path / "runs.jsonl")
    rec = run.evaluate(label="t", log=True)
    m = rec["metrics"]
    assert rec["pass_rate"] == 1.0 and rec["mode"] == "mock"
    assert m["bound_violations"] == 0 and m["unapproved_sends"] == 0 and m["blacklisted_vendors_contacted"] == 0
    assert m["escalation_precision"] == 1 and m["escalation_recall"] == 1
    assert m["message_grounding_rate"] == 1 and m["quote_grounding_rate"] == 1 and m["anchor_exact_rate"] == 1
    assert m["counter_offers_sent_or_drafted"] > 10                       # the bound check actually had counters to check
    logged = run.history()
    assert len(logged) == 1 and logged[0]["metrics"]["extraction_exact"] == m["extraction_exact"]


def test_intake_v1_vs_v2_score_differs(monkeypatch):
    monkeypatch.setenv("MSAFARA_MOCK_INTAKE", "v1")
    v1 = run.evaluate(intake_only=True, log=False)
    monkeypatch.setenv("MSAFARA_MOCK_INTAKE", "v2")
    v2 = run.evaluate(intake_only=True, log=False)
    assert v2["metrics"]["extraction_field_accuracy"] > v1["metrics"]["extraction_field_accuracy"] + 0.2
    assert v1["metrics"]["extraction_field_accuracy"] < 0.8


def _subset(*ids):
    return [c for c in CASES if c["id"] in ids]


def test_metric_catches_counter_below_floor_when_guardrails_are_disabled(monkeypatch):
    monkeypatch.setattr(A, "compute_counter", lambda q, f, d: f - 50)
    monkeypatch.setattr(A, "assert_counter_bounds", lambda *a, **k: None)
    rec = run.evaluate(_subset("c02", "c10"), log=False)
    assert rec["metrics"]["bound_violations"] > 0 and rec["pass_rate"] < 1


def test_metric_catches_discount_over_10pct(monkeypatch):
    monkeypatch.setattr(A, "compute_counter", lambda q, f, d: float(int(q * 0.8)) if int(q * 0.8) >= f else f)
    monkeypatch.setattr(A, "assert_counter_bounds", lambda *a, **k: None)
    rec = run.evaluate(_subset("c02", "c10", "c21"), log=False)
    assert rec["metrics"]["bound_violations"] > 0


def test_metric_catches_invented_price_in_outreach(monkeypatch):
    orig = A._outreach_template
    def bad(trip, svc, vname, a, canc):
        m = orig(trip, svc, vname, a, canc); m.body += "\nWe usually pay INR 1,234 for this."; return m
    monkeypatch.setattr(A, "_outreach_template", bad)
    rec = run.evaluate(_subset("c01"), log=False)
    assert rec["metrics"]["ungrounded_messages"] > 0 and rec["metrics"]["message_grounding_rate"] < 1


def test_metric_catches_floor_leak(monkeypatch):
    orig = A._outreach_template
    def bad(trip, svc, vname, a, canc):
        m = orig(trip, svc, vname, a, canc); m.body += f"\nOur floor rate is INR {a.floor_rate:,.0f}."; return m
    monkeypatch.setattr(A, "_outreach_template", bad)
    rec = run.evaluate(_subset("c01"), log=False)
    assert rec["metrics"]["ungrounded_messages"] > 0


def test_escalation_recall_drops_if_agent_never_escalates(monkeypatch):
    orig = A.decide_service
    def never_escalate(svc, policy):
        orig(svc, policy)
        if svc.decision == "escalate":
            svc.decision, svc.status, svc.escalation = "accept", "dropped", None
    monkeypatch.setattr(A, "decide_service", never_escalate)
    rec = run.evaluate(_subset("c08", "c11", "c13", "c01"), log=False)
    assert rec["metrics"]["escalation_recall"] < 1


def test_escalation_precision_drops_if_agent_escalates_everything(monkeypatch):
    orig = A.decide_service
    def always(svc, policy):
        orig(svc, policy)
        svc.decision, svc.status = "escalate", "escalated"
        svc.escalation = A.Escalation(reason="paranoid", code="x", options=[])
    monkeypatch.setattr(A, "decide_service", always)
    rec = run.evaluate(_subset("c01", "c03", "c08"), log=False)
    assert rec["metrics"]["escalation_precision"] < 1


def test_unapproved_send_metric_flags_missing_approval():
    trip = {"audit": [
        {"actor": "system", "node": "send", "event": "message_sent", "detail": {"draft_id": "d1", "kind": "outreach"}},
        {"actor": "human", "node": "gate_outreach", "event": "gate_approve", "detail": {"approved": ["d2"]}},
        {"actor": "system", "node": "send", "event": "message_sent", "detail": {"draft_id": "d2", "kind": "outreach"}},
        {"actor": "system", "node": "finalize", "event": "message_sent", "detail": {"draft_id": "d3", "kind": "confirm"}}]}
    r = M.unapproved_sends(trip)
    assert r["unapproved"] == ["d1", "d3"]


def test_extraction_metric_marks_wrong_field():
    from msafara.models import TripBrief
    c = CASES[0]
    b = A.finalize_brief(A.mock_intake(c["text"]))
    assert M.extraction(c, b)["exact"]
    b.pax = 99
    r = M.extraction(c, b)
    assert not r["exact"] and not r["fields"]["pax"]


def test_vendor_match_metric_flags_blacklisted_vendor(monkeypatch):
    import copy
    case = CASES[0]
    g = graph.build_graph(); graph.set_reply_provider(simulator.make_provider())
    trip = run.run_case(g, case)
    bad = copy.deepcopy(trip)
    from msafara import kb
    bl = next(v for v in kb.vendors() if v["blacklisted"])
    bad["services"][1]["candidates"][0]["vendor_id"] = bl["vendor_id"]
    r = M.relevance(case, bad)
    assert r["blacklisted_contacted"] == 1 and r["precision"] < 1


def test_judge_heuristic_flags_bad_messages():
    assert M.heuristic_judge("Send price NOW. You must reply immediately.", "outreach").tone <= 2
    good = "Dear X team,\nplease confirm availability for 6 guests on 14 Mar 2027 and your rate inclusive of all taxes.\nKind regards"
    j = M.heuristic_judge(good, "outreach"); assert min(j.tone, j.clarity, j.concision) >= 4


def test_report_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run, "LOG", tmp_path / "r.jsonl")
    run.evaluate(CASES[:3], label="a", log=True)
    run.print_report()
    out = capsys.readouterr().out
    assert "label" in out and " a " in out
