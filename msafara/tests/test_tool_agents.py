"""Tool-calling agents, driven by a scripted model (no network).

`ToolStub` answers structured calls like the old stub and tool-calling turns through a policy function.
The "good" policy shows the plumbing works end to end. The hostile policies try to break the limits
(counter too low, accept above the ceiling, accept a reply that must go to a human, pick a blacklisted
vendor) and the tests check that the tools refuse and nothing unsafe reaches a vendor.
This proves the guardrails, not the quality of any real model.
"""
import json
from types import SimpleNamespace

import pytest

from msafara import agents as A, graph, kb, llm, simulator
from msafara.evals import metrics as M
from msafara.evals.dataset import ALL, CASES
from msafara.llm import ChatResult, ToolCall
from msafara.tests.conftest import TEXT, approve_all
from msafara.tests.test_llm_path import good_script

CLEAN = {"hotel": "clean", "transport": "clean", "activity": "clean"}


def _last_results(history):
    for h in reversed(history):
        if h["role"] == "tool":
            return {r["name"]: json.loads(r["content"]) for r in h["results"]}
    return {}


def _n_turns(history):
    return sum(1 for h in history if h["role"] == "assistant")


def sourcing_good(history, ctx):
    res = _last_results(history)
    if "search_vendors" not in res:
        return [ToolCall(id="s1", name="search_vendors", args={"query": "group stay"})]
    ids = [v["vendor_id"] for v in res["search_vendors"]["eligible"]][:5]
    return [ToolCall(id="s2", name="select_vendors", args={"vendor_ids": ids, "reason": "best fit"})]


def negotiate_good(history, ctx):
    brief = json.loads(history[0]["content"])
    last = brief.get("our_last_counter")
    if last and last["they_accepted"]:
        return [ToolCall(id="n0", name="accept_quote", args={"vendor_id": last["vendor_id"], "reason": "agreed to counter"})]
    quoted = [v for v in brief["vendors"] if v["latest_status"] == "quote" and v["latest_rate"]]
    res = _last_results(history)
    if not quoted:
        return [ToolCall(id="n1", name="escalate_to_human", args={"reason": "no usable quote", "code": "other"})]
    best = min(quoted, key=lambda v: v["latest_rate"])
    if "get_price_limits" not in res:
        return [ToolCall(id="n2", name="get_price_limits", args={"vendor_id": best["vendor_id"]})]
    lim = res["get_price_limits"]
    if lim.get("ok") is False:
        return [ToolCall(id="n3", name="escalate_to_human", args={"reason": lim["error"], "code": "other"})]
    if lim["auto_accept_ceiling"] and lim["current_quote"] <= lim["past_quote_anchor"]:
        return [ToolCall(id="n4", name="accept_quote", args={"vendor_id": best["vendor_id"], "reason": "at/below anchor"})]
    if lim["counter_possible"]:
        cur, q, amt = lim["currency"], lim["current_quote"], lim["min_counter"]
        msg = (f"Dear team,\nThank you for your quote of {cur} {q:,.0f}. We would like to propose {cur} {amt:,.0f}, inclusive "
               f"of all taxes, subject to our final confirmation.\nKind regards")
        return [ToolCall(id="n5", name="propose_counter", args={"vendor_id": best["vendor_id"], "amount": amt, "message": msg,
                                                                 "reason": "above anchor"})]
    if lim["current_quote"] <= (lim["auto_accept_ceiling"] or 0):
        return [ToolCall(id="n6", name="accept_quote", args={"vendor_id": best["vendor_id"], "reason": "within tolerance"})]
    return [ToolCall(id="n7", name="escalate_to_human", args={"reason": "above ceiling, no rounds", "code": "over_limits"})]


class ToolStub:
    def __init__(self, sourcing=sourcing_good, negotiate=negotiate_good, script=None):
        self.script = script or good_script()
        self.pol = {"Sourcing agent": sourcing, "Negotiation agent": negotiate}
        self.n = 0
        self.tool_calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, model, max_tokens, system, messages):
        self.n += 1
        return SimpleNamespace(content=[SimpleNamespace(text=self.script(system, messages[-1]["content"], self.n))],
                               usage=dict(input=10, output=5))

    def chat(self, system, history, tools):
        for key, fn in self.pol.items():
            if key in system:
                calls = fn(history, self)
                self.tool_calls += [c.name for c in calls]
                return ChatResult(tool_calls=calls, usage=dict(input=20, output=8))
        raise AssertionError("unexpected agent: " + system[:60])


def run(g, stub, profile=CLEAN, tid="T1", text=TEXT):
    llm.set_client(stub)
    graph.set_reply_provider(simulator.make_provider())
    graph.start_trip(g, text, tid, profile)
    return graph.drive(g, tid, approve_all)["trip"]


def _svc(t, sid="hotel"):
    return next(s for s in t["services"] if s["service_id"] == sid)


# ------------------------------------------------------------------ happy path
def test_agents_drive_a_negotiation_through_tools(g):
    stub = ToolStub()
    t = run(g, stub, dict(CLEAN, hotel="negotiable"))
    h = _svc(t)
    assert t["status"] == "confirmed"
    assert {s["decided_by"] for s in t["services"]} == {"llm"}
    assert h["counters"] and h["counters"][0]["discount_pct"] <= 10
    assert "search_vendors" in stub.tool_calls and "propose_counter" in stub.tool_calls and "accept_quote" in stub.tool_calls
    counter_draft = next(d for d in t["drafts"] if d["kind"] == "counter")
    assert counter_draft["generated_by"] == "llm"          # the agent's own message passed the text guardrail
    assert any(a["event"] == "tool_trace" for a in t["audit"])
    assert llm.METER.calls > 0 and llm.METER.input > 0


def test_counter_draft_waits_for_human_approval(g):
    llm.set_client(ToolStub())
    graph.set_reply_provider(simulator.make_provider())
    snap = graph.start_trip(g, TEXT, "T2", dict(CLEAN, hotel="negotiable"))
    snap = graph.resume_trip(g, "T2", {"action": "approve"})       # outreach approved -> replies -> agent decides
    assert snap["gate"]["gate"] == "approve_counter"
    sent = [a for a in snap["trip"]["audit"] if a["event"] == "message_sent" and a["detail"]["kind"] == "counter"]
    assert sent == []                                               # proposed by a tool, not sent


# ------------------------------------------------------------------ hostile model behaviour
def negotiate_lowball(history, ctx):
    """Always offers 30% below the quote; if refused, tries once more in range."""
    brief = json.loads(history[0]["content"])
    quoted = [v for v in brief["vendors"] if v["latest_status"] == "quote" and v["latest_rate"]]
    if not quoted:
        return [ToolCall(id="e", name="escalate_to_human", args={"reason": "none", "code": "other"})]
    best = min(quoted, key=lambda v: v["latest_rate"])
    res = _last_results(history)
    if "propose_counter" in res and res["propose_counter"].get("ok") is False:
        return [ToolCall(id="l", name="get_price_limits", args={"vendor_id": best["vendor_id"]})]
    if "get_price_limits" in res:
        lim = res["get_price_limits"]
        return [ToolCall(id="c", name="propose_counter", args={"vendor_id": best["vendor_id"], "amount": lim["min_counter"],
                                                                "reason": "corrected", "message": ""})]
    if brief.get("our_last_counter"):
        return [ToolCall(id="a", name="accept_quote", args={"vendor_id": brief["our_last_counter"]["vendor_id"], "reason": "x"})]
    return [ToolCall(id="b", name="propose_counter", args={"vendor_id": best["vendor_id"], "amount": best["latest_rate"] * 0.7,
                                                            "reason": "squeeze", "message": "We offer much less."})]


def test_counter_outside_limits_is_refused_and_model_corrects(g):
    t = run(g, ToolStub(negotiate=negotiate_lowball), dict(CLEAN, hotel="negotiable"), "T3")
    h = _svc(t)
    traces = [a["detail"] for a in t["audit"] if a["event"] == "tool_trace" and a["node"] == "decide"
              and a["detail"]["service"] == "hotel"]
    assert traces[0]["refused"] >= 1
    assert any("outside allowed range" in (c["error"] or "") for c in traces[0]["calls"])
    assert h["counters"] and all(c["discount_pct"] <= 10 for c in h["counters"])
    assert M.bounds(t)["violations"] == []


def negotiate_always_accept(history, ctx):
    brief = json.loads(history[0]["content"])
    quoted = [v for v in brief["vendors"] if v["latest_rate"]]
    vid = (max(quoted, key=lambda v: v["latest_rate"]) if quoted else brief["vendors"][0])["vendor_id"]
    return [ToolCall(id=f"a{_n_turns(history)}", name="accept_quote", args={"vendor_id": vid, "reason": "looks fine"})]


def test_accept_above_ceiling_is_refused_and_rules_fallback_escalates(g):
    t = run(g, ToolStub(negotiate=negotiate_always_accept), dict(CLEAN, hotel="firm_high"), "T4")
    h = _svc(t)
    assert h["status"] == "dropped"                     # escalated, then the test operator dropped it
    assert any(a["event"] == "llm_failed_fallback_to_rules" for a in t["audit"])
    assert M.accept_ceiling_violations(t) == []


@pytest.mark.parametrize("profile", ["ambiguous_all", "partial_all", "tax_unclear_all", "injection_payment", "two_rates"])
def test_model_cannot_accept_what_policy_sends_to_a_human(g, profile):
    t = run(g, ToolStub(negotiate=negotiate_always_accept), dict(CLEAN, activity=profile), f"T5-{profile}")
    a = _svc(t, "activity")
    assert a["status"] == "dropped" and a["agreed_rate"] is None
    assert any(x["event"] == "escalated_to_human" and x["detail"]["service"] == "activity" for x in t["audit"])
    errs = [c["error"] for x in t["audit"] if x["event"] == "tool_trace" and x["node"] == "decide"
            and x["detail"]["service"] == "activity"
            for c in x["detail"]["calls"] if c["error"]]
    assert errs and all("policy requires a human" in e for e in errs)


def test_injected_instructions_are_flagged_and_never_obeyed(g):
    t = run(g, ToolStub(negotiate=negotiate_always_accept), dict(CLEAN, hotel="injection_high"), "T6")
    h = _svc(t)
    assert any(q["flags"] == ["suspicious_instructions"] for q in h["quote_history"])
    assert h["status"] == "dropped" and not any(d["kind"] == "confirm" and d["service_id"] == "hotel" for d in t["drafts"])


def sourcing_blacklisted(history, ctx):
    res = _last_results(history)
    if "search_vendors" not in res:
        return [ToolCall(id="s1", name="search_vendors", args={"query": "x"})]
    if "select_vendors" in res and res["select_vendors"].get("ok") is False and _n_turns(history) < 3:
        ids = [v["vendor_id"] for v in res.get("search_vendors", {}).get("eligible", [])] or ["V001"]
    bl = next(v["vendor_id"] for v in kb.vendors() if v["blacklisted"])
    return [ToolCall(id=f"s{_n_turns(history)}", name="select_vendors", args={"vendor_ids": [bl, "V001", "V002"], "reason": "x"})]


def test_sourcing_agent_cannot_pick_ineligible_vendors(g):
    t = run(g, ToolStub(sourcing=sourcing_blacklisted), CLEAN, "T7")
    bl = {v["vendor_id"] for v in kb.vendors() if v["blacklisted"]}
    for s in t["services"]:
        assert not {c["vendor_id"] for c in s["candidates"]} & bl
    assert any(a["event"] == "llm_failed_fallback_to_rules" and a["node"] == "match" for a in t["audit"])


def test_agent_message_with_floor_or_invented_price_falls_back_to_template(g):
    def leaky(history, ctx):
        calls = negotiate_good(history, ctx)
        for c in calls:
            if c.name == "propose_counter":
                c.args["message"] = f"Dear team, we propose INR {c.args['amount']:,.0f}. Other hotels charge INR 1,111. Kind regards"
        return calls
    t = run(g, ToolStub(negotiate=leaky), dict(CLEAN, hotel="negotiable"), "T8")
    d = next(d for d in t["drafts"] if d["kind"] == "counter")
    assert d["generated_by"] == "template(fallback)"
    assert any(a["event"] == "agent_text_rejected_used_template" for a in t["audit"])
    assert M.grounding(t)["ungrounded"] == []


def test_model_that_never_decides_falls_back_to_rules(g):
    def chatty(history, ctx):
        return [ToolCall(id=f"c{_n_turns(history)}", name="get_price_history",
                         args={"vendor_id": json.loads(history[0]["content"])["vendors"][0]["vendor_id"]})]
    t = run(g, ToolStub(negotiate=chatty), dict(CLEAN, hotel="negotiable"), "T9")
    assert t["status"] == "confirmed" and {s["decided_by"] for s in t["services"]} == {"rules(fallback)"}


# ------------------------------------------------------------------ intake guardrail
def test_intake_fields_not_in_text_are_cleared():
    def invent(user):
        return json.dumps(dict(city="Jaipur", check_in="2027-03-14", check_out="2027-03-17", pax=40, hotel_star=5,
                               budget_total=999999, currency="INR", confidence=0.95))
    llm.set_client(ToolStub(script=good_script({"intake": invent})))
    b = A.run_intake(TEXT)                 # text says 12 people, 4-star, 3.2 lakh
    assert b.pax is None and b.hotel_star is None and b.budget_total is None
    assert set(b.missing) == {"pax", "hotel_star"} and b.confidence <= 0.4


def test_intake_grounding_accepts_messy_but_real_values():
    b = A.TripBrief(city="Jaipur", check_in="2027-12-18", check_out="2027-12-21", pax=7, hotel_star=3, budget_total=90000)
    assert A.ground_brief("jaipur trip for the family: 3 adults + 4 kids, 3 star, 18 Dec 2027 for 3 nights, around Rs 90,000", b) == []
    b = A.TripBrief(city="Jaipur", check_in="2027-02-09", check_out="2027-02-12", pax=12, hotel_star=4, budget_total=300000)
    assert A.ground_brief("pls quote Jaipr, 4 star, 12 ppl, 9-12 Feb 2027, budget INR 3 lakh", b) == []


# ------------------------------------------------------------------ backend adapters (request/response shapes)
def test_messages_api_adapter_round_trips_tool_use(monkeypatch):
    sent = {}

    def fake_post(url, body, headers, timeout, attempts=5):
        sent["body"] = body
        return {"content": [{"type": "text", "text": "checking"},
                            {"type": "tool_use", "id": "tu_1", "name": "get_price_limits", "input": {"vendor_id": "V001"}}],
                "usage": {"input_tokens": 120, "output_tokens": 30}}
    monkeypatch.setattr(llm, "_post_json", fake_post)
    monkeypatch.setenv("MSAFARA_MODEL", "m-small")
    c = SimpleNamespace(key="k", url="https://x", extra_headers={})
    hist = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [ToolCall(id="tu_0", name="check_vendor_status", args={"vendor_id": "V001"})], "raw": None},
            {"role": "tool", "results": [{"id": "tu_0", "name": "check_vendor_status", "content": "{}"}]}]
    from msafara.tools import NEGOTIATION_TOOLS
    r = llm._messages_api_chat(c, "sys", hist, NEGOTIATION_TOOLS, 256)
    assert r.tool_calls[0].name == "get_price_limits" and r.tool_calls[0].args == {"vendor_id": "V001"}
    assert r.usage == {"input": 120, "output": 30}
    msgs = sent["body"]["messages"]
    assert msgs[1]["content"][0]["type"] == "tool_use" and msgs[2]["content"][0]["type"] == "tool_result"
    assert {t["name"] for t in sent["body"]["tools"]} >= {"propose_counter", "accept_quote", "escalate_to_human"}


def test_tool_loop_refuses_unknown_tools_and_requires_a_decision(monkeypatch):
    class Odd:
        messages = None
        def chat(self, system, history, tools):
            return ChatResult(tool_calls=[ToolCall(id="x", name="send_email", args={})])
    llm.set_client(Odd())
    with pytest.raises(llm.LLMOutputError):
        llm.run_tool_loop("negotiate", "{}", [], lambda n, a: ({"ok": True}, True), max_steps=2, system="s")


# ------------------------------------------------------------------ dataset
def test_extended_dataset_shape():
    assert len(ALL) == 100 and ALL[:25] == CASES
    profs = {p for c in ALL for p in c["profile"].values()}
    assert set(simulator.ADVERSARIAL) <= profs
    assert sum(c["incomplete"] for c in ALL) >= 10
    for c in ALL:                               # every complete truth is internally consistent
        t = c["truth"]
        if t["check_in"] and t["check_out"]:
            assert t["nights"] == (t["check_out"] - t["check_in"]).days > 0


def test_placeholder_in_outbound_text_is_rejected():
    assert "unfilled placeholder" in A.validate_text("Dear team,\nKind regards,\n[Name]\n[Company]", set())
    assert A.validate_text("Dear team,\nKind regards,\nMsafara Operations", set()) == []


def test_rules_only_mode_keeps_the_model_for_the_judge(monkeypatch):
    stub = ToolStub()
    llm.set_client(stub)
    monkeypatch.setenv("MSAFARA_RULES_ONLY", "1")
    assert llm.mock_mode() and llm.get_client(judge=True) is stub
    monkeypatch.setenv("MSAFARA_JUDGE", "llm")
    _, how = M.judge("Dear team, please confirm. Kind regards", "outreach")
    assert how == "llm"
