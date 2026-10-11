import json

from msafara.tests.conftest import Stub
from msafara import agents as A, llm
from msafara.models import Anchor, ServiceLine, TripState

CASES = [
    ("Hi, we can offer INR 7,100 per room per night, inclusive of all taxes.", "quote", 7100),
    ("Our rate is INR 6,200 per room per night plus 12% GST. Availability confirmed.", "quote", 6944),
    ("Best rate: $135 per room per night, taxes included.", "quote", 135),
    ("Our rate is INR 7,100 per room per night. Availability is confirmed.", "tax_unclear", 7100),
    ("Unfortunately we are fully booked for those dates.", "decline", None),
    ("Thanks, let me check with the manager and revert.", "ambiguous", None),
    ("Probably around INR 7,000 but it depends on the season.", "ambiguous", None),
    ("We only have 3 rooms available. Rate INR 7,000 incl. taxes.", "partial", 7000),
    ("We offer INR 7,000 or INR 7,500 depending on view, all taxes included.", "ambiguous", None),
]


def test_mock_parser_variants():
    for text, status, rate in CASES:
        pq = A.mock_parse_quote(text, 6, None, "INR")
        assert pq.status == status, text
        assert pq.unit_rate == rate or (rate is None and pq.unit_rate is None), text


def test_accepts_counter_detection():
    pq = A.mock_parse_quote("Okay, we can accept your proposed rate of INR 6,900 per room, inclusive of all taxes.", 6, 6900, "INR")
    assert pq.accepts_counter and pq.unit_rate == 6900
    pq = A.mock_parse_quote("Sorry, INR 7,400 is our final rate, inclusive of all taxes. We cannot go lower.", 6, 6900, "INR")
    assert not pq.accepts_counter and pq.unit_rate == 7400


def _ctx():
    svc = ServiceLine(service_id="hotel", category="hotel", spec="x", units=6, unit_label="u", min_capacity=3,
                      anchors={"V001": Anchor(vendor_id="V001", currency="INR", anchor_rate=1000, floor_rate=800)})
    return TripState(trip_id="t", request_text=""), svc


def test_llm_parse_rejects_invented_rate():
    t, svc = _ctx()
    llm.set_client(Stub(lambda s, u, n: json.dumps(dict(status="quote", unit_rate=999, currency="INR", tax_inclusive=True, confidence=0.95, summary="x"))))
    q = A.parse_quote(t, svc, "V001", "We can do INR 7,100 per room, taxes included.", 0, None)
    assert q.status == "ambiguous" and q.unit_rate is None
    assert any(a.event == "llm_rate_not_in_reply_downgraded" for a in t.audit)


def test_llm_parse_accepts_grounded_rate_and_gst_math():
    t, svc = _ctx()
    llm.set_client(Stub(lambda s, u, n: json.dumps(dict(status="quote", unit_rate=1120, currency="INR", tax_inclusive=False, confidence=0.9, summary="gst"))))
    q = A.parse_quote(t, svc, "V001", "Our rate is INR 1,000 per room plus 12% GST.", 0, None)
    assert q.status == "quote" and q.unit_rate == 1120


def test_llm_garbage_output_becomes_ambiguous_after_retry():
    t, svc = _ctx()
    stub = Stub(lambda s, u, n: "I think the rate is fine!")
    llm.set_client(stub)
    q = A.parse_quote(t, svc, "V001", "INR 1000 incl taxes", 0, None)
    assert q.status == "ambiguous" and len(stub.calls) == 2          # one retry
    assert any(a.event == "llm_failed_mark_ambiguous" for a in t.audit)


def test_structured_call_handles_fences_retry_and_bad_enum():
    from pydantic import BaseModel
    class M(BaseModel):
        a: int
    seq = ["not json", "```json\n{\"a\": \"x\"}\n```", "Sure! ```json\n{\"a\": 3}\n```"]
    llm.set_client(Stub(lambda s, u, n: seq[n - 1]))
    assert llm.structured_call("intake", "q", M, retries=2).a == 3
    llm.set_client(Stub(lambda s, u, n: "nope"))
    import pytest
    with pytest.raises(llm.LLMOutputError):
        llm.structured_call("intake", "q", M)


def test_extract_json_nested_and_strings():
    assert llm.extract_json('x {"a": {"b": "}"}, "c": 1} y') == {"a": {"b": "}"}, "c": 1}


def test_prompt_files_versioned_and_env_override(monkeypatch):
    assert set(llm.available_versions("intake")) >= {"v1", "v2"}
    monkeypatch.setenv("MSAFARA_PROMPT_INTAKE", "v1")
    text, ver = llm.load_prompt("intake")
    assert ver == "v1" and "missing" in text
    monkeypatch.delenv("MSAFARA_PROMPT_INTAKE")
    assert llm.load_prompt("intake")[1] == llm.active_versions()["intake"]
