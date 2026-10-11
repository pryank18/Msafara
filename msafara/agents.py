"""Agent logic: intake, vendor matching, outreach, quote parsing, negotiation decisions, dispatch.

Design rule: LLMs may *read* (intake, quote parsing) and *word* (outreach/counter text).
Everything that decides money or sending is plain code:
  - counter amounts: compute_counter() + assert_counter_bounds()
  - accept/counter/escalate: decide_service()
  - what may leave the building: validate_text() / dispatch_draft()
With no API key every agent runs on its rule-based path (the "mock" backend).
"""
import math
import os
import re
import uuid
from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel

from . import kb, llm, rag
from .models import (HARD_MAX_DISCOUNT_PCT, Anchor, Candidate, CounterRecord, Escalation, OutreachDraft,
                     Policy, Quote, ServiceLine, TripBrief, TripState)
from .textutil import ceil_money, currency_numbers, fmt_money, med, to_float

REQUIRED_FIELDS = ["city", "check_in", "check_out", "pax", "hotel_star"]


class BoundViolation(Exception):
    """A negotiation guardrail would have been broken. Never swallowed silently."""


class UnapprovedSend(Exception):
    pass


# =========================================================== INTAKE (mock parsers)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
_MON = "|".join(list(_MONTHS) + [m[:3] for m in _MONTHS])
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_WORDNUM = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_WORDNUM.update({"thirty": 30, "twenty five": 25, "twenty-five": 25, "twenty four": 24, "twenty-four": 24})
_WORD_ALT = "|".join(sorted(_WORDNUM, key=len, reverse=True))


def _mk(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _dates_v1(text):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s*(?:to|-|–)\s*(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return _mk(*m.groups()[:3]), _mk(*m.groups()[3:])
    m = re.search(rf"(?<![\d/-])(\d{{1,2}})\s*(?:-|–|to)\s*(\d{{1,2}})\s+({_MON})\s+(\d{{4}})", text, re.I)
    if m:
        d1, d2, mon, y = m.groups()
        return _mk(y, _MONTHS[mon.lower()], d1), _mk(y, _MONTHS[mon.lower()], d2)
    return None, None


def _dates_v2(text):
    ci, co = _dates_v1(text)
    if ci and co:
        return ci, co
    m = re.search(rf"({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:-|–|to)\s*(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", text, re.I)
    if m:
        mon, d1, d2, y = m.groups()
        return _mk(y, _MONTHS[mon.lower()], d1), _mk(y, _MONTHS[mon.lower()], d2)
    found = []
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", text):
        found.append((m.start(), _mk(*m.groups())))
    for m in re.finditer(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):          # DD/MM/YYYY
        found.append((m.start(), _mk(m.group(3), m.group(2), m.group(1))))
    for m in re.finditer(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})\s*,?\s*(\d{{4}})", text, re.I):
        found.append((m.start(), _mk(m.group(3), _MONTHS[m.group(2).lower()], m.group(1))))
    for m in re.finditer(rf"({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", text, re.I):
        found.append((m.start(), _mk(m.group(3), _MONTHS[m.group(1).lower()], m.group(2))))
    found = sorted((p, d) for p, d in found if d)
    if len(found) >= 2:
        return found[0][1], found[1][1]
    if len(found) == 1:
        n = re.search(r"for\s+(\d+)\s+nights?", text, re.I)
        if n:
            return found[0][1], found[0][1] + timedelta(days=int(n.group(1)))
    return None, None


def _pax_v1(text):
    m = re.search(r"(\d+)\s*(?:people|pax|guests|persons)", text, re.I)
    return int(m.group(1)) if m else None


def _word_to_int(w):
    w = w.lower().strip()
    return _WORDNUM.get(w)


def _pax_v2(text):
    p = _pax_v1(text)
    if p:
        return p
    m = re.search(r"(\d+)\s+adults?\s+and\s+(\d+)\s+(?:kids|children|child)", text, re.I)
    if m:
        return int(m.group(1)) + int(m.group(2))
    m = re.search(r"(?:group|party|team)\s+of\s+(\d+)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(rf"(?:group|party|team)\s+of\s+({_WORD_ALT})\b", text, re.I)
    if m:
        return _word_to_int(m.group(1))
    m = re.search(r"\bpax[:\s]+(\d+)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(?:travell?ers|adults|delegates|members|folks|of us)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(rf"\b({_WORD_ALT})\s+(?:people|pax|guests|travell?ers)", text, re.I)
    if m:
        return _word_to_int(m.group(1))
    return None


def _star_v1(text):
    m = re.search(r"\b([345])[\s-]star", text, re.I)
    return int(m.group(1)) if m else None


def _star_v2(text):
    s = _star_v1(text)
    if s:
        return s
    m = re.search(r"\b([345])\s*\*", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(three|four|five)[\s-]star", text, re.I)
    if m:
        return {"three": 3, "four": 4, "five": 5}[m.group(1).lower()]
    return None


def _budget_v1(text):
    m = re.search(r"(INR|USD|\$|Rs\.?|₹)\s*(\d[\d,]*(?:\.\d+)?)", text, re.I)
    if not m:
        return None, None
    cur = "USD" if m.group(1).upper() in ("USD", "$") else "INR"
    return to_float(m.group(2).rstrip(",")), cur


_AMT = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(lakhs?|lacs?|k|m|million)?"


def _scale(num, suf):
    suf = (suf or "").lower()
    mult = 100000 if suf.startswith(("lakh", "lac")) else 1000 if suf == "k" else 1_000_000 if suf in ("m", "million") else 1
    return to_float(num) * mult


def _budget_v2(text, city):
    cur_default = kb.CITY_CURRENCY.get(city or "", None)
    m = re.search(rf"(INR|USD|\$|Rs\.?|₹)\s*{_AMT}", text, re.I)
    if m:
        cur = "USD" if m.group(1).upper() in ("USD", "$") else "INR"
        return _scale(m.group(2), m.group(3)), cur
    m = re.search(rf"{_AMT}\s*(INR|USD|rupees|dollars|rs)\b", text, re.I)
    if m:
        cur = "USD" if m.group(3).upper() in ("USD", "DOLLARS") else "INR"
        return _scale(m.group(1), m.group(2)), cur
    m = re.search(rf"budget[^\d]{{0,25}}{_AMT}", text, re.I)
    if m:
        return _scale(m.group(1), m.group(2)), cur_default
    return None, None


def mock_intake(text: str, version: str = "v2") -> TripBrief:
    v2 = version != "v1"
    city = kb.city_in_text(text) if v2 else next(
        (c for c in kb.CITY_CURRENCY if re.search(rf"\b{c}\b", text, re.I)), None)
    ci, co = (_dates_v2 if v2 else _dates_v1)(text)
    pax = (_pax_v2 if v2 else _pax_v1)(text)
    star = (_star_v2 if v2 else _star_v1)(text)
    budget, cur = _budget_v2(text, city) if v2 else _budget_v1(text)
    return TripBrief(city=city, check_in=ci, check_out=co, pax=pax, hotel_star=star, budget_total=budget, currency=cur)


def finalize_brief(b: TripBrief) -> TripBrief:
    """Authoritative post-processing in code (applies to mock AND LLM output)."""
    if b.city:
        b.city = kb.canonical_city(b.city) or kb.city_in_text(b.city) or b.city
    if b.city not in kb.CITY_CURRENCY:
        b.city = None
    if b.check_in and b.check_out:
        if b.check_out <= b.check_in:
            b.check_out = None
        else:
            b.nights = (b.check_out - b.check_in).days
    else:
        b.nights = None
    if b.hotel_star not in (3, 4, 5):
        b.hotel_star = None
    if b.pax is not None and b.pax <= 0:
        b.pax = None
    if b.budget_total is not None and b.currency is None and b.city:
        b.currency = kb.CITY_CURRENCY[b.city]
    b.missing = [f for f in REQUIRED_FIELDS if getattr(b, f) in (None, "")]
    if b.missing:
        b.confidence = min(b.confidence or 0.0, 0.4)
    elif not b.confidence:
        b.confidence = 0.9
    return b


def run_intake(text: str, trip: Optional[TripState] = None) -> TripBrief:
    if llm.mock_mode():
        brief = mock_intake(text, os.environ.get("MSAFARA_MOCK_INTAKE", "v2"))
        brief.confidence = 0.9
    else:
        try:
            brief = llm.structured_call("intake", text, TripBrief)
        except llm.LLMOutputError as e:
            if trip:
                trip.log("agent:intake", "intake", "llm_failed_fallback_to_rules", error=str(e)[:200])
            brief = mock_intake(text, "v2")
            brief.confidence = 0.5
    return finalize_brief(brief)


# =========================================================== SERVICES, POLICY, ANCHORS
def build_services(brief: TripBrief) -> list[ServiceLine]:
    rooms = math.ceil(brief.pax / 2)
    nights = brief.nights
    return [
        ServiceLine(service_id="hotel", category="hotel", min_capacity=rooms, units=rooms * nights,
                    unit_label="room-nights",
                    spec=f"{rooms} rooms x {nights} nights, {brief.hotel_star}-star, {brief.city}"),
        ServiceLine(service_id="transport", category="transport", min_capacity=brief.pax, units=nights + 1,
                    unit_label="vehicle-days",
                    spec=f"1 vehicle seating {brief.pax}, {nights + 1} days, {brief.city}"),
        ServiceLine(service_id="activity", category="activity", min_capacity=brief.pax, units=brief.pax,
                    unit_label="guests", spec=f"guided group activity for {brief.pax} guests, {brief.city}"),
    ]


def get_policy() -> Policy:
    p = kb.load()["policies"]
    return Policy(**{k: p[k] for k in ("markup_pct", "max_discount_pct", "max_rounds", "discount_schedule",
                                       "over_anchor_tolerance_pct", "confidence_threshold",
                                       "preferred_vendor_ids", "blacklist_vendor_ids")})


def policy_chunk(query: str, section: str) -> rag.Hit | None:
    hits = rag.retrieve(query, {"doc_type": "policy", "section": section}, k=1)
    return hits[0] if hits else None


def anchor_for(vendor_id: str) -> Anchor:
    """Build the pricing anchor ONLY from retrieved chunks (vendor-ID filter + semantic rank)."""
    pq = rag.retrieve("past quote rate for this vendor", {"vendor_id": vendor_id, "doc_type": "past_quote"}, k=10)
    rc = rag.retrieve("rate card published rate", {"vendor_id": vendor_id, "doc_type": "rate_card"}, k=1)
    nl = rag.retrieve("past negotiation settled below opening quote", {"vendor_id": vendor_id, "doc_type": "negotiation_log"}, k=1)
    a = Anchor(vendor_id=vendor_id, n_quotes=len(pq), sources=[h.id for h in pq + rc + nl])
    if pq:
        a.anchor_rate = med([h.metadata["rate"] for h in pq])
    if rc:
        a.rate_card_rate = rc[0].metadata["rate"]
        a.floor_rate = rc[0].metadata["floor_rate"]
        a.currency = rc[0].metadata["currency"]
    if nl:
        f, s = nl[0].metadata["first_quote"], nl[0].metadata["settled"]
        a.history_note = f"Past negotiation: settled {round((f - s) / f * 100)}% below the opening quote."
    return a


# =========================================================== VENDOR MATCHING
def match_vendors(svc: ServiceLine, brief: TripBrief, policy: Policy, max_n: int = 5):
    where = {"doc_type": "vendor_profile", "city": brief.city, "category": svc.category}
    if svc.category == "hotel":
        where["star"] = brief.hotel_star
    hits = rag.retrieve(f"{svc.spec} for a group of {brief.pax}", where, k=12)
    top = max((h.score for h in hits), default=1.0) or 1.0
    scored, excluded = [], []
    for h in hits:
        m = h.metadata
        vid = m["vendor_id"]
        if m["blacklisted"] or vid in policy.blacklist_vendor_ids:
            excluded.append(dict(vendor_id=vid, reason="blacklisted")); continue
        if m["capacity"] < svc.min_capacity:
            excluded.append(dict(vendor_id=vid, reason=f"capacity {m['capacity']} < needed {svc.min_capacity}")); continue
        pref = vid in policy.preferred_vendor_ids or bool(m["preferred"])
        sem = max(h.score, 0.0) / top
        score = 0.5 * sem + 0.3 * m["reliability"] + 0.2 * (1.0 if pref else 0.0)
        why = f"semantic {sem:.2f}, reliability {m['reliability']:.2f}" + (", preferred vendor" if pref else "")
        scored.append(Candidate(vendor_id=vid, name=m["name"], score=round(score, 3), why=why, preferred=pref))
    scored.sort(key=lambda c: (-c.score, c.vendor_id))
    return scored[:max_n], excluded


# =========================================================== TEXT GUARDRAILS
_FORBIDDEN_WORDS = re.compile(r"\b(floor rate|markup|mark-up|walk-away|our budget)\b", re.I)


def validate_text(body: str, allowed: set[float], floor: Optional[float] = None) -> list[str]:
    """Return a list of problems (empty = safe to send). Used on LLM output."""
    problems = []
    for n in currency_numbers(body):
        if not any(abs(n - a) < 0.5 for a in allowed):
            problems.append(f"ungrounded amount {n:,.0f}")
    if floor is not None and any(abs(n - floor) < 0.5 for n in currency_numbers(body)) and \
            not any(abs(floor - a) < 0.5 for a in allowed):
        problems.append("floor rate disclosed")
    if _FORBIDDEN_WORDS.search(body):
        problems.append("mentions internal terms")
    return problems


def offer_numbers(body: str, quote: float, anchor: Optional[float]) -> list[float]:
    """Currency amounts in a counter message that are OFFERS: everything except the vendor's own quote and
    the past-quote anchor we cite as a reference."""
    refs = [quote] + ([anchor] if anchor else [])
    return [n for n in currency_numbers(body) if not any(abs(n - r) < 0.5 for r in refs)]


def validate_edit(draft: OutreachDraft, svc: ServiceLine, policy: Policy, body: str) -> list[str]:
    """Checks applied to HUMAN-edited text: never reveal the floor; counters must stay within bounds."""
    problems = []
    a = svc.anchors.get(draft.vendor_id)
    nums = currency_numbers(body)
    if a and a.floor_rate and any(abs(n - a.floor_rate) < 0.5 for n in nums) and draft.kind != "counter":
        problems.append("edit reveals the internal floor rate")
    if _FORBIDDEN_WORDS.search(body):
        problems.append("edit mentions internal terms (floor/markup/budget)")
    if draft.kind == "counter":
        q = latest_valid_quote(svc, draft.vendor_id)
        if q and a and a.floor_rate:
            lo = max(a.floor_rate, q.unit_rate * (1 - min(policy.max_discount_pct, HARD_MAX_DISCOUNT_PCT) / 100.0)) - 1
            offers = offer_numbers(body, q.unit_rate, a.anchor_rate)
            low = [n for n in offers if n < lo]
            if low:
                problems.append(f"edited counter offers {low[0]:,.0f}, below the allowed minimum {lo + 1:,.0f}")
            if not offers:
                problems.append("edited counter contains no offer amount")
    return problems


# =========================================================== DRAFTING
class _Msg(BaseModel):
    subject: str
    body: str


def _unit_phrase(svc: ServiceLine) -> str:
    return {"hotel": "per room per night", "transport": "per vehicle per day", "activity": "per person"}[svc.category]


def _cancellation_sentence() -> tuple[str, list[str]]:
    h = policy_chunk("cancellation terms free cancellation", "Cancellation terms")
    if not h:
        return "", []
    txt = h.text.split("Cancellation terms:", 1)[-1].strip()
    return txt, [h.id]


def _outreach_template(trip: TripState, svc: ServiceLine, vname: str, a: Anchor, canc: str) -> _Msg:
    b = trip.brief
    ref = (f" For reference, your recent quotes to us were around {fmt_money(a.anchor_rate, a.currency)} {_unit_phrase(svc)}."
           if a.anchor_rate else "")
    body = (f"Dear {vname} team,\n\n"
            f"We are planning a group trip to {b.city} and would like to check availability and your best rate.\n\n"
            f"- Dates: {b.check_in:%d %b %Y} to {b.check_out:%d %b %Y}\n"
            f"- Requirement: {svc.spec}\n"
            f"- Group size: {b.pax} guests\n\n"
            f"Could you please confirm (1) availability for the full quantity and (2) your rate {_unit_phrase(svc)}, "
            f"inclusive of all taxes?{ref}\n\n"
            + (f"Our standard cancellation terms: {canc} Please tell us if you can match or improve on them.\n\n" if canc else "")
            + "Kind regards,\nMsafara Operations")
    return _Msg(subject=f"Availability & quote request - {b.city}, {b.check_in:%d %b %Y}", body=body)


def draft_outreach(trip: TripState, svc: ServiceLine, cand: Candidate) -> OutreachDraft:
    a = svc.anchors[cand.vendor_id]
    canc, canc_src = _cancellation_sentence()
    msg, by = _outreach_template(trip, svc, cand.name, a, canc), "template"
    if not llm.mock_mode():
        try:
            facts = dict(trip_brief=trip.brief.model_dump(mode="json"), requirement=svc.spec, vendor=cand.name,
                         unit=_unit_phrase(svc), vendor_past_quote_anchor=a.anchor_rate, currency=a.currency,
                         cancellation_terms=canc)
            cand_msg = llm.structured_call("outreach", __import__("json").dumps(facts), _Msg)
            problems = validate_text(cand_msg.body, {a.anchor_rate} if a.anchor_rate else set(), a.floor_rate)
            if problems:
                trip.log("agent:outreach", "outreach", "llm_text_rejected_used_template", vendor_id=cand.vendor_id, problems=problems)
                by = "template(fallback)"
            else:
                msg, by = cand_msg, "llm"
        except llm.LLMOutputError as e:
            trip.log("agent:outreach", "outreach", "llm_failed_used_template", vendor_id=cand.vendor_id, error=str(e)[:200])
            by = "template(fallback)"
    return OutreachDraft(draft_id=f"d-{uuid.uuid4().hex[:8]}", vendor_id=cand.vendor_id, service_id=svc.service_id,
                         kind="outreach", subject=msg.subject, body=msg.body, grounding=a.sources + canc_src,
                         generated_by=by)


def draft_counter(trip: TripState, svc: ServiceLine, vendor_id: str, vname: str, counter: float, quote: Quote,
                  rnd: int) -> OutreachDraft:
    a = svc.anchors[vendor_id]
    cur = quote.currency or a.currency
    ref = (f" Based on your recent quotes to us (around {fmt_money(a.anchor_rate, a.currency)}),"
           if a.anchor_rate else "")
    body = (f"Dear {vname} team,\n\nThank you for your quote of {fmt_money(quote.unit_rate, cur)} {_unit_phrase(svc)} "
            f"for {svc.spec}.{ref} we would like to propose {fmt_money(counter, cur)} {_unit_phrase(svc)}, "
            f"inclusive of all taxes, for the same dates and quantity.\n\n"
            f"This proposal is subject to our final confirmation. Could you let us know whether you can accept it?\n\n"
            f"Kind regards,\nMsafara Operations")
    msg, by = _Msg(subject=f"Re: Availability & quote request - {trip.brief.city}", body=body), "template"
    if not llm.mock_mode():
        try:
            facts = dict(counter_offer_amount=counter, vendor_quote=quote.unit_rate, vendor_past_quote_anchor=a.anchor_rate,
                         currency=cur, unit=_unit_phrase(svc), vendor=vname, requirement=svc.spec, history=a.history_note)
            cand_msg = llm.structured_call("counter", __import__("json").dumps(facts), _Msg)
            allowed = {counter, quote.unit_rate} | ({a.anchor_rate} if a.anchor_rate else set())
            problems = validate_text(cand_msg.body, allowed, a.floor_rate)
            if not any(abs(n - counter) < 0.5 for n in currency_numbers(cand_msg.body)):
                problems.append("counter amount missing from text")
            if problems:
                trip.log("agent:negotiation", "counter", "llm_text_rejected_used_template", vendor_id=vendor_id, problems=problems)
                by = "template(fallback)"
            else:
                msg, by = cand_msg, "llm"
        except llm.LLMOutputError as e:
            trip.log("agent:negotiation", "counter", "llm_failed_used_template", vendor_id=vendor_id, error=str(e)[:200])
            by = "template(fallback)"
    return OutreachDraft(draft_id=f"d-{uuid.uuid4().hex[:8]}", vendor_id=vendor_id, service_id=svc.service_id,
                         kind="counter", subject=msg.subject, body=msg.body, round=rnd, counter_rate=counter,
                         grounding=a.sources, generated_by=by)


# =========================================================== QUOTE PARSING
class ParsedQuote(BaseModel):
    status: str
    unit_rate: Optional[float] = None
    currency: str = ""
    tax_inclusive: Optional[bool] = None
    available_units: Optional[int] = None
    accepts_counter: bool = False
    confidence: float = 0.0
    summary: str = ""


_DECLINE = re.compile(r"fully booked|sold out|not available|no availability|unable to|cannot accommodate|can't accommodate|"
                      r"regret|unfortunately we (?:do not|don't|cannot|can't)|not able to", re.I)
_HEDGE = re.compile(r"probably|maybe|might be|let me check|will revert|get back to you|not sure|depends|tentative|check with", re.I)
_INCL = re.compile(r"inclusive|\bincl\b|taxes included|all taxes|including (?:all )?tax|tax(?:es)? included|all-in", re.I)
_EXCL = re.compile(r"exclusive of|excluding|excl\b|before tax", re.I)
_AGREE = re.compile(r"\b(accept|agree|agreed|okay|ok|deal|works for us|happy to)\b", re.I)
_REFUSE = re.compile(r"cannot|can't|unable|sorry|unfortunately|not possible|best rate|lowest|final rate|no flexibility", re.I)


def mock_parse_quote(text: str, needed_qty: int, our_counter: Optional[float], cur: str) -> ParsedQuote:
    nums = currency_numbers(text)
    uniq = sorted(set(nums))
    pct = re.search(r"(?:plus|\+)\s*(\d+(?:\.\d+)?)\s*%\s*(?:gst|vat|tax|taxes)", text, re.I)
    avail = None
    m = re.search(r"only\s+(?:have\s+)?(\d+)\s+(?:rooms?|vehicles?|seats?|spots?|places?|guests?|people)", text, re.I) or \
        re.search(r"(\d+)\s+(?:rooms?|vehicles?|seats?|spots?|places?)\s+(?:are\s+)?available", text, re.I)
    if m:
        avail = int(m.group(1))
    # 1) explicit agreement to OUR counter
    if our_counter is not None and any(abs(n - our_counter) < 0.5 for n in nums) and _AGREE.search(text) \
            and not _REFUSE.search(text) and len(uniq) == 1:
        return ParsedQuote(status="quote", unit_rate=our_counter, currency=cur, tax_inclusive=True,
                           accepts_counter=True, confidence=0.9, summary="Vendor agreed to our counter-offer.")
    theirs = [n for n in uniq if our_counter is None or abs(n - our_counter) >= 0.5]
    if _DECLINE.search(text) and not theirs:
        return ParsedQuote(status="decline", currency=cur, confidence=0.9, summary="Vendor declined / unavailable.")
    if not theirs or _HEDGE.search(text):
        return ParsedQuote(status="ambiguous", currency=cur, confidence=0.4,
                           unit_rate=None, summary="Reply does not give a clear price or answer.")
    if len(theirs) > 1:
        return ParsedQuote(status="ambiguous", currency=cur, confidence=0.4, summary="Conflicting amounts in reply.")
    rate = theirs[0]
    if avail is not None and avail < needed_qty:
        return ParsedQuote(status="partial", unit_rate=rate, currency=cur, available_units=avail, confidence=0.85,
                           summary=f"Only {avail} of {needed_qty} units available.")
    if pct:
        return ParsedQuote(status="quote", unit_rate=float(ceil_money(rate * (1 + float(pct.group(1)) / 100))),
                           currency=cur, tax_inclusive=False, confidence=0.85,
                           summary=f"{rate:,.0f} + {pct.group(1)}% tax normalised to tax-inclusive.")
    if _INCL.search(text) and not _EXCL.search(text):
        return ParsedQuote(status="quote", unit_rate=rate, currency=cur, tax_inclusive=True, confidence=0.92,
                           summary="Clear tax-inclusive quote.")
    return ParsedQuote(status="tax_unclear", unit_rate=rate, currency=cur, tax_inclusive=None, confidence=0.8,
                       summary="Price given but taxes not stated.")


def rate_grounded(rate: float, text: str) -> bool:
    nums = currency_numbers(text)
    if any(abs(rate - n) < 0.5 for n in nums):
        return True
    pcts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]
    return any(abs(rate - n * (1 + p / 100)) <= 1.5 for n in nums for p in pcts)


def parse_quote(trip: TripState, svc: ServiceLine, vendor_id: str, reply: Optional[str], rnd: int,
                our_counter: Optional[float]) -> Quote:
    a = svc.anchors[vendor_id]
    cur = a.currency
    if not reply or not reply.strip():
        return Quote(vendor_id=vendor_id, status="no_response", currency=cur, confidence=0.9,
                     summary="No reply received.", round=rnd)
    needed = svc.min_capacity
    if llm.mock_mode():
        pq = mock_parse_quote(reply, needed, our_counter, cur)
    else:
        try:
            ctx = dict(reply=reply, quantity_requested=needed, unit=_unit_phrase(svc), currency=cur,
                       our_counter_offer=our_counter)
            pq = llm.structured_call("parse_quote", __import__("json").dumps(ctx), ParsedQuote)
            if pq.status not in ("quote", "decline", "partial", "ambiguous", "tax_unclear"):
                raise llm.LLMOutputError(f"unknown status {pq.status}")
            if pq.unit_rate is not None and not (rate_grounded(pq.unit_rate, reply) or
                                                 (pq.accepts_counter and our_counter is not None and abs(pq.unit_rate - our_counter) < 0.5)):
                trip.log("agent:parse", "parse", "llm_rate_not_in_reply_downgraded", vendor_id=vendor_id, claimed=pq.unit_rate)
                pq = ParsedQuote(status="ambiguous", currency=cur, confidence=0.3, summary="Parsed rate not found in reply text.")
        except llm.LLMOutputError as e:
            trip.log("agent:parse", "parse", "llm_failed_mark_ambiguous", vendor_id=vendor_id, error=str(e)[:200])
            pq = ParsedQuote(status="ambiguous", currency=cur, confidence=0.3, summary="Could not parse reply reliably.")
    return Quote(vendor_id=vendor_id, status=pq.status, unit_rate=pq.unit_rate, currency=pq.currency or cur,
                 tax_inclusive=pq.tax_inclusive, available_units=pq.available_units, accepts_counter=pq.accepts_counter,
                 confidence=pq.confidence, summary=pq.summary, raw_text=reply, round=rnd)


# =========================================================== NEGOTIATION (pure code)
def compute_counter(quote: float, floor: float, discount_pct: float) -> int:
    d = max(0.0, min(discount_pct, HARD_MAX_DISCOUNT_PCT))
    return ceil_money(max(quote * (1 - d / 100.0), floor))


def assert_counter_bounds(counter: float, quote: float, floor: float, policy: Policy, rounds_sent: int):
    cap = min(policy.max_discount_pct, HARD_MAX_DISCOUNT_PCT)
    if rounds_sent >= policy.rounds_cap:
        raise BoundViolation(f"round cap reached ({rounds_sent}/{policy.rounds_cap})")
    if counter < floor - 1e-6:
        raise BoundViolation(f"counter {counter} below floor {floor}")
    if counter < quote * (1 - cap / 100.0) - 1e-6:
        raise BoundViolation(f"counter {counter} more than {cap}% below quote {quote}")
    if counter >= quote:
        raise BoundViolation(f"counter {counter} is not below quote {quote}")


def latest_valid_quote(svc: ServiceLine, vendor_id: str) -> Optional[Quote]:
    """Most recent usable quote from a vendor (falls back through history if the latest reply was unusable)."""
    for q in reversed(svc.quote_history):
        if q.vendor_id == vendor_id and q.status == "quote" and q.unit_rate:
            return q
    return None


def validate_operator_counter(svc: ServiceLine, vendor_id: str, rate: float, policy: Policy):
    q = latest_valid_quote(svc, vendor_id)
    if not q:
        raise BoundViolation("no valid quote from that vendor to counter")
    assert_counter_bounds(rate, q.unit_rate, svc.anchors[vendor_id].floor_rate, policy, svc.negotiation_round)


def conf_label(c: Optional[float]) -> str:
    if c is None:
        return "-"
    return "High" if c >= 0.8 else "Medium" if c >= 0.6 else "Low"


def _cname(svc, vid):
    return next((c.name for c in svc.candidates if c.vendor_id == vid), vid)


def _no_quote_escalation(svc: ServiceLine) -> Escalation:
    st = [q.status for q in svc.quotes.values()]
    names = lambda s: ", ".join(_cname(svc, v) for v, q in svc.quotes.items() if q.status == s)
    for s, code, text in [("ambiguous", "ambiguous_reply", "ambiguous reply from {n}"),
                          ("partial", "partial_availability", "partial availability from {n} (cannot cover the full quantity)"),
                          ("tax_unclear", "tax_unclear", "price from {n} does not say whether taxes are included")]:
        if s in st:
            return Escalation(reason=text.format(n=names(s)), code=code,
                              options=["ask_vendor_to_clarify (manual)", "drop_service"])
    if st and all(s == "decline" for s in st):
        return Escalation(reason=f"all {len(st)} vendors declined", code="all_declined", options=["drop_service"])
    return Escalation(reason="no vendor responded", code="no_response", options=["drop_service"])


def decide_service(svc: ServiceLine, policy: Policy):
    """Sets svc.decision/status/reasoning/confidence/escalation. Pure rules, no LLM."""
    svc.escalation, svc.decision = None, None
    thr = policy.confidence_threshold
    last_neg = svc.counters[-1].vendor_id if svc.counters else None
    # A reply to our counter that is not a usable quote always goes to a human.
    if last_neg:
        q = svc.quotes.get(last_neg)
        if q and q.round >= svc.negotiation_round and q.status != "quote":
            nm = _cname(svc, last_neg)
            svc.decision, svc.status = "escalate", "escalated"
            svc.escalation = Escalation(
                reason=f"reply from {nm} to our counter was {q.status.replace('_', ' ')}: {q.summary}",
                code=f"counter_reply_{q.status}", options=["accept_quote (previous valid quote)", "counter", "drop_service"])
            svc.reasoning, svc.confidence = svc.escalation.reason, q.confidence
            return
    valid = {v: q for v, q in svc.quotes.items() if q.status == "quote" and q.unit_rate}
    if not valid:
        svc.escalation = _no_quote_escalation(svc)
        svc.decision, svc.status = "escalate", "escalated"
        svc.reasoning, svc.confidence = "Escalated: " + svc.escalation.reason, 0.0
        return
    score = {c.vendor_id: c.score for c in svc.candidates}
    vid = min(valid, key=lambda v: (valid[v].unit_rate, -score.get(v, 0)))
    q, a, nm = valid[vid], svc.anchors[vid], _cname(svc, vid)
    cur = q.currency
    n_valid, n_all = len(valid), len(svc.quotes)
    svc.selected_vendor_id = vid
    base = f"Lowest of {n_valid} valid quote(s) ({n_all} contacted): {nm} at {fmt_money(q.unit_rate, cur)}"
    if q.confidence < thr:
        svc.decision, svc.status = "escalate", "escalated"
        svc.escalation = Escalation(reason=f"low parse confidence ({q.confidence:.2f}) on {nm}'s reply", code="low_confidence",
                                    options=["accept_quote", "counter", "drop_service"])
        svc.reasoning, svc.confidence = svc.escalation.reason, q.confidence
        return
    over_card = ((q.unit_rate - a.rate_card_rate) / a.rate_card_rate * 100) if a.rate_card_rate else 0
    if q.accepts_counter:
        svc.decision, svc.status, svc.agreed_rate = "accept", "accepted", q.unit_rate
        svc.reasoning = f"{nm} agreed to our counter of {fmt_money(q.unit_rate, cur)} (floor on file respected)."
        svc.confidence = round(q.confidence * 0.95, 2)
        return
    if a.anchor_rate is None:
        svc.decision, svc.status = "escalate", "escalated"
        svc.escalation = Escalation(reason=f"no past-quote anchor on file for {nm}; cannot ground a decision", code="no_anchor",
                                    options=["accept_quote", "drop_service"])
        svc.reasoning, svc.confidence = svc.escalation.reason, 0.3
        return
    if q.unit_rate <= a.anchor_rate:
        svc.decision, svc.status, svc.agreed_rate = "accept", "accepted", q.unit_rate
        pct = (a.anchor_rate - q.unit_rate) / a.anchor_rate * 100
        svc.reasoning = f"{base}, {pct:.0f}% at/below its past-quote anchor {fmt_money(a.anchor_rate, cur)}: accept."
        svc.confidence = round(q.confidence * 0.95, 2)
        return
    over = (q.unit_rate - a.anchor_rate) / a.anchor_rate * 100
    if svc.negotiation_round < policy.rounds_cap:
        d = policy.discount_for_round(svc.negotiation_round)
        counter = compute_counter(q.unit_rate, a.floor_rate, d)
        if counter >= q.unit_rate:
            svc.decision, svc.status, svc.agreed_rate = "accept", "accepted", q.unit_rate
            svc.reasoning = f"{base}; already at/near our floor, nothing to negotiate: accept."
            svc.confidence = round(q.confidence * 0.85, 2)
            return
        svc.decision, svc.status = "counter", "negotiating"
        hist = f" {a.history_note}" if a.history_note else ""
        svc.reasoning = (f"{base}, {over:.0f}% above anchor {fmt_money(a.anchor_rate, cur)}; "
                         f"counter round {svc.negotiation_round + 1} at {fmt_money(counter, cur)} "
                         f"(max {d:.0f}% off, floor respected).{hist}")
        svc.confidence = round(q.confidence * 0.85, 2)
        return
    if over <= policy.over_anchor_tolerance_pct:
        svc.decision, svc.status, svc.agreed_rate = "accept", "accepted", q.unit_rate
        svc.reasoning = (f"{base}; vendor held firm after {svc.negotiation_round} rounds, {over:.0f}% above anchor "
                         f"(within {policy.over_anchor_tolerance_pct:.0f}% tolerance): accept.")
        svc.confidence = round(q.confidence * 0.75, 2)
        return
    svc.decision, svc.status = "escalate", "escalated"
    svc.escalation = Escalation(
        reason=(f"quote {over:.0f}% above anchor after {svc.negotiation_round} rounds: {fmt_money(q.unit_rate, cur)} vs "
                f"{fmt_money(a.anchor_rate, cur)} ({over_card:+.0f}% vs rate card {fmt_money(a.rate_card_rate, cur)})"),
        code="over_anchor", options=["accept_quote", "drop_service"])
    svc.reasoning, svc.confidence = "Escalated: " + svc.escalation.reason, round(q.confidence * 0.6, 2)


# =========================================================== DISPATCH (single chokepoint)
def dispatch_draft(trip: TripState, draft: OutreachDraft, approved_by: str) -> str:
    """The ONLY function that sends. Refuses anything not approved by a human gate."""
    if draft.status != "approved":
        raise UnapprovedSend(f"draft {draft.draft_id} is '{draft.status}', not 'approved'")
    channel = "dry-run"
    if os.environ.get("MSAFARA_LIVE_DISPATCH") == "1":
        try:
            import message_router
            from state import VendorInfo
            v = kb.vendor(draft.vendor_id)
            ok = message_router.send(f"{trip.trip_id}:{draft.draft_id}",
                                     VendorInfo(name=v["name"], contact_channel="email", contact_address=v["email"]),
                                     draft.subject, draft.body)
            channel = "email" if ok else "email-not-configured"
        except Exception as e:  # never crash the trip because a channel is down
            channel = f"failed:{type(e).__name__}"
    draft.status = "sent"
    trip.log("system", "send", "message_sent", draft_id=draft.draft_id, vendor_id=draft.vendor_id, service=draft.service_id,
             kind=draft.kind, channel=channel, approved_by=approved_by)
    return channel
