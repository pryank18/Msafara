"""
Graph wiring for the vendor coordination agent.

Design note on the LLM/plain-code split (this is the part worth reading
before editing anything below):
  - draft_request, draft_followup, draft_counter, parse_response need
    judgment over freeform text -> LLM nodes (llm_nodes.py).
  - check_timeout, check_response, resolve, escalate are deterministic
    checks over the state object -> plain Python, no LLM call. Cheaper,
    faster, and doesn't add non-determinism where none is needed.

The graph does NOT run continuously for days waiting on a vendor. Each
invocation does one pass and then either finishes (resolved/escalated) or
pauses (awaiting_response/negotiating) — see run_demo.py and the
scheduled-recheck note at the bottom of this file for how it's meant to be
re-invoked later.
"""
from datetime import datetime, timedelta
from typing import Optional

from langgraph.graph import StateGraph, END

from state import VendorRequestState, RequestStatus, Message
import llm_nodes

# --- LangGraph works on plain dicts by default; we wrap state in/out of
# --- the pydantic model at the edges of each node for type safety.

def load(state_dict: dict) -> VendorRequestState:
    return VendorRequestState.model_validate(state_dict)

def _dump(state: VendorRequestState) -> dict:
    return state.model_dump()

# ---- Nodes ----

def node_draft_request(state_dict: dict) -> dict:
    state = load(state_dict)
    text = llm_nodes.draft_request(state)
    state.message_history.append(Message(direction="outbound", timestamp=datetime.utcnow(), content=text))
    state.status = RequestStatus.SENT
    state.last_action_at = datetime.utcnow()
    import vendor_directory
    vendor_directory.upsert_vendor(state.vendor, category=state.need.category)
    return _dump(state)

def node_send(state_dict: dict) -> dict:
    state = load(state_dict)
    import dispatch
    import message_router
    subject = dispatch.subject_for(state.request_id, state.need.description)
    body = state.message_history[-1].content
    sent = message_router.send(state.request_id, state.vendor, subject, body)
    state.status = RequestStatus.AWAITING_RESPONSE
    if not sent:
        # dispatch not configured — draft is printed for manual sending,
        # status still moves forward so the rest of the loop (timeout
        # checks etc.) behaves the same either way
        pass
    return _dump(state)

def node_check_timeout(state_dict: dict) -> dict:
    state = load(state_dict)
    hours = state.hours_since_last_action()
    if hours < 24:
        state.status = RequestStatus.AWAITING_RESPONSE  # still waiting, no action
    elif state.follow_up_count < state.max_follow_ups:
        state.status = RequestStatus.AWAITING_RESPONSE  # will route to followup
    else:
        state.status = RequestStatus.ESCALATED
        state.escalation_reason = f"No response after {state.follow_up_count} follow-ups over {hours:.0f}h"
    return _dump(state)

def route_after_timeout_check(state_dict: dict) -> str:
    state = load(state_dict)
    hours = state.hours_since_last_action()
    if state.status == RequestStatus.ESCALATED:
        return "escalate"
    if hours >= 24 and state.follow_up_count < state.max_follow_ups:
        return "draft_followup"
    return "wait"  # exit graph, nothing to do yet

def node_draft_followup(state_dict: dict) -> dict:
    state = load(state_dict)
    text = llm_nodes.draft_followup(state)
    state.message_history.append(Message(direction="outbound", timestamp=datetime.utcnow(), content=text))
    state.follow_up_count += 1
    state.last_action_at = datetime.utcnow()
    import dispatch
    import message_router
    subject = "Re: " + dispatch.subject_for(state.request_id, state.need.description)
    message_router.send(state.request_id, state.vendor, subject, text)
    return _dump(state)
def node_parse_response(state_dict: dict, reply_text: Optional[str] = None) -> dict:
    state = load(state_dict)
    # reply_text is injected externally when a real reply has arrived
    # (see run_demo.py). In a live system this comes from an email/webhook
    # handler that calls the graph with the new inbound message attached.
    text = reply_text or state_dict.get("_incoming_reply", "")
    state.message_history.append(Message(direction="inbound", timestamp=datetime.utcnow(), content=text))
    result = llm_nodes.parse_response(state, text)
    state.latest_parsed_result = result
    state.last_action_at = datetime.utcnow()
    return _dump(state)

def node_evaluate(state_dict: dict) -> dict:
    state = load(state_dict)
    result = state.latest_parsed_result
    if result is None:
        state.status = RequestStatus.ESCALATED
        state.escalation_reason = "No parsed result to evaluate"
    elif result.line_items:
        # Multi-service reply: only auto-resolve if every line item is a
        # clear yes AND the combined cost is within budget. A reply where
        # every item is available but the total blows past budget_max
        # should not auto-confirm any more than a single-item over-budget
        # quote would.
        unclear = [li for li in result.line_items if li.available is not True]
        if unclear:
            names = ", ".join(li.description for li in unclear)
            state.status = RequestStatus.ESCALATED
            state.escalation_reason = f"Multi-item reply — needs review: {names}"
        else:
            priced_items = [li for li in result.line_items if li.rate is not None]
            total = sum(li.rate for li in priced_items) if priced_items else None
            over_budget = (
                total is not None
                and state.need.budget_max is not None
                and total > state.need.budget_max
            )
            if over_budget:
                state.status = RequestStatus.ESCALATED
                state.escalation_reason = (
                    f"Multi-item reply — all items available but combined total "
                    f"${total} exceeds budget (${state.need.budget_max}), needs a human decision"
                )
                # NOTE: multi-item negotiation isn't built — draft_counter only
                # handles single-rate quotes. This always escalates rather than
                # auto-negotiating a multi-item total.
            else:
                state.status = RequestStatus.CONFIRMED
    elif result.available is True:
        over_budget = (
            result.rate is not None
            and state.need.budget_max is not None
            and result.rate > state.need.budget_max
        )
        if over_budget and state.negotiation_round < state.max_negotiation_rounds:
            state.status = RequestStatus.NEGOTIATING
        elif over_budget:
            state.status = RequestStatus.ESCALATED
            state.escalation_reason = (
                f"Vendor quote ${result.rate} still above budget (${state.need.budget_max}) "
                f"after {state.negotiation_round} counter(s) — needs a human decision"
            )
        else:
            state.status = RequestStatus.CONFIRMED
    elif result.available is False and result.alternative_offer:
        # Ambiguous trade-off (partial availability, different dates) —
        # a human should decide, not the agent.
        state.status = RequestStatus.ESCALATED
        state.escalation_reason = f"Vendor offered alternative: {result.alternative_offer}"
    elif result.available is False:
        state.status = RequestStatus.DECLINED
    else:
        # available is None -> genuinely ambiguous reply
        state.status = RequestStatus.ESCALATED
        state.escalation_reason = "Vendor reply was ambiguous, could not parse a clear answer"
    return _dump(state)

def route_after_evaluate(state_dict: dict) -> str:
    state = load(state_dict)
    if state.status == RequestStatus.CONFIRMED:
        return "resolve"
    if state.status == RequestStatus.DECLINED:
        return "resolve"
    if state.status == RequestStatus.NEGOTIATING:
        return "negotiate"
    if state.status == RequestStatus.ESCALATED:
        return "escalate"
    return "resolve"
def node_draft_counter(state_dict: dict) -> dict:
    state = load(state_dict)
    import negotiation_rules
    result = state.latest_parsed_result
    target = negotiation_rules.counter_offer_rate(
        result.rate, state.need.budget_max, state.negotiation_round + 1
    )
    text = llm_nodes.draft_counter(state, target_rate=target, quoted_rate=result.rate)
    state.message_history.append(Message(direction="outbound", timestamp=datetime.utcnow(), content=text))
    state.negotiation_round += 1
    state.last_action_at = datetime.utcnow()
    import dispatch
    import message_router
    subject = "Re: " + dispatch.subject_for(state.request_id, state.need.description)
    message_router.send(state.request_id, state.vendor, subject, text)
    return _dump(state)

def node_resolve(state_dict: dict) -> dict:
    state = load(state_dict)
    # terminal — status already set by node_evaluate
    return _dump(state)

def node_escalate(state_dict: dict) -> dict:
    state = load(state_dict)
    state.status = RequestStatus.ESCALATED
    return _dump(state)

# ---- Graph assembly ----

def build_graph():
    g = StateGraph(dict)
    g.add_node("draft_request", node_draft_request)
    g.add_node("send", node_send)
    g.add_node("check_timeout", node_check_timeout)
    g.add_node("draft_followup", node_draft_followup)
    g.add_node("parse_response", node_parse_response)
    g.add_node("evaluate", node_evaluate)
    g.add_node("draft_counter", node_draft_counter)
    g.add_node("resolve", node_resolve)
    g.add_node("escalate", node_escalate)

    g.set_entry_point("draft_request")
    g.add_edge("draft_request", "send")
    g.add_edge("send", "check_timeout")

    g.add_conditional_edges("check_timeout", route_after_timeout_check, {
        "draft_followup": "draft_followup",
        "escalate": "escalate",
        "wait": END,
    })
    g.add_edge("draft_followup", END)  # sent; wait for next scheduled check

    # parse_response is entered externally once a reply arrives (see
    # run_demo.py) rather than from check_timeout, since a reply can land
    # at any time, not just when we happen to be polling.
    g.add_edge("parse_response", "evaluate")
    g.add_conditional_edges("evaluate", route_after_evaluate, {
        "resolve": "resolve",
        "negotiate": "draft_counter",
        "escalate": "escalate",
    })
    g.add_edge("draft_counter", END)  # sent; wait for vendor's response to the counter
    g.add_edge("resolve", END)
    g.add_edge("escalate", END)

    return g.compile()

# ---- Re-entry point for when a vendor reply arrives ----

def handle_incoming_reply(compiled_graph, state_dict: dict, reply_text: str) -> dict:
    """Call this from your email/webhook handler when a vendor reply comes
    in for a request that's currently AWAITING_RESPONSE or NEGOTIATING.
    Runs parse_response -> evaluate -> (resolve|negotiate|escalate) as one pass."""
    state_dict = node_parse_response(state_dict, reply_text=reply_text)
    state_dict = node_evaluate(state_dict)
    route = route_after_evaluate(state_dict)
    if route == "resolve":
        state_dict = node_resolve(state_dict)
    elif route == "negotiate":
        state_dict = node_draft_counter(state_dict)
    else:
        state_dict = node_escalate(state_dict)
    return state_dict

# ---- Re-entry point for scheduled timeout checks ----

def handle_scheduled_check(compiled_graph, state_dict: dict) -> dict:
    """Call this periodically (e.g. every few hours via cron) for every
    request currently AWAITING_RESPONSE or NEGOTIATING, to trigger
    follow-ups or escalation on silence. A negotiating request that never
    hears back on its counter-offer needs exactly the same silence
    handling as a fresh request — it isn't a separate case."""
    state_dict = node_check_timeout(state_dict)
    route = route_after_timeout_check(state_dict)
    if route == "draft_followup":
        state_dict = node_draft_followup(state_dict)
    elif route == "escalate":
        state_dict = node_escalate(state_dict)
    return state_dict

TIMEOUT_CHECKABLE_STATUSES = [RequestStatus.AWAITING_RESPONSE.value, RequestStatus.NEGOTIATING.value]
# statuses that can legitimately go silent and need the timeout sweep —
# used by callers (cli.py, app.py) instead of querying AWAITING_RESPONSE
# alone, so a stalled negotiation doesn't sit forever unflagged
