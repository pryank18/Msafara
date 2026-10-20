"""Msafara trip graph (LangGraph).

  intake -> [escalate_intake] -> match -> outreach -> GATE(approve outreach) -> send -> collect(+parse)
         -> decide -> {escalate | counter -> GATE(approve counter) -> send ...| GATE(approve final)} -> finalize

* One shared state: TripState (brief, services/vendors, quotes, negotiation round, status, audit log).
* Human gates use LangGraph `interrupt()`; the operator resumes with Command(resume=...):
  approve / edit / reject. Nodes before an interrupt are side-effect free because LangGraph
  re-runs an interrupted node from its top on resume.
* Every outbound message leaves through agents.dispatch_draft, which refuses unapproved drafts.
"""
import os
import sqlite3
import threading
import uuid
from typing import Callable, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import agents as A
from . import kb, llm
from .agents import BoundViolation
from .models import CounterRecord, Escalation, OutreachDraft, TripBrief, TripState
from .textutil import fmt_money

_LOCK = threading.RLock()
_reply_provider: Optional[Callable] = None


class GState(TypedDict):
    trip: dict


def set_reply_provider(fn: Optional[Callable]):
    """fn(trip, svc, draft, vendor_dict) -> reply text or None. Used by demo/evals (simulated vendors)."""
    global _reply_provider
    _reply_provider = fn


def _load(s: GState) -> TripState:
    return TripState.model_validate(s["trip"])


def _out(t: TripState) -> dict:
    return {"trip": t.model_dump(mode="json")}


def _active(t: TripState):
    return [s for s in t.services if s.status != "dropped"]


def _vname(vid: str) -> str:
    return kb.vendor(vid)["name"]


def _refresh_status(t: TripState):
    if t.status in ("confirmed", "rejected", "failed"):
        return
    act = _active(t)
    if any(s.status == "escalated" for s in act):
        t.status = "escalated"
    elif any(s.decision == "counter" for s in act):
        t.status = "negotiating"
    elif act and all(s.status == "accepted" for s in act):
        t.status = "awaiting_final"
    t.negotiation_round = max([s.negotiation_round for s in t.services] or [0])


# ------------------------------------------------------------------ nodes
def intake(s):
    t = _load(s)
    t.status = "intake"
    t.policy = A.get_policy()
    t.llm_backend = llm.backend_label()
    t.prompt_versions = llm.active_versions()
    b = A.run_intake(t.request_text, t)
    t.brief = b
    t.log("agent:intake", "intake", "brief_extracted", brief=b.model_dump(mode="json"),
          reasoning=f"Extracted {5 - len(b.missing)}/5 required fields; confidence {b.confidence:.2f}.")
    if b.missing or b.confidence < t.policy.confidence_threshold:
        t.status = "escalated"
        t.log("agent:intake", "intake", "escalation_requested", reason=f"missing/low-confidence: {b.missing}")
    return _out(t)


def route_intake(s):
    t = _load(s)
    return "escalate_intake" if (t.brief.missing or t.brief.confidence < t.policy.confidence_threshold) else "match"


def escalate_intake(s):
    t = _load(s)
    b = t.brief
    reason = (f"intake incomplete - missing {', '.join(b.missing)}" if b.missing
              else f"intake confidence {b.confidence:.2f} below threshold")
    d = interrupt({"gate": "escalate_intake", "trip_id": t.trip_id, "reason": reason, "scope": "trip",
                   "brief": b.model_dump(mode="json"), "request_text": t.request_text,
                   "options": ["edit (supply the missing fields)", "reject"]})
    if d.get("action") == "reject":
        t.status = "rejected"
        t.log("human", "escalate_intake", "rejected_at_intake", note=d.get("note", ""))
        return _out(t)
    fields = {k: v for k, v in (d.get("brief") or {}).items() if v not in (None, "")}
    merged = TripBrief.model_validate({**b.model_dump(mode="json"), **fields})
    merged.confidence = 0.9
    merged = A.finalize_brief(merged)
    t.brief = merged
    t.log("human", "escalate_intake", "brief_edited", fields=fields, still_missing=merged.missing)
    return _out(t)


def route_escalate_intake(s):
    t = _load(s)
    if t.status == "rejected":
        return END
    return "escalate_intake" if (t.brief.missing or t.brief.confidence < t.policy.confidence_threshold) else "match"


def match(s):
    t = _load(s)
    t.status = "matching"
    t.services = A.build_services(t.brief)
    for svc in t.services:
        cands, excluded, by = A.source_vendors(t, svc)
        svc.candidates = cands
        svc.anchors = {c.vendor_id: A.anchor_for(c.vendor_id) for c in cands}
        t.log("agent:matching", "match", "vendors_matched", service=svc.service_id,
              candidates=[dict(vendor_id=c.vendor_id, name=c.name, score=c.score, why=c.why) for c in cands],
              excluded=excluded, chosen_by=by,
              reasoning=f"{len(cands)} candidate(s) for {svc.service_id} via {'sourcing agent (search_vendors tool)' if by == 'llm' else 'RAG filter + rank'} "
                        f"({len(excluded)} excluded: {', '.join(e['reason'] for e in excluded) or 'none'}).")
        if not cands:
            svc.status, svc.decision = "escalated", "escalate"
            svc.escalation = Escalation(reason=f"no eligible {svc.category} vendors found", code="no_vendors",
                                        options=["drop_service"])
            svc.reasoning = svc.escalation.reason
    return _out(t)


def outreach(s):
    t = _load(s)
    n = 0
    for svc in t.services:
        if svc.status != "planned":
            continue
        for c in svc.candidates:
            t.drafts.append(A.draft_outreach(t, svc, c))
            n += 1
        svc.reasoning = f"Drafted {len(svc.candidates)} availability/quote request(s), grounded in each vendor's past quotes."
    if n:
        t.status = "awaiting_approval"
        t.gate = {"gate": "approve_outreach"}
        t.log("agent:outreach", "outreach", "gate_requested", gate="approve_outreach", drafts=n)
    return _out(t)


def _draft_view(t, d):
    svc = t.svc(d.service_id)
    return dict(draft_id=d.draft_id, vendor_id=d.vendor_id, vendor=_vname(d.vendor_id), service=d.service_id,
                kind=d.kind, subject=d.subject, body=d.body, round=d.round, counter_rate=d.counter_rate,
                generated_by=d.generated_by, grounding=d.grounding)


def _apply_draft_gate(t: TripState, d: dict, kinds: tuple, node: str):
    pend = [x for x in t.drafts if x.status == "draft" and x.kind in kinds]
    action = d.get("action", "reject")
    by = d.get("by", "operator")
    rejected_ids = set(d.get("reject_ids") or [])
    if action == "reject":
        rejected_ids = {x.draft_id for x in pend}
    if action == "edit":
        for did, e in (d.get("edits") or {}).items():
            x = next((p for p in pend if p.draft_id == did), None)
            if not x:
                continue
            body, subj = e.get("body", x.body), e.get("subject", x.subject)
            probs = A.validate_edit(x, t.svc(x.service_id), t.policy, body)
            if probs:
                t.log("human", node, "edit_refused", draft_id=did, problems=probs)
                rejected_ids.add(did)          # never fall back to sending the unedited text
                continue
            x.body, x.subject, x.edited = body, subj, True
            t.log("human", node, "draft_edited", draft_id=did, vendor_id=x.vendor_id)
    appr, rej = [], []
    for x in pend:
        if x.draft_id in rejected_ids:
            x.status = "rejected"; rej.append(x.draft_id)
        else:
            x.status = "approved"; appr.append(x.draft_id)
    t.log("human", node, f"gate_{action}", approved=appr, rejected=rej, by=by, note=d.get("note", ""))
    t.gate = None
    return pend, rej


def gate_outreach(s):
    t = _load(s)
    pend = [x for x in t.drafts if x.status == "draft" and x.kind == "outreach"]
    if not pend:
        return _out(t)
    d = interrupt({"gate": "approve_outreach", "trip_id": t.trip_id, "scope": "trip",
                   "reason": "No message is sent to a vendor without your approval.",
                   "drafts": [_draft_view(t, x) for x in pend],
                   "options": ["approve", "edit", "reject"]})
    pend, rej = _apply_draft_gate(t, d, ("outreach",), "gate_outreach")
    for svc in t.services:
        mine = [x for x in pend if x.service_id == svc.service_id]
        if mine and all(x.draft_id in rej for x in mine):
            svc.status, svc.decision, svc.reasoning = "dropped", None, "Operator rejected all outreach for this service."
    if not _active(t):
        t.status = "rejected"
        t.log("human", "gate_outreach", "trip_rejected", reason="all outreach rejected")
    return _out(t)


def send(s):
    t = _load(s)
    sent = []
    for x in t.drafts:
        if x.status != "approved" or x.kind == "confirm":
            continue
        A.dispatch_draft(t, x, approved_by="operator")
        svc = t.svc(x.service_id)
        if x.kind == "outreach":
            svc.status = "requested"
        else:
            svc.negotiation_round += 1
            svc.status = "negotiating"
        sent.append(x.draft_id)
    t.status = "negotiating" if any(t.svc(i.service_id).negotiation_round for i in t.drafts if i.draft_id in sent) else "waiting_vendors"
    t.negotiation_round = max([x.negotiation_round for x in t.services] or [0])
    return _out(t)


def _pending_replies(t):
    return [x for x in t.drafts if x.status == "sent" and x.kind in ("outreach", "counter") and not x.replied]


def route_after_send(s):
    return "collect" if _pending_replies(_load(s)) else "route"


def collect(s):
    t = _load(s)
    pend = _pending_replies(t)
    replies: dict = {}
    provider = _reply_provider
    if provider is None and t.simulate_profile is not None:
        from .simulator import make_provider
        provider = make_provider()                      # per-trip simulated vendors (demo / evals)
    if provider is not None:
        for x in pend:
            replies[x.draft_id] = provider(t, t.svc(x.service_id), x, kb.vendor(x.vendor_id))
    else:
        d = interrupt({"gate": "await_replies", "trip_id": t.trip_id, "scope": "trip",
                       "reason": "Paste each vendor's reply (leave blank = no response).",
                       "pending": [dict(draft_id=x.draft_id, vendor_id=x.vendor_id, vendor=_vname(x.vendor_id),
                                        service=x.service_id, kind=x.kind) for x in pend]})
        replies = d.get("replies", {}) or {}
    for x in pend:
        svc = t.svc(x.service_id)
        text = replies.get(x.draft_id)
        q = A.parse_quote(t, svc, x.vendor_id, text, svc.negotiation_round, x.counter_rate if x.kind == "counter" else None)
        x.replied = True
        svc.quotes[x.vendor_id] = q
        svc.quote_history.append(q)
        t.log("agent:parse", "collect", "reply_parsed", vendor_id=x.vendor_id, service=svc.service_id, status=q.status,
              unit_rate=q.unit_rate, confidence=q.confidence, accepts_counter=q.accepts_counter, summary=q.summary,
              reply_excerpt=(text or "")[:200])
    for svc in t.services:
        if svc.status in ("requested", "negotiating") and any(q.round == svc.negotiation_round for q in svc.quotes.values()):
            svc.status = "quoted"
    return _out(t)


def decide(s):
    t = _load(s)
    for svc in t.services:
        if svc.status in ("dropped", "accepted", "confirmed", "planned") or (svc.status == "escalated" and svc.escalation and svc.escalation.code == "no_vendors"):
            continue
        A.decide_service_llm(t, svc)
        t.log("agent:negotiation", "decide", f"decision_{svc.decision}", service=svc.service_id, reasoning=svc.reasoning,
              decided_by=svc.decided_by,
              confidence=svc.confidence, label=A.conf_label(svc.confidence),
              escalation=svc.escalation.model_dump() if svc.escalation else None)
        if svc.decision == "escalate":
            t.log("agent:escalation", "decide", "escalated_to_human", service=svc.service_id, reason=svc.escalation.reason)
    _refresh_status(t)
    return _out(t)


def _resolved_valid_quotes(svc):
    out = {}
    for q in svc.quote_history:
        if q.status == "quote" and q.unit_rate:
            out[q.vendor_id] = q
    return out


def escalate(s):
    t = _load(s)
    items = []
    for svc in t.services:
        if svc.status == "escalated" and svc.escalation:
            vq = _resolved_valid_quotes(svc)
            items.append(dict(service_id=svc.service_id, category=svc.category, reason=svc.escalation.reason,
                              code=svc.escalation.code, options=svc.escalation.options, suggestion=svc.escalation.suggestion,
                              valid_quotes=[dict(vendor_id=v, vendor=_vname(v), unit_rate=q.unit_rate, currency=q.currency,
                                                 floor_allows_counter=bool(svc.anchors[v].floor_rate and q.unit_rate > svc.anchors[v].floor_rate))
                                            for v, q in vq.items()],
                              replies=[dict(vendor=_vname(q.vendor_id), status=q.status, summary=q.summary, excerpt=q.raw_text[:160])
                                       for q in svc.quotes.values()]))
    d = interrupt({"gate": "escalation", "trip_id": t.trip_id, "scope": "service",
                   "reason": "; ".join(f"{i['service_id']}: {i['reason']}" for i in items), "items": items,
                   "options": ["accept (pick a vendor)", "counter (within bounds)", "drop"]})
    res = d.get("resolutions", {}) or {}
    for svc in t.services:
        if not (svc.status == "escalated" and svc.escalation):
            continue
        r = res.get(svc.service_id) or {"action": "drop"}
        act = r.get("action")
        why = svc.escalation.reason
        if act == "accept":
            q = A.latest_valid_quote(svc, r.get("vendor_id"))
            if q:
                svc.selected_vendor_id, svc.agreed_rate = q.vendor_id, q.unit_rate
                svc.status, svc.decision, svc.escalation = "accepted", "accept", None
                svc.reasoning = f"Operator accepted {_vname(q.vendor_id)} at {fmt_money(q.unit_rate, q.currency)} after escalation ({why})."
                t.log("human", "escalate", "resolved_accept", service=svc.service_id, vendor_id=q.vendor_id, rate=q.unit_rate)
                continue
        if act == "counter":
            try:
                A.validate_operator_counter(svc, r.get("vendor_id"), float(r.get("rate")), t.policy)
                svc.selected_vendor_id, svc.operator_counter_rate = r["vendor_id"], float(r["rate"])
                svc.status, svc.decision, svc.escalation = "negotiating", "counter", None
                t.log("human", "escalate", "resolved_counter", service=svc.service_id, vendor_id=r["vendor_id"], rate=r["rate"])
                continue
            except (BoundViolation, TypeError, ValueError) as e:
                t.log("human", "escalate", "operator_counter_refused", service=svc.service_id, error=str(e))
                continue  # stays escalated -> asked again
        svc.status, svc.decision, svc.escalation = "dropped", None, None
        svc.reasoning = f"Operator dropped this service ({why})."
        t.log("human", "escalate", "resolved_drop", service=svc.service_id)
    if not _active(t):
        t.status = "rejected"
    _refresh_status(t)
    return _out(t)


def counter(s):
    t = _load(s)
    n = 0
    for svc in t.services:
        if not (svc.decision == "counter" and svc.status == "negotiating"):
            continue
        vid = svc.selected_vendor_id
        q = A.latest_valid_quote(svc, vid)
        a = svc.anchors[vid]
        operator_set = svc.operator_counter_rate is not None
        agent_set = (not operator_set) and svc.llm_counter_rate is not None
        try:
            if operator_set:
                c = svc.operator_counter_rate
            elif agent_set:
                c = svc.llm_counter_rate
            else:
                c = A.next_counter(svc, vid, q, t.policy)
                if c is None:
                    raise BoundViolation("no counter left above our last offer and within the walk-away price")
            A.assert_counter_bounds(c, q.unit_rate, a.floor_rate, t.policy, svc.negotiation_round)
        except BoundViolation as e:
            svc.status, svc.decision = "escalated", "escalate"
            svc.escalation = Escalation(reason=f"guardrail blocked counter: {e}", code="bound_hit",
                                        options=["accept_quote", "drop_service"])
            svc.reasoning = svc.escalation.reason
            t.log("agent:negotiation", "counter", "guardrail_blocked", service=svc.service_id, error=str(e))
            t.log("agent:escalation", "counter", "escalated_to_human", service=svc.service_id, reason=svc.escalation.reason)
            continue
        body = svc.llm_counter_body if agent_set else ""
        svc.operator_counter_rate, svc.llm_counter_rate, svc.llm_counter_body = None, None, ""
        disc = round((q.unit_rate - c) / q.unit_rate * 100, 2)
        svc.counters.append(CounterRecord(round=svc.negotiation_round, vendor_id=vid, quote_rate=q.unit_rate,
                                          discount_pct=disc, counter_rate=c, floor_rate=a.floor_rate, operator_set=operator_set))
        t.drafts.append(A.draft_counter(t, svc, vid, _vname(vid), c, q, svc.negotiation_round, agent_body=body))
        t.log("agent:negotiation", "counter", "counter_drafted", service=svc.service_id, vendor_id=vid, round=svc.negotiation_round + 1,
              quote=q.unit_rate, counter=c, discount_pct=disc, reasoning=svc.reasoning)
        n += 1
    if n:
        t.status = "awaiting_approval"
        t.gate = {"gate": "approve_counter"}
    else:
        _refresh_status(t)
    return _out(t)


def route_after_counter(s):
    t = _load(s)
    return "gate_counter" if [x for x in t.drafts if x.status == "draft" and x.kind == "counter"] else "route"


def gate_counter(s):
    t = _load(s)
    pend = [x for x in t.drafts if x.status == "draft" and x.kind == "counter"]
    d = interrupt({"gate": "approve_counter", "trip_id": t.trip_id, "scope": "service",
                   "reason": "Counter-offers are within the 10% / floor-rate / round-cap guardrails. Approve to send.",
                   "drafts": [dict(_draft_view(t, x), quote_rate=t.svc(x.service_id).counters[-1].quote_rate,
                                   discount_pct=t.svc(x.service_id).counters[-1].discount_pct,
                                   reasoning=t.svc(x.service_id).reasoning) for x in pend],
                   "options": ["approve", "edit", "reject"]})
    pend, rej = _apply_draft_gate(t, d, ("counter",), "gate_counter")
    for x in pend:
        if x.draft_id in rej:
            svc = t.svc(x.service_id)
            if svc.counters:
                svc.counters.pop()
            svc.status, svc.decision = "escalated", "escalate"
            svc.escalation = Escalation(reason="operator rejected the proposed counter-offer", code="operator_rejected_counter",
                                        options=["accept_quote", "counter", "drop_service"])
    _refresh_status(t)
    return _out(t)


def gate_final(s):
    t = _load(s)
    act = _active(t)
    lines, net = [], 0.0
    for svc in act:
        vq = _resolved_valid_quotes(svc)
        total = svc.agreed_rate * svc.units
        net += total
        lines.append(dict(service_id=svc.service_id, category=svc.category, spec=svc.spec, vendor_id=svc.selected_vendor_id,
                          vendor=_vname(svc.selected_vendor_id), unit_rate=svc.agreed_rate, units=svc.units, unit_label=svc.unit_label,
                          total=total, currency=svc.anchors[svc.selected_vendor_id].currency, reasoning=svc.reasoning,
                          confidence=svc.confidence, label=A.conf_label(svc.confidence),
                          alternatives=[dict(vendor_id=v, vendor=_vname(v), unit_rate=q.unit_rate) for v, q in vq.items()
                                        if v != svc.selected_vendor_id]))
    cur = lines[0]["currency"] if lines else ""
    client = net * (1 + t.policy.markup_pct / 100)
    b = t.brief
    within = None if not b.budget_total else (client <= b.budget_total)
    d = interrupt({"gate": "approve_final", "trip_id": t.trip_id, "scope": "trip",
                   "reason": "No quote is accepted and no confirmation is sent without your approval. The system makes no payments.",
                   "lines": lines, "net_total": net, "client_total": round(client), "markup_pct": t.policy.markup_pct,
                   "currency": cur, "budget": b.budget_total, "within_budget": within,
                   "options": ["approve", "edit (switch a vendor)", "reject"]})
    action = d.get("action", "reject")
    if action == "reject":
        t.status = "rejected"
        t.log("human", "gate_final", "gate_reject", note=d.get("note", ""))
        return _out(t)
    if action == "edit":
        for sid, vid in (d.get("selections") or {}).items():
            svc = t.svc(sid)
            q = A.latest_valid_quote(svc, vid)
            if q:
                svc.selected_vendor_id, svc.agreed_rate = vid, q.unit_rate
                svc.reasoning = f"Operator switched to {_vname(vid)} at {fmt_money(q.unit_rate, q.currency)}."
                t.log("human", "gate_final", "selection_changed", service=sid, vendor_id=vid)
    for svc in _active(t):
        svc.status = "confirmed"
        v = kb.vendor(svc.selected_vendor_id)
        body = (f"Dear {v['name']} team,\n\nFurther to our correspondence, we would like to proceed with {svc.spec} at the agreed "
                f"rate of {fmt_money(svc.agreed_rate, svc.anchors[svc.selected_vendor_id].currency)} per unit, inclusive of all taxes. "
                f"Please send your formal confirmation and booking terms.\n\nKind regards,\nMsafara Operations")
        t.drafts.append(OutreachDraft(draft_id=f"d-{uuid.uuid4().hex[:8]}", vendor_id=svc.selected_vendor_id,
                                      service_id=svc.service_id, kind="confirm", subject=f"Booking confirmation - {t.brief.city}",
                                      body=body, status="approved", generated_by="template"))
    t.log("human", "gate_final", f"gate_{action}", by=d.get("by", "operator"), note=d.get("note", ""),
          total=net, client_total=round(client))
    t.gate = None
    return _out(t)


def route_after_final(s):
    return END if _load(s).status == "rejected" else "finalize"


def finalize(s):
    t = _load(s)
    for x in t.drafts:
        if x.kind == "confirm" and x.status == "approved":
            A.dispatch_draft(t, x, approved_by="operator")
    net = sum(sv.agreed_rate * sv.units for sv in _active(t))
    t.status = "confirmed"
    t.outcome = dict(net_total=net, client_total=round(net * (1 + t.policy.markup_pct / 100)),
                     services={sv.service_id: dict(vendor_id=sv.selected_vendor_id, unit_rate=sv.agreed_rate) for sv in _active(t)})
    t.log("system", "finalize", "trip_confirmed", **t.outcome)
    return _out(t)


def route_next(s):
    t = _load(s)
    if t.status in ("rejected", "failed", "confirmed"):
        return END
    if any(x.status == "approved" and x.kind != "confirm" for x in t.drafts):
        return "send"
    act = _active(t)
    if not act:
        return END
    if any(x.status == "escalated" and x.escalation for x in act):
        return "escalate"
    if any(x.decision == "counter" and x.status == "negotiating" for x in act):
        return "counter"
    if all(x.status == "accepted" for x in act):
        return "gate_final"
    if _pending_replies(t):
        return "collect"
    return END


def _route_node(s):
    return {}  # passthrough anchor so edges can converge on route_next


def build_graph(checkpointer=None):
    g = StateGraph(GState)
    for name, fn in [("intake", intake), ("escalate_intake", escalate_intake), ("match", match), ("outreach", outreach),
                     ("gate_outreach", gate_outreach), ("send", send), ("collect", collect), ("decide", decide),
                     ("escalate", escalate), ("counter", counter), ("gate_counter", gate_counter),
                     ("gate_final", gate_final), ("finalize", finalize), ("route", _route_node)]:
        g.add_node(name, fn)
    targets = {n: n for n in ["send", "escalate", "counter", "gate_final", "collect"]}
    targets[END] = END
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_intake, {"escalate_intake": "escalate_intake", "match": "match"})
    g.add_conditional_edges("escalate_intake", route_escalate_intake, {"escalate_intake": "escalate_intake", "match": "match", END: END})
    g.add_edge("match", "outreach")
    g.add_conditional_edges("outreach", lambda s: "gate_outreach" if [x for x in _load(s).drafts if x.status == "draft"] else "route",
                            {"gate_outreach": "gate_outreach", "route": "route"})
    g.add_edge("gate_outreach", "route")
    g.add_conditional_edges("route", route_next, targets)
    g.add_conditional_edges("send", route_after_send, {"collect": "collect", "route": "route"})
    g.add_edge("collect", "decide")
    g.add_edge("decide", "route")
    g.add_edge("escalate", "route")
    g.add_conditional_edges("counter", route_after_counter, {"gate_counter": "gate_counter", "route": "route"})
    g.add_edge("gate_counter", "route")
    g.add_conditional_edges("gate_final", route_after_final, {"finalize": "finalize", END: END})
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


def sqlite_checkpointer(path: str):
    from langgraph.checkpoint.sqlite import SqliteSaver
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


# ------------------------------------------------------------------ driving API
def _cfg(trip_id):
    return {"configurable": {"thread_id": trip_id}, "recursion_limit": 100}


def snapshot(graph, trip_id: str) -> Optional[dict]:
    st = graph.get_state(_cfg(trip_id))
    if not st or not st.values:
        return None
    gate = None
    for task in st.tasks:
        for it in task.interrupts:
            gate = it.value
    return dict(trip=st.values["trip"], gate=gate, done=not st.next)


def start_trip(graph, request_text: str, trip_id: Optional[str] = None, simulate_profile: Optional[dict] = None) -> dict:
    trip_id = trip_id or f"trip-{uuid.uuid4().hex[:8]}"
    t = TripState(trip_id=trip_id, request_text=request_text, simulate_profile=simulate_profile)
    t.log("human", "start", "trip_requested", request=request_text)
    with _LOCK:
        graph.invoke({"trip": t.model_dump(mode="json")}, _cfg(trip_id))
    return snapshot(graph, trip_id)


def validate_resume(snap: dict, decision: dict):
    """Pre-check an operator decision so bad input is a clean 400, not a corrupted run."""
    gate = snap.get("gate")
    if not gate:
        raise ValueError("this trip has no open approval gate")
    t = TripState.model_validate(snap["trip"])
    g, act = gate["gate"], decision.get("action")
    if g in ("approve_outreach", "approve_counter"):
        if act not in ("approve", "edit", "reject"):
            raise ValueError("action must be approve, edit or reject")
        if act == "edit":
            for did, e in (decision.get("edits") or {}).items():
                x = t.draft(did)
                probs = A.validate_edit(x, t.svc(x.service_id), t.policy, e.get("body", x.body))
                if probs:
                    raise ValueError(f"edit to {did} refused: {'; '.join(probs)}")
    elif g == "escalate_intake":
        if act not in ("edit", "reject"):
            raise ValueError("action must be edit or reject")
        if act == "edit":
            TripBrief.model_validate({**snap["trip"]["brief"], **(decision.get("brief") or {})})
    elif g == "escalation":
        res = decision.get("resolutions") or {}
        for it in gate["items"]:
            r = res.get(it["service_id"])
            if not r or r.get("action") not in ("accept", "counter", "drop"):
                raise ValueError(f"resolution required for {it['service_id']} (accept, counter or drop)")
            svc = t.svc(it["service_id"])
            if r["action"] == "accept" and not A.latest_valid_quote(svc, r.get("vendor_id")):
                raise ValueError(f"{r.get('vendor_id')} has no valid quote to accept")
            if r["action"] == "counter":
                try:
                    A.validate_operator_counter(svc, r.get("vendor_id"), float(r.get("rate")), t.policy)
                except (BoundViolation, TypeError, ValueError) as e:
                    raise ValueError(f"counter refused by guardrails: {e}")
    elif g == "approve_final":
        if act not in ("approve", "edit", "reject"):
            raise ValueError("action must be approve, edit or reject")
        for sid, vid in (decision.get("selections") or {}).items():
            if not A.latest_valid_quote(t.svc(sid), vid):
                raise ValueError(f"{vid} has no valid quote for {sid}")
    elif g == "await_replies":
        pass


def resume_trip(graph, trip_id: str, decision: dict) -> dict:
    with _LOCK:
        snap = snapshot(graph, trip_id)
        if snap is None:
            raise KeyError(trip_id)
        validate_resume(snap, decision)
        graph.invoke(Command(resume=decision), _cfg(trip_id))
    return snapshot(graph, trip_id)


def drive(graph, trip_id: str, operator: Callable[[dict], dict], max_steps: int = 40) -> dict:
    """Run a trip to completion with a programmatic operator (evals / demo / tests)."""
    snap = snapshot(graph, trip_id)
    for _ in range(max_steps):
        if not snap or snap["done"] or not snap["gate"]:
            break
        snap = resume_trip(graph, trip_id, operator(snap["gate"]))
    return snap
