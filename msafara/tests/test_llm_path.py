"""LLM code path exercised with a scripted stub client (no network). This proves the plumbing and the
guardrails around LLM output - it says nothing about how good a real model's wording or extraction is."""
import json

from msafara import agents as A, graph, kb, simulator
from msafara.evals.dataset import CASES, HELDOUT
from msafara.textutil import currency_numbers
from msafara.tests.conftest import Stub, TEXT, approve_all

TRUTH = {c["text"]: c["truth"] for c in CASES + HELDOUT}
CLEAN = {"hotel": "clean", "transport": "clean", "activity": "clean"}


def good_script(overrides=None):
    overrides = overrides or {}

    def script(system, user, n):
        if "Intake agent" in system:
            if "intake" in overrides:
                return overrides["intake"](user)
            t = TRUTH[user]
            return json.dumps({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in t.items()} | {"confidence": 0.9, "missing": []})
        if "availability-and-quote request" in system:
            if "outreach" in overrides:
                return overrides["outreach"](user)
            f = json.loads(user)
            return json.dumps(dict(subject="Quote request", body=f"Dear {f['vendor']} team,\nPlease confirm availability for {f['requirement']} and your rate {f['unit']} inclusive of all taxes. "
                                   f"Your recent quotes were around {f['currency']} {f['vendor_past_quote_anchor']:,.0f}.\nKind regards"))
        if "counter-offer message" in system:
            if "counter" in overrides:
                return overrides["counter"](user)
            f = json.loads(user)
            return json.dumps(dict(subject="Re: quote", body=f"Dear {f['vendor']} team,\nThanks for {f['currency']} {f['vendor_quote']:,.0f}. We propose {f['currency']} {f['counter_offer_amount']:,.0f} inclusive of all taxes, subject to our final confirmation.\nKind regards"))
        if "read a vendor's reply" in system:
            c = json.loads(user)
            pq = A.mock_parse_quote(c["reply"], c["quantity_requested"], c["our_counter_offer"], c["currency"])
            return pq.model_dump_json()
        if "grade a message" in system:
            return json.dumps(dict(tone=5, clarity=4, concision=5, issues=[]))
        raise AssertionError("unexpected prompt: " + system[:60])
    return script


def run_trip(g, stub, profile=CLEAN, tid="L1"):
    from msafara import llm
    llm.set_client(stub)
    graph.set_reply_provider(simulator.make_provider())
    graph.start_trip(g, TEXT, tid, profile)
    return graph.drive(g, tid, approve_all)["trip"]


def test_full_trip_on_llm_path_with_negotiation(g):
    stub = Stub(good_script())
    t = run_trip(g, stub, dict(CLEAN, hotel="negotiable"))
    assert t["status"] == "confirmed" and t["llm_backend"].startswith("llm:")
    assert {d["generated_by"] for d in t["drafts"] if d["kind"] != "confirm"} == {"llm"}
    assert t["prompt_versions"]["intake"] == "v2"
    hotel = next(s for s in t["services"] if s["service_id"] == "hotel")
    assert hotel["counters"]


def test_llm_counter_offering_25pct_off_is_rejected_and_template_used(g):
    def greedy(user):
        f = json.loads(user)
        return json.dumps(dict(subject="Re", body=f"Dear team, we offer {f['currency']} {f['vendor_quote'] * 0.75:,.0f} instead. Subject to confirmation."))
    t = run_trip(g, Stub(good_script({"counter": greedy})), dict(CLEAN, hotel="negotiable"), "L2")
    counters = [d for d in t["drafts"] if d["kind"] == "counter"]
    assert counters and all(d["generated_by"] == "template(fallback)" for d in counters)
    assert any(a["event"] == "llm_text_rejected_used_template" for a in t["audit"])
    for d in counters:
        assert min(currency_numbers(d["body"])) >= d["counter_rate"] - 1e-6 or d["counter_rate"] in currency_numbers(d["body"])


def test_llm_counter_that_changes_the_amount_is_rejected(g):
    def wrong(user):
        f = json.loads(user)
        return json.dumps(dict(subject="Re", body=f"Dear team, we propose {f['currency']} {f['counter_offer_amount'] - 1:,.0f}."))
    t = run_trip(g, Stub(good_script({"counter": wrong})), dict(CLEAN, hotel="negotiable"), "L3")
    assert all(d["generated_by"] == "template(fallback)" for d in t["drafts"] if d["kind"] == "counter")


def test_llm_outreach_with_invented_price_or_floor_is_rejected(g):
    def leaky(user):
        f = json.loads(user)
        v = [x for x in kb.vendors() if x["name"] == f["vendor"]][0]
        floor = kb.truth_anchor(v["vendor_id"])["floor"]
        return json.dumps(dict(subject="Hi", body=f"Dear team, our floor is {f['currency']} {floor:,.0f}; we pay INR 1 or $5."))
    t = run_trip(g, Stub(good_script({"outreach": leaky})), CLEAN, "L4")
    outs = [d for d in t["drafts"] if d["kind"] == "outreach"]
    assert outs and all(d["generated_by"] == "template(fallback)" for d in outs)
    assert not any("floor" in d["body"].lower() for d in outs)


def test_llm_outreach_invalid_json_falls_back_to_template(g):
    t = run_trip(g, Stub(good_script({"outreach": lambda u: "sorry I cannot"})), CLEAN, "L5")
    assert all(d["generated_by"] == "template(fallback)" for d in t["drafts"] if d["kind"] == "outreach")
    assert t["status"] == "confirmed"


def test_llm_intake_garbage_falls_back_to_rules_with_low_confidence_then_escalates(g):
    from msafara import llm
    llm.set_client(Stub(good_script({"intake": lambda u: "no json here"})))
    graph.set_reply_provider(simulator.make_provider())
    snap = graph.start_trip(g, TEXT, "L6", CLEAN)
    assert snap["gate"]["gate"] == "escalate_intake" and "confidence" in snap["gate"]["reason"]
    assert any(a["event"] == "llm_failed_fallback_to_rules" for a in snap["trip"]["audit"])


def test_llm_intake_hallucinated_fields_are_sanitised(g):
    def sloppy(user):
        return json.dumps(dict(city="Paris", check_in="2027-03-14", check_out="2027-03-10", pax=-2, hotel_star=9, confidence=0.99))
    from msafara import llm
    llm.set_client(Stub(good_script({"intake": sloppy})))
    b = A.run_intake(TEXT)
    assert b.city is None and b.check_out is None and b.pax is None and b.hotel_star is None
    assert set(b.missing) == {"city", "check_out", "pax", "hotel_star"} and b.confidence <= 0.4


def test_eval_harness_runs_on_llm_path_and_labels_mode():
    from msafara.evals import run
    from msafara import llm
    llm.set_client(Stub(good_script()))
    rec = run.evaluate(CASES[:6], label="stub", log=False)
    assert rec["mode"] == "llm" and rec["metrics"]["bound_violations"] == 0 and rec["judge"] == ["llm"]


def test_hosted_adapter_parses_response(monkeypatch):
    import io, json
    from msafara import llm
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("MSAFARA_LLM_KEY", "k")
    monkeypatch.setenv("MSAFARA_HOSTED_URL", "https://hosted.example/models/{model}:generate")
    monkeypatch.setenv("MSAFARA_HOSTED_MODEL", "hosted-model-1")
    llm.set_client(None); llm.force_mock(False)
    payload = {"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}]}
    seen = {}
    import urllib.request
    def fake(req, timeout=0):
        seen["url"] = req.full_url; seen["key"] = req.get_header("X-api-key")
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    c = llm.get_client()
    assert isinstance(c, llm.HostedClient)
    r = c.messages.create(model=llm.DEFAULT_MODEL.split("-")[0] + "-x", max_tokens=10, system="s", messages=[{"role": "user", "content": "u"}])
    assert r.content[0].text == '{"a": 1}'
    assert seen["url"] == "https://hosted.example/models/hosted-model-1:generate" and seen["key"] == "k"
    assert llm.backend_label() == "llm:hosted-model-1"
    llm.force_mock(False)


def test_hosted_backend_incomplete_config_stays_in_rules_mode(monkeypatch):
    from msafara import llm
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("MSAFARA_HOSTED_URL", raising=False)
    monkeypatch.delenv("MSAFARA_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("MSAFARA_LLM_KEY", "k")
    llm.set_client(None); llm.force_mock(False)
    assert llm.get_client() is None and llm.mock_mode()


def test_primary_api_adapter(monkeypatch):
    import io, json, urllib.request
    from msafara import llm
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_API_URL", "https://primary.example/v1/messages")
    monkeypatch.setenv("LLM_API_HEADERS", json.dumps({"api-version": "2023-06-01"}))
    monkeypatch.setenv("MSAFARA_MODEL", "model-a")
    llm.set_client(None); llm.force_mock(False)
    seen = {}
    def fake(req, timeout=0):
        seen["url"] = req.full_url; seen["body"] = json.loads(req.data)
        seen["key"] = req.get_header("X-api-key"); seen["ver"] = req.get_header("Api-version")
        return io.BytesIO(json.dumps({"content": [{"type": "text", "text": '{"c": 3}'}]}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    c = llm.get_client()
    assert isinstance(c, llm.ApiClient)
    r = c.messages.create(model=llm.DEFAULT_MODEL, max_tokens=7, system="s", messages=[{"role": "user", "content": "u"}])
    assert r.content[0].text == '{"c": 3}'
    assert seen["url"] == "https://primary.example/v1/messages" and seen["key"] == "secret" and seen["ver"] == "2023-06-01"
    assert seen["body"] == {"model": "model-a", "max_tokens": 7, "system": "s", "messages": [{"role": "user", "content": "u"}]}
    assert llm.backend_label() == "llm:model-a"


def test_primary_api_failure_raises_output_error_for_fallback(monkeypatch):
    import urllib.request
    from msafara import llm
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_API_URL", "https://primary.example/v1/messages")
    monkeypatch.setenv("MSAFARA_MODEL", "model-a")
    llm.set_client(None); llm.force_mock(False)
    def boom(req, timeout=0):
        raise OSError("down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    import pytest
    with pytest.raises(llm.LLMOutputError):
        llm.get_client().messages.create(model=llm.DEFAULT_MODEL, max_tokens=5, system="s", messages=[])


def test_local_adapter(monkeypatch):
    import io, json, urllib.request
    from msafara import llm
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("MSAFARA_LLM_PROVIDER", "local")
    llm.set_client(None); llm.force_mock(False)
    seen = {}
    def fake(req, timeout=0):
        seen["body"] = json.loads(req.data)
        return io.BytesIO(json.dumps({"message": {"content": '{"b": 2}'}}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    c = llm.get_client()
    assert isinstance(c, llm.LocalClient)
    r = c.messages.create(model=llm.DEFAULT_MODEL.split("-")[0] + "-x", max_tokens=5, system="s", messages=[{"role": "user", "content": "u"}])
    assert r.content[0].text == '{"b": 2}'
    assert seen["body"]["model"] == "local-model" and seen["body"]["messages"][0]["role"] == "system"
    assert llm.backend_label() == "llm:local/local-model"
