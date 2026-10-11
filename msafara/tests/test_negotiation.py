import random

import pytest

from msafara import agents as A
from msafara.models import Anchor, Candidate, CounterRecord, Policy, Quote, ServiceLine

POL = Policy()


def svc_with(q_rate, anchor, floor, rate_card=None, rnd=0, conf=0.92, accepts=False, status="quote"):
    s = ServiceLine(service_id="hotel", category="hotel", spec="x", units=10, unit_label="u", min_capacity=1,
                    candidates=[Candidate(vendor_id="V1", name="Alpha", score=1, why="")],
                    anchors={"V1": Anchor(vendor_id="V1", anchor_rate=anchor, rate_card_rate=rate_card or anchor * 1.05,
                                          floor_rate=floor, currency="INR")})
    q = Quote(vendor_id="V1", status=status, unit_rate=q_rate, currency="INR", confidence=conf, accepts_counter=accepts, round=rnd)
    s.quotes["V1"] = q; s.quote_history.append(q); s.negotiation_round = rnd
    if rnd:
        s.counters = [CounterRecord(round=i, vendor_id="V1", quote_rate=q_rate, discount_pct=5, counter_rate=q_rate * .95, floor_rate=floor) for i in range(rnd)]
    return s


def test_counter_never_below_floor_or_more_than_10pct_off_property():
    rng = random.Random(1)
    for _ in range(5000):
        quote = rng.uniform(50, 20000); floor = quote * rng.uniform(0.5, 1.2); d = rng.uniform(0, 40)
        c = A.compute_counter(quote, floor, d)
        assert c >= floor - 1e-9
        assert c >= quote * 0.9 - 1e-9                       # hard 10% cap even if asked for 40%
        assert c <= max(quote, floor) + 1               # ceil adds < 1 unit


@pytest.mark.parametrize("counter,quote,floor,rounds,ok", [
    (9000, 10000, 8000, 0, True), (8900, 10000, 8000, 0, False),   # >10% off
    (7900, 10000, 8000, 0, False),                                 # below floor
    (9000, 10000, 8000, 2, False),                                 # round cap
    (10000, 10000, 8000, 0, False),                                # not a concession
])
def test_assert_counter_bounds(counter, quote, floor, rounds, ok):
    if ok:
        A.assert_counter_bounds(counter, quote, floor, POL, rounds)
    else:
        with pytest.raises(A.BoundViolation):
            A.assert_counter_bounds(counter, quote, floor, POL, rounds)


def test_policy_cannot_loosen_hard_ceiling():
    p = Policy(max_discount_pct=50, discount_schedule=[40, 30], max_rounds=9)
    assert p.discount_for_round(0) == 10 and p.rounds_cap == 2
    with pytest.raises(A.BoundViolation):
        A.assert_counter_bounds(5000, 10000, 100, p, 0)


def test_accept_when_at_or_below_anchor():
    s = svc_with(7000, 7200, 6000); A.decide_service(s, POL)
    assert s.decision == "accept" and s.agreed_rate == 7000 and "anchor" in s.reasoning


def test_counter_when_above_anchor_and_round_available():
    s = svc_with(8000, 7200, 6000); A.decide_service(s, POL)
    assert s.decision == "counter" and s.status == "negotiating" and "round 1 at INR 7,200" in s.reasoning


def test_second_round_uses_5pct():
    s = svc_with(8000, 7200, 6000, rnd=1); A.decide_service(s, POL)
    assert s.decision == "counter" and "INR 7,600" in s.reasoning


def test_counter_respects_floor():
    s = svc_with(8000, 7200, 7900); A.decide_service(s, POL)
    assert s.decision == "counter" and "INR 7,900" in s.reasoning       # floor 7,900 > 8,000*0.9


def test_nothing_to_negotiate_when_quote_at_floor():
    s = svc_with(8000, 7200, 8000); A.decide_service(s, POL)
    assert s.decision == "accept"


def test_after_max_rounds_accept_within_tolerance_else_escalate():
    near = svc_with(7700, 7200, 6000, rnd=2); A.decide_service(near, POL)
    assert near.decision == "accept" and "tolerance" in near.reasoning
    far = svc_with(8784, 7200, 6000, rnd=2); A.decide_service(far, POL)
    assert far.decision == "escalate" and far.escalation.code == "over_anchor" and "22% above anchor" in far.escalation.reason


def test_low_confidence_escalates():
    s = svc_with(7000, 7200, 6000, conf=0.5); A.decide_service(s, POL)
    assert s.decision == "escalate" and s.escalation.code == "low_confidence"


def test_ambiguous_reply_to_counter_escalates_even_if_other_vendor_ok():
    s = svc_with(8000, 7200, 6000, rnd=1)
    s.quote_history.append(Quote(vendor_id="V1", status="quote", unit_rate=8000, currency="INR", confidence=.9, round=0))
    s.quotes["V1"] = Quote(vendor_id="V1", status="ambiguous", confidence=.4, round=1, summary="vague")
    A.decide_service(s, POL)
    assert s.decision == "escalate" and s.escalation.code == "counter_reply_ambiguous"


def test_no_valid_quotes_reasons():
    for status, code in [("ambiguous", "ambiguous_reply"), ("partial", "partial_availability"), ("tax_unclear", "tax_unclear"),
                         ("decline", "all_declined"), ("no_response", "no_response")]:
        s = svc_with(None, 7200, 6000, status=status); A.decide_service(s, POL)
        assert s.escalation.code == code, status
