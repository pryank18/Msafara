"""Simulated vendors for demos and evals.

Stateless and deterministic: the reply depends only on (profile, vendor, our message).
Profiles describe *behaviour*, e.g. 'firm_high' quotes ~22% above the vendor's own past-quote
anchor and refuses to move. The simulator knows the ground truth (kb.truth_anchor); the agents
under test never see it, they only get the reply text.
"""
import math
from typing import Optional

from . import kb
from .textutil import ceil_money, fmt_money

PROFILES = ["clean", "negotiable", "two_round", "firm_slightly_high", "firm_high", "mixed_one_ambiguous",
            "ambiguous_all", "partial_all", "declines_all", "silent_all", "tax_unclear_all"]
ESCALATING = {"firm_high", "ambiguous_all", "partial_all", "declines_all", "silent_all", "tax_unclear_all"}

_MULT = {"clean": 0.97, "negotiable": 1.12, "two_round": 1.18, "firm_slightly_high": 1.07, "firm_high": 1.22,
         "mixed_one_ambiguous": 0.97, "tax_unclear_all": 1.0, "partial_all": 1.0}
_UNIT = {"hotel": "per room per night", "transport": "per vehicle per day", "activity": "per person"}
_TAX = {"INR": 12, "USD": 18}


def _round(x, cur):
    return float(ceil_money(x))


def base_quote(profile: str, rank: int, A: float, cur: str) -> float:
    return _round(A * (_MULT.get(profile, 1.0) + 0.01 * rank), cur)


def _style_text(q: float, cur: str, unit: str, rank: int, qty_note: str) -> tuple[str, float]:
    """Three phrasing styles; style 1 quotes tax-exclusive (+GST/VAT). Returns (text, effective_inclusive_rate)."""
    style = rank % 3
    if style == 1:
        g = _TAX[cur]
        excl = math.floor(q / (1 + g / 100))
        eff = float(ceil_money(excl * (1 + g / 100)))
        return (f"Hello, thank you for your enquiry. {qty_note} Our rate is {fmt_money(excl, cur)} {unit} plus {g}% "
                f"{'GST' if cur == 'INR' else 'VAT'}. Rate held for 48 hours."), eff
    if style == 2:
        return (f"Dear team, we confirm availability. {qty_note} Best rate: {fmt_money(q, cur)} {unit}, taxes included. "
                f"Please let us know how you wish to proceed."), q
    return (f"Hi, thanks for reaching out. {qty_note} We can offer {fmt_money(q, cur)} {unit}, inclusive of all taxes."), q


def effective_quote(profile: str, rank: int, A: float, cur: str) -> float:
    """What the agent should end up with after tax normalisation (used by tests/metrics)."""
    q = base_quote(profile, rank, A, cur)
    if rank % 3 == 1:
        g = _TAX[cur]
        return float(ceil_money(math.floor(q / (1 + g / 100)) * (1 + g / 100)))
    return q


def reply_for(profile: str, category: str, rank: int, vendor_id: str, needed_qty: int,
              counter: Optional[float], counter_round: int) -> Optional[str]:
    t = kb.truth_anchor(vendor_id)
    A, cur = t["anchor"], t["currency"]
    unit = _UNIT[category]
    if profile == "silent_all":
        return None
    if profile == "declines_all":
        return "Unfortunately we are fully booked for those dates and cannot take this group."
    if profile == "ambiguous_all" or (profile == "mixed_one_ambiguous" and rank == 0):
        return ["Thanks for your message. Let me check with the manager and revert.",
                "Probably possible, but it depends on the season. Will get back to you."][rank % 2]
    q0 = base_quote(profile, rank, A, cur)
    if profile == "partial_all":
        have = max(1, needed_qty // 2)
        word = {"hotel": "rooms", "transport": "seats", "activity": "spots"}[category]
        return (f"We only have {have} {word} available for those dates, not the full quantity. "
                f"Rate would be {fmt_money(q0, cur)} {unit}, inclusive of all taxes.")
    if profile == "tax_unclear_all":
        return f"Thanks. Our rate is {fmt_money(q0, cur)} {unit}. Availability is confirmed."
    if counter is None:                                   # first reply to the outreach
        text, _ = _style_text(q0, cur, unit, rank, "Availability is confirmed for the full quantity.")
        return text
    # reply to a counter-offer
    if profile in ("negotiable", "clean", "mixed_one_ambiguous"):
        return f"Okay, we can accept your proposed rate of {fmt_money(counter, cur)} {unit}, inclusive of all taxes."
    if profile == "two_round":
        if counter_round == 0:
            q1 = float(ceil_money(q0 * 0.95))
            return f"We can come down a little: {fmt_money(q1, cur)} {unit}, taxes included. That is our revised offer."
        return f"Alright, agreed at {fmt_money(counter, cur)} {unit}, all taxes included."
    # firm profiles
    return f"Sorry, {fmt_money(q0, cur)} {unit} is our final rate, inclusive of all taxes. We cannot go lower."


def make_provider():
    """Reply provider for graph.set_reply_provider()."""
    def provider(trip, svc, draft, vendor):
        prof = (trip.simulate_profile or {}).get(svc.category, "clean")
        rank = [c.vendor_id for c in svc.candidates].index(draft.vendor_id)
        return reply_for(prof, svc.category, rank, draft.vendor_id, svc.min_capacity,
                         draft.counter_rate if draft.kind == "counter" else None, draft.round)
    return provider


def expected_parse(profile: str, category: str, rank: int, vendor_id: str, kind: str, counter: Optional[float],
                   counter_round: int) -> tuple[str, Optional[float]]:
    """(status, unit_rate) a correct parser should return for reply_for(...) - ground truth for the parse metric."""
    t = kb.truth_anchor(vendor_id)
    A, cur = t["anchor"], t["currency"]
    if profile == "silent_all":
        return "no_response", None
    if profile == "declines_all":
        return "decline", None
    if profile == "ambiguous_all" or (profile == "mixed_one_ambiguous" and rank == 0):
        return "ambiguous", None
    q0 = base_quote(profile, rank, A, cur)
    if profile == "partial_all":
        return "partial", q0
    if profile == "tax_unclear_all":
        return "tax_unclear", q0
    if kind == "outreach":
        return "quote", effective_quote(profile, rank, A, cur)
    if profile in ("negotiable", "clean", "mixed_one_ambiguous"):
        return "quote", counter
    if profile == "two_round":
        return ("quote", float(ceil_money(q0 * 0.95))) if counter_round == 0 else ("quote", counter)
    return "quote", q0
