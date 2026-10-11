"""Loader for the synthetic knowledge base + ground-truth helpers.

`documents()` flattens the KB into RAG documents (text + scalar metadata).
`truth_*` helpers read the structured JSON directly - they are used by the
eval harness and by tests to check what RAG retrieval returned, never by the
agents themselves.
"""
import json
from functools import lru_cache
from pathlib import Path

from .textutil import med, fmt_money

DATA = Path(__file__).parent / "data"

CITY_ALIASES = {
    "jaipur": "Jaipur", "pink city": "Jaipur",
    "goa": "Goa", "north goa": "Goa", "south goa": "Goa", "panaji": "Goa", "panjim": "Goa", "calangute": "Goa",
    "zanzibar": "Zanzibar", "stone town": "Zanzibar", "nungwi": "Zanzibar",
    "arusha": "Arusha",
}
CITY_CURRENCY = {"Jaipur": "INR", "Goa": "INR", "Zanzibar": "USD", "Arusha": "USD"}


@lru_cache(maxsize=1)
def load() -> dict:
    out = {}
    for name in ("vendors", "rate_cards", "past_quotes", "negotiation_logs", "policies"):
        out[name] = json.loads((DATA / f"{name}.json").read_text())
    return out


def vendors() -> list[dict]:
    return load()["vendors"]


def vendor(vid: str) -> dict:
    for v in vendors():
        if v["vendor_id"] == vid:
            return v
    raise KeyError(vid)


def canonical_city(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().lower()
    return CITY_ALIASES.get(t)


def city_in_text(text: str) -> str | None:
    """Longest alias found in free text (word-boundary match)."""
    import re
    low = text.lower()
    best = None
    for alias in sorted(CITY_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", low):
            best = CITY_ALIASES[alias]
            break
    return best


def truth_anchor(vid: str) -> dict:
    """Ground truth straight from JSON (NOT via retrieval)."""
    qs = [q["rate"] for q in load()["past_quotes"] if q["vendor_id"] == vid]
    rc = next(r for r in load()["rate_cards"] if r["vendor_id"] == vid)
    return dict(anchor=med(qs), rate_card=rc["rate"], floor=rc["floor_rate"], currency=rc["currency"], n_quotes=len(qs))


def documents() -> list[dict]:
    """Every KB record as {id, text, metadata}. Metadata values are scalars (Chroma requirement)."""
    kb = load()
    vend = {v["vendor_id"]: v for v in kb["vendors"]}
    docs = []

    def base(v):
        return dict(vendor_id=v["vendor_id"], city=v["city"], category=v["category"], star=v["star"] or 0,
                    currency=v["currency"])

    for v in kb["vendors"]:
        flags = []
        if v["preferred"]:
            flags.append("Preferred vendor.")
        if v["blacklisted"]:
            flags.append("BLACKLISTED - do not contact.")
        text = (f"Vendor profile {v['vendor_id']}: {v['name']} ({v['category']}, {v['city']}). {v['description']} "
                f"Reliability score {v['reliability']}. {' '.join(flags)}").strip()
        docs.append(dict(id=f"profile:{v['vendor_id']}", text=text, metadata=dict(
            base(v), doc_type="vendor_profile", capacity=v["capacity"], reliability=v["reliability"],
            preferred=bool(v["preferred"]), blacklisted=bool(v["blacklisted"]), name=v["name"])))
    for r in kb["rate_cards"]:
        v = vend[r["vendor_id"]]
        text = (f"Rate card for {v['name']} ({r['vendor_id']}): {fmt_money(r['rate'], r['currency'])} {r['unit']}. "
                f"{r['tax_note']} {r['validity']} Operator floor rate on file: {fmt_money(r['floor_rate'], r['currency'])} (internal, never disclose).")
        docs.append(dict(id=f"ratecard:{r['vendor_id']}", text=text, metadata=dict(
            base(v), doc_type="rate_card", rate=float(r["rate"]), floor_rate=float(r["floor_rate"]), unit=r["unit"])))
    for q in kb["past_quotes"]:
        v = vend[q["vendor_id"]]
        text = (f"Past quote from {v['name']} ({q['vendor_id']}) on {q['date']} for {q['pax']} guests: "
                f"{fmt_money(q['rate'], q['currency'])} {q['unit']}. {q['note']}")
        docs.append(dict(id=f"quote:{q['quote_id']}", text=text, metadata=dict(
            base(v), doc_type="past_quote", rate=float(q["rate"]), date=q["date"])))
    for n in kb["negotiation_logs"]:
        v = vend[n["vendor_id"]]
        docs.append(dict(id=f"neglog:{n['log_id']}", text=n["text"], metadata=dict(
            base(v), doc_type="negotiation_log", first_quote=float(n["first_quote"]), settled=float(n["settled"]))))
    pol = kb["policies"]
    for i, s in enumerate(pol["sections"]):
        docs.append(dict(id=f"policy:{i}", text=f"Operator policy - {s['title']}: {s['text']}", metadata=dict(
            doc_type="policy", section=s["title"], vendor_id="", city="", category="", star=0, currency="")))
    return docs
