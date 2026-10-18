import json

import pytest

from msafara import agents as A, graph, kb, simulator
from msafara.models import TripState
from msafara.tests.conftest import TEXT, approve_all

CLEAN = {"hotel": "clean", "transport": "clean", "activity": "clean"}


def sent(trip, kind=None):
    return [a for a in trip["audit"] if a["event"] == "message_sent" and (kind is None or a["detail"]["kind"] == kind)]


def go_to(g, gate_name, op, tid, profile=None):
    snap = graph.start_trip(g, TEXT, tid, profile or CLEAN)
    for _ in range(30):
        if snap["done"] or snap["gate"]["gate"] == gate_name:
            return snap
        snap = graph.resume_trip(g, tid, op(snap["gate"]))
    raise AssertionError("gate not reached")


def test_nothing_is_sent_before_outreach_approval(g, sim):
    snap = graph.start_trip(g, TEXT, "t1", CLEAN)
    t = snap["trip"]
    assert snap["gate"]["gate"] == "approve_outreach" and len(snap["gate"]["drafts"]) >= 9
    assert sent(t) == [] and all(d["status"] == "draft" for d in t["drafts"])
    assert t["status"] == "awaiting_approval"


def test_reject_outreach_sends_nothing(g, sim):
    graph.start_trip(g, TEXT, "t2", CLEAN)
    snap = graph.resume_trip(g, "t2", {"action": "reject"})
    assert snap["done"] and snap["trip"]["status"] == "rejected" and sent(snap["trip"]) == []


def test_per_draft_reject_drops_service_when_all_its_drafts_rejected(g, sim):
    snap = graph.start_trip(g, TEXT, "t3", CLEAN)
    ids = [d["draft_id"] for d in snap["gate"]["drafts"] if d["service"] == "activity"]
    snap = graph.resume_trip(g, "t3", {"action": "approve", "reject_ids": ids})
    st = {s["service_id"]: s["status"] for s in snap["trip"]["services"]}
    assert st["activity"] == "dropped" and st["hotel"] != "dropped"
    assert not any(a["detail"]["service"] == "activity" for a in sent(snap["trip"]))


def test_edit_that_leaks_floor_is_refused_and_good_edit_applies(g, sim):
    snap = graph.start_trip(g, TEXT, "t4", CLEAN)
    d = snap["gate"]["drafts"][0]
    svc = next(s for s in snap["trip"]["services"] if s["service_id"] == d["service"])
    floor = svc["anchors"][d["vendor_id"]]["floor_rate"]
    with pytest.raises(ValueError, match="floor"):
        graph.resume_trip(g, "t4", {"action": "edit", "edits": {d["draft_id"]: {"body": f"Dear team, we can pay INR {floor:,.0f}."}}})
    with pytest.raises(ValueError, match="internal"):
        graph.resume_trip(g, "t4", {"action": "edit", "edits": {d["draft_id"]: {"body": "Our markup is high."}}})
    snap = graph.resume_trip(g, "t4", {"action": "edit", "edits": {d["draft_id"]: {"body": "Dear team, please quote the full group. Thanks."}}})
    sent_draft = next(x for x in snap["trip"]["drafts"] if x["draft_id"] == d["draft_id"])
    assert sent_draft["edited"] and sent_draft["body"].startswith("Dear team, please quote")


def test_dispatch_refuses_unapproved_draft(g, sim):
    snap = graph.start_trip(g, TEXT, "t5", CLEAN)
    t = TripState.model_validate(snap["trip"])
    with pytest.raises(A.UnapprovedSend):
        A.dispatch_draft(t, t.drafts[0], approved_by="x")


def test_resume_without_gate_or_bad_action_is_rejected(g, sim):
    graph.start_trip(g, TEXT, "t6", CLEAN)
    with pytest.raises(ValueError):
        graph.resume_trip(g, "t6", {"action": "maybe"})
    graph.resume_trip(g, "t6", {"action": "reject"})
    with pytest.raises(ValueError, match="no open"):
        graph.resume_trip(g, "t6", {"action": "approve"})


def test_final_gate_before_any_confirmation_and_full_audit(g, sim):
    snap = go_to(g, "approve_final", approve_all, "t7")
    t = snap["trip"]
    assert snap["gate"]["gate"] == "approve_final" and sent(t, "confirm") == []
    assert snap["gate"]["net_total"] > 0 and snap["gate"]["lines"][0]["label"] in ("High", "Medium", "Low")
    snap = graph.resume_trip(g, "t7", {"action": "approve"})
    t = snap["trip"]
    assert t["status"] == "confirmed" and len(sent(t, "confirm")) == 3
    assert {a["actor"].split(":")[0] for a in t["audit"]} >= {"human", "agent", "system"}
    assert [a["ts"] for a in t["audit"]] == sorted(a["ts"] for a in t["audit"])
    assert all(s["status"] == "confirmed" for s in t["services"])


def test_final_reject_sends_no_confirmations(g, sim):
    go_to(g, "approve_final", approve_all, "t8")
    snap = graph.resume_trip(g, "t8", {"action": "reject", "note": "client changed mind"})
    assert snap["trip"]["status"] == "rejected" and sent(snap["trip"], "confirm") == []


def test_final_edit_switches_vendor_to_one_with_a_valid_quote(g, sim):
    snap = go_to(g, "approve_final", approve_all, "t9")
    line = next(l for l in snap["gate"]["lines"] if l["service_id"] == "hotel")
    alt = line["alternatives"][0]
    snap = graph.resume_trip(g, "t9", {"action": "edit", "selections": {"hotel": alt["vendor_id"]}})
    assert snap["trip"]["outcome"]["services"]["hotel"]["vendor_id"] == alt["vendor_id"]
    with pytest.raises(ValueError):
        go_to(g, "approve_final", approve_all, "t9b")
        graph.resume_trip(g, "t9b", {"action": "edit", "selections": {"hotel": "V999"}})


def test_firm_high_vendor_escalates_with_reason_and_next_move(g, sim):
    # vendor ~22% over anchor says "final"; our first counter already sits at the walk-away price,
    # so there is no honest second offer: escalate instead of re-sending the same ask
    prof = dict(CLEAN, hotel="firm_high")
    snap = go_to(g, "escalation", approve_all, "t10", prof)
    t = snap["trip"]
    hotel = next(s for s in t["services"] if s["service_id"] == "hotel")
    rates = [c["counter_rate"] for c in hotel["counters"]]
    assert 1 <= len(rates) <= 2 and rates == sorted(set(rates))           # never repeats or lowers an ask
    item = snap["gate"]["items"][0]
    assert "above anchor" in item["reason"] and "best price" in item["reason"]
    assert item["suggestion"].startswith("Accept at")
    assert [c["discount_pct"] for c in hotel["counters"]][0] <= 10


def test_counter_gate_reject_goes_to_escalation(g, sim):
    prof = dict(CLEAN, hotel="negotiable")
    snap = go_to(g, "approve_counter", approve_all, "t11", prof)
    assert snap["gate"]["drafts"][0]["discount_pct"] <= 10
    snap = graph.resume_trip(g, "t11", {"action": "reject"})
    assert snap["gate"]["gate"] == "escalation"
    assert snap["gate"]["items"][0]["code"] == "operator_rejected_counter"
    hotel = next(s for s in snap["trip"]["services"] if s["service_id"] == "hotel")
    assert hotel["counters"] == [] and hotel["negotiation_round"] == 0         # never sent -> not counted


def test_operator_counter_is_bounded_by_the_same_guardrails(g):
    def provider(trip, svc, draft, vendor):
        rank = [c.vendor_id for c in svc.candidates].index(draft.vendor_id)
        if svc.category != "hotel":
            return simulator.reply_for("clean", svc.category, rank, draft.vendor_id, svc.min_capacity, None, 0)
        if draft.kind == "counter":
            return "Hmm, will think about it and let you know."
        return simulator.reply_for("negotiable", "hotel", rank, draft.vendor_id, svc.min_capacity, None, 0)
    graph.set_reply_provider(provider)
    snap = go_to(g, "escalation", approve_all, "t12", CLEAN)
    item = snap["gate"]["items"][0]
    assert item["code"] == "counter_reply_ambiguous"
    vq = item["valid_quotes"][0]
    vid, q = vq["vendor_id"], vq["unit_rate"]
    floor = kb.truth_anchor(vid)["floor"]
    for bad in (q * 0.80, floor - 5, q):
        with pytest.raises(ValueError, match="guardrails"):
            graph.resume_trip(g, "t12", {"resolutions": {"hotel": {"action": "counter", "vendor_id": vid, "rate": bad}}})
    good = max(floor, q * 0.95)
    snap = graph.resume_trip(g, "t12", {"resolutions": {"hotel": {"action": "counter", "vendor_id": vid, "rate": good}}})
    assert snap["gate"]["gate"] == "approve_counter"
    c = snap["trip"]["services"][0]["counters"][-1]
    assert c["operator_set"] and c["counter_rate"] == good and c["round"] == 1


def test_guardrail_blocks_a_buggy_counter_and_escalates(g, sim, monkeypatch):
    monkeypatch.setattr(A, "compute_counter", lambda q, f, d: f - 100)
    prof = dict(CLEAN, hotel="negotiable")
    snap = go_to(g, "escalation", approve_all, "t13", prof)
    t = snap["trip"]
    assert snap["gate"]["items"][0]["code"] == "bound_hit"
    assert not [d for d in t["drafts"] if d["kind"] == "counter"]
    assert any(a["event"] == "guardrail_blocked" for a in t["audit"])


def test_incomplete_intake_escalates_then_human_fills_the_gap(g, sim):
    snap = graph.start_trip(g, "Need a hotel in Jaipur for 10 people on 12-15 May 2027", "t14", CLEAN)
    assert snap["gate"]["gate"] == "escalate_intake" and "hotel_star" in snap["gate"]["reason"]
    with pytest.raises(ValueError):
        graph.resume_trip(g, "t14", {"action": "approve"})
    snap = graph.resume_trip(g, "t14", {"action": "edit", "brief": {"hotel_star": 4}})
    assert snap["gate"]["gate"] == "approve_outreach"
    assert any(a["event"] == "brief_edited" for a in snap["trip"]["audit"])


def test_incomplete_intake_still_missing_asks_again_and_reject_ends(g, sim):
    graph.start_trip(g, "hotel somewhere nice", "t15", CLEAN)
    snap = graph.resume_trip(g, "t15", {"action": "edit", "brief": {"pax": 5}})
    assert snap["gate"]["gate"] == "escalate_intake"
    snap = graph.resume_trip(g, "t15", {"action": "reject"})
    assert snap["done"] and snap["trip"]["status"] == "rejected"


def test_manual_reply_flow_without_simulator(g):
    snap = graph.start_trip(g, TEXT, "t16")
    snap = graph.resume_trip(g, "t16", {"action": "approve"})
    assert snap["gate"]["gate"] == "await_replies"
    replies = {}
    for p in snap["gate"]["pending"]:
        svc = next(s for s in snap["trip"]["services"] if s["service_id"] == p["service"])
        rank = [c["vendor_id"] for c in svc["candidates"]].index(p["vendor_id"])
        replies[p["draft_id"]] = simulator.reply_for("clean", p["service"], rank, p["vendor_id"], svc["min_capacity"], None, 0)
    # leave one vendor silent -> parsed as no_response, others still decide the service
    first = next(iter(replies)); replies[first] = ""
    snap = graph.resume_trip(g, "t16", {"replies": replies})
    assert snap["gate"]["gate"] == "approve_final"
    q = [q for s in snap["trip"]["services"] for q in s["quote_history"] if q["status"] == "no_response"]
    assert len(q) == 1


def test_state_survives_restart_with_sqlite_checkpointer(tmp_path, sim):
    path = str(tmp_path / "ck.db")
    g1 = graph.build_graph(graph.sqlite_checkpointer(path))
    graph.start_trip(g1, TEXT, "t17", CLEAN)
    del g1
    g2 = graph.build_graph(graph.sqlite_checkpointer(path))
    snap = graph.snapshot(g2, "t17")
    assert snap["gate"]["gate"] == "approve_outreach"
    snap = graph.drive(g2, "t17", lambda gate: {"action": "approve"} if gate["gate"] != "escalation" else approve_all(gate))
    assert snap["trip"]["status"] == "confirmed"


def test_trip_state_json_roundtrip_and_one_shared_state():
    snap_keys = set(TripState(trip_id="x", request_text="y").model_dump())
    assert {"brief", "services", "status", "audit", "negotiation_round", "drafts"} <= snap_keys
