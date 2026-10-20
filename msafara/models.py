"""Pydantic models. TripState is the single shared state object passed through the graph."""
from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

HARD_MAX_DISCOUNT_PCT = 10.0   # code-level ceiling; policy may only tighten it
HARD_MAX_ROUNDS = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TripBrief(BaseModel):
    city: Optional[str] = None
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    nights: Optional[int] = None
    pax: Optional[int] = None
    hotel_star: Optional[int] = None
    budget_total: Optional[float] = None
    currency: Optional[str] = None
    notes: str = ""
    confidence: float = 0.0
    missing: list[str] = Field(default_factory=list)


class Policy(BaseModel):
    markup_pct: float = 12
    max_discount_pct: float = 10
    max_rounds: int = 2
    discount_schedule: list[float] = [10, 5]
    over_anchor_tolerance_pct: float = 10
    confidence_threshold: float = 0.6
    preferred_vendor_ids: list[str] = []
    blacklist_vendor_ids: list[str] = []

    def discount_for_round(self, rnd: int) -> float:
        """Discount for 0-based negotiation round, clamped by the hard code ceiling."""
        sched = self.discount_schedule or [self.max_discount_pct]
        d = sched[min(rnd, len(sched) - 1)]
        return max(0.0, min(d, self.max_discount_pct, HARD_MAX_DISCOUNT_PCT))

    @property
    def rounds_cap(self) -> int:
        return max(0, min(self.max_rounds, HARD_MAX_ROUNDS))


class Anchor(BaseModel):
    vendor_id: str
    anchor_rate: Optional[float] = None      # median of retrieved past quotes
    rate_card_rate: Optional[float] = None
    floor_rate: Optional[float] = None       # internal - never put in a vendor message
    currency: str = ""
    n_quotes: int = 0
    sources: list[str] = Field(default_factory=list)   # retrieved chunk ids
    history_note: str = ""                    # from past negotiation logs


class Candidate(BaseModel):
    vendor_id: str
    name: str
    score: float
    why: str
    preferred: bool = False


class Quote(BaseModel):
    vendor_id: str
    status: Literal["quote", "decline", "partial", "ambiguous", "tax_unclear", "no_response"]
    unit_rate: Optional[float] = None        # tax-inclusive, per service unit
    currency: str = ""
    tax_inclusive: Optional[bool] = None
    available_units: Optional[int] = None
    accepts_counter: bool = False
    confidence: float = 0.0
    summary: str = ""
    raw_text: str = ""
    round: int = 0
    flags: list[str] = Field(default_factory=list)   # e.g. suspicious_instructions


class OutreachDraft(BaseModel):
    draft_id: str
    vendor_id: str
    service_id: str
    kind: Literal["outreach", "counter", "confirm"]
    subject: str
    body: str
    round: int = 0
    counter_rate: Optional[float] = None
    grounding: list[str] = Field(default_factory=list)
    status: Literal["draft", "approved", "rejected", "sent"] = "draft"
    edited: bool = False
    generated_by: str = "template"           # template | llm | template(fallback)
    replied: bool = False


class CounterRecord(BaseModel):
    round: int
    vendor_id: str
    quote_rate: float
    discount_pct: float
    counter_rate: float
    floor_rate: float
    operator_set: bool = False


class Escalation(BaseModel):
    reason: str
    code: str
    options: list[str] = Field(default_factory=list)


class ServiceLine(BaseModel):
    service_id: str
    category: Literal["hotel", "transport", "activity"]
    spec: str
    units: int                                # total billable units (e.g. rooms x nights)
    unit_label: str
    min_capacity: int
    status: Literal["planned", "requested", "quoted", "negotiating", "accepted", "confirmed", "escalated", "dropped"] = "planned"
    candidates: list[Candidate] = Field(default_factory=list)
    anchors: dict[str, Anchor] = Field(default_factory=dict)
    quotes: dict[str, Quote] = Field(default_factory=dict)         # latest per vendor
    quote_history: list[Quote] = Field(default_factory=list)
    counters: list[CounterRecord] = Field(default_factory=list)
    negotiation_round: int = 0                                      # counters SENT so far
    decision: Optional[Literal["accept", "counter", "escalate"]] = None
    selected_vendor_id: Optional[str] = None
    agreed_rate: Optional[float] = None
    reasoning: str = ""
    confidence: Optional[float] = None
    escalation: Optional[Escalation] = None
    operator_counter_rate: Optional[float] = None
    decided_by: str = "rules"                  # rules | llm | rules(fallback)
    llm_counter_rate: Optional[float] = None   # counter amount chosen by the negotiation agent (bounds re-checked)
    llm_counter_body: str = ""


class AuditEvent(BaseModel):
    ts: str
    actor: str          # system | agent:<name> | human
    node: str
    event: str
    detail: dict = Field(default_factory=dict)


class TripState(BaseModel):
    trip_id: str
    request_text: str
    created_at: str = Field(default_factory=now_iso)
    status: Literal["intake", "matching", "awaiting_approval", "waiting_vendors", "negotiating",
                    "escalated", "awaiting_final", "confirmed", "rejected", "failed"] = "intake"
    brief: Optional[TripBrief] = None
    services: list[ServiceLine] = Field(default_factory=list)
    drafts: list[OutreachDraft] = Field(default_factory=list)
    negotiation_round: int = 0                 # max rounds used across services
    policy: Policy = Field(default_factory=Policy)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    llm_backend: str = "mock"
    simulate_profile: Optional[dict] = None    # demo/eval only: vendor behaviour per category
    audit: list[AuditEvent] = Field(default_factory=list)
    gate: Optional[dict] = None                # description of the gate currently open
    outcome: dict = Field(default_factory=dict)

    def log(self, actor: str, node: str, event: str, **detail):
        self.audit.append(AuditEvent(ts=now_iso(), actor=actor, node=node, event=event, detail=detail))

    def svc(self, service_id: str) -> ServiceLine:
        for s in self.services:
            if s.service_id == service_id:
                return s
        raise KeyError(service_id)

    def draft(self, draft_id: str) -> OutreachDraft:
        for d in self.drafts:
            if d.draft_id == draft_id:
                return d
        raise KeyError(draft_id)
