"""Deterministic generator for Msafara's SYNTHETIC knowledge base.

Everything here is fictional: vendor names, emails (.example), phone numbers
(+1555 01xx), prices. Run `python -m msafara.seed_kb` to regenerate the JSON
files in msafara/data/. Same seed -> byte-identical output.

Data model (one JSON file per RAG document type):
  vendors.json          profiles (also hold floor_rate, the operator's walk-away
                        price on file, and flags: preferred / blacklisted)
  rate_cards.json       published rate per unit, floor rate, tax note
  past_quotes.json      3 prior quotes per vendor (anchor = their median)
  negotiation_logs.json prose logs of earlier negotiations (about half the vendors)
  policies.json         one operator policy document (+ structured numbers)
"""
import json
import math
import random
from pathlib import Path

SEED = 20261012
OUT = Path(__file__).parent / "data"

CITIES = {
    "Jaipur":   dict(cur="INR", hotel={3: 3500, 4: 7500, 5: 16000}, transport=4500, activity=1800,
                     areas=["Civil Lines", "C-Scheme", "Amer Road", "MI Road"],
                     acts=["Amer Fort guided tour", "Old City heritage walk", "Block-printing workshop", "Elephant-free village safari"]),
    "Goa":      dict(cur="INR", hotel={3: 4000, 4: 8500, 5: 18000}, transport=5000, activity=2500,
                     areas=["Calangute", "Candolim", "Panjim", "Palolem"],
                     acts=["Spice plantation tour", "Dolphin cruise", "Fontainhas heritage walk", "Beach kayaking"]),
    "Zanzibar": dict(cur="USD", hotel={3: 70, 4: 140, 5: 320}, transport=90, activity=60,
                     areas=["Stone Town", "Nungwi", "Kendwa", "Paje"],
                     acts=["Spice farm tour", "Prison Island trip", "Dhow sunset cruise", "Jozani forest visit"]),
    "Arusha":   dict(cur="USD", hotel={3: 60, 4: 130, 5: 280}, transport=150, activity=120,
                     areas=["Njiro", "Sakina", "Usa River", "Kisongo"],
                     acts=["Arusha National Park drive", "Maasai boma visit", "Coffee plantation tour", "Lake Duluti canoe"]),
}
PREFIX = ["Amber", "Coral", "Saffron", "Baobab", "Lotus", "Mirage", "Cedar", "Indigo", "Sable", "Pearl",
          "Ember", "Jade", "Monsoon", "Dune", "Opal", "Tamarind", "Zephyr", "Marigold", "Azure", "Kestrel",
          "Lagoon", "Hibiscus", "Ridge", "Sundial", "Harbour", "Orchid", "Granite", "Willow", "Cobalt", "Meadow"]
HOTEL_SUFFIX = {3: ["Residency", "Inn", "Lodge", "Stay"], 4: ["Suites", "Grand", "Retreat", "Courtyard"],
                5: ["Palace", "Resort & Spa", "Royale", "Collection"]}
TRANSPORT_SUFFIX = ["Cabs", "Coaches", "Transfers", "Fleet"]
ACTIVITY_SUFFIX = ["Experiences", "Trails", "Expeditions", "Journeys"]
PROFILE_LINES = [
    "Known for punctual check-ins and a dedicated groups desk.",
    "Responds to quote requests within a few hours on weekdays.",
    "Strong with corporate and incentive groups; flexible on rooming lists.",
    "Mostly leisure clients; peak-season rates apply in December and January.",
    "Family-run; the owner approves discounts personally.",
    "Offers complimentary airport pick-up on group bookings of 10 or more rooms.",
]


def _round_rate(x, cur):
    return int(round(x / 50.0) * 50) if cur == "INR" else int(round(x / 5.0) * 5)


def build():
    rng = random.Random(SEED)
    vendors, rate_cards, past_quotes, neg_logs = [], [], [], []
    names_used = set()
    vid = 0

    def uname(prefix_pool, suffixes, city):
        for _ in range(100):
            n = f"{rng.choice(prefix_pool)} {rng.choice(suffixes)}"
            if (n, city) not in names_used:
                names_used.add((n, city)); return n
        raise RuntimeError("name pool exhausted")

    for city, c in CITIES.items():
        cur = c["cur"]
        groups = [("hotel", s) for s in (3, 4, 5)] + [("transport", None), ("activity", None)]
        for category, star in groups:
            n = 4
            # exactly one disqualifier in some groups: tiny capacity or blacklisted
            disq_idx = rng.randrange(n) if rng.random() < 0.5 else None
            disq_kind = rng.choice(["capacity", "blacklist"])
            for i in range(n):
                vid += 1
                v_id = f"V{vid:03d}"
                if category == "hotel":
                    base = c["hotel"][star]
                    name = uname(PREFIX, HOTEL_SUFFIX[star], city)
                    unit = "per room per night"
                    capacity = rng.randrange(24, 61)
                elif category == "transport":
                    base = c["transport"]
                    name = uname(PREFIX, TRANSPORT_SUFFIX, city)
                    unit = "per vehicle per day"
                    capacity = rng.choice([35, 40, 45, 50])
                else:
                    base = c["activity"]
                    name = uname(PREFIX, ACTIVITY_SUFFIX, city)
                    unit = "per person"
                    capacity = rng.randrange(30, 81)
                blacklisted = False
                if i == disq_idx:
                    if disq_kind == "capacity":
                        capacity = rng.choice([4, 6, 8])
                    else:
                        blacklisted = True
                rate = _round_rate(base * rng.uniform(0.92, 1.08), cur)
                floor = _round_rate(rate * 0.85, cur)
                reliability = round(rng.uniform(0.62, 0.98), 2)
                preferred = (not blacklisted) and rng.random() < 0.25
                area = rng.choice(c["areas"])
                slug = name.lower().replace(" & ", "-").replace(" ", "-")
                email = f"groups@{slug}.example"
                phone = f"+1555010{rng.randrange(1000, 9999)}"
                desc = {
                    "hotel": f"{star}-star hotel in {area}, {city}. {capacity} rooms available for groups.",
                    "transport": f"Ground transport operator in {city}: air-conditioned vehicles seating up to {capacity} guests, driver included.",
                    "activity": f"Local experiences operator in {city} running '{rng.choice(c['acts'])}' and similar day activities for groups up to {capacity}.",
                }[category]
                vendors.append(dict(
                    vendor_id=v_id, name=name, city=city, category=category, star=star, area=area,
                    capacity=capacity, currency=cur, unit=unit, reliability=reliability,
                    preferred=preferred, blacklisted=blacklisted, floor_rate=floor,
                    email=email, phone=phone,
                    description=desc + " " + rng.choice(PROFILE_LINES)))
                rate_cards.append(dict(
                    vendor_id=v_id, city=city, category=category, star=star, currency=cur, unit=unit,
                    rate=rate, floor_rate=floor,
                    tax_note=rng.choice(["Published rate is inclusive of all taxes.",
                                         "Published rate is inclusive of all taxes and service charge."]),
                    validity="Rates valid for travel dates within the next 12 months; peak surcharge 10% on 20 Dec - 5 Jan."))
                qs = []
                for k in range(3):
                    q = _round_rate(rate * rng.uniform(0.88, 1.0), cur)
                    qs.append(q)
                    past_quotes.append(dict(
                        quote_id=f"{v_id}-Q{k+1}", vendor_id=v_id, city=city, category=category, star=star,
                        currency=cur, unit=unit, rate=q,
                        date=f"2026-{rng.randrange(1, 10):02d}-{rng.randrange(1, 28):02d}",
                        pax=rng.choice([8, 12, 16, 20, 24]),
                        note=rng.choice(["All-inclusive of taxes.", "Inclusive of taxes; payment 50% advance.",
                                         "Inclusive of taxes; free cancellation 14 days prior."])))
                if rng.random() < 0.5:
                    first = _round_rate(rate * rng.uniform(1.05, 1.15), cur)
                    pct = rng.choice([4, 5, 6, 8])
                    settled = _round_rate(first * (1 - pct / 100.0), cur)
                    neg_logs.append(dict(
                        log_id=f"{v_id}-N1", vendor_id=v_id, city=city, category=category, star=star,
                        currency=cur, first_quote=first, settled=settled, rounds=rng.choice([1, 2]),
                        text=(f"Earlier negotiation with {name}: opened at {cur} {first:,} {unit}. "
                              f"One counter was sent; the vendor settled at {cur} {settled:,} "
                              f"({pct}% below the opening quote) and held the rate for 48 hours. "
                              f"{rng.choice(['Replied same day.', 'Needed a reminder after 24 hours.', 'Owner approval was required.'])}")))
    policies = dict(
        policy_id="POL-001",
        markup_pct=12,
        max_discount_pct=10,
        max_rounds=2,
        discount_schedule=[10, 5],
        over_anchor_tolerance_pct=10,
        confidence_threshold=0.6,
        preferred_vendor_ids=[v["vendor_id"] for v in vendors if v["preferred"]],
        blacklist_vendor_ids=[v["vendor_id"] for v in vendors if v["blacklisted"]],
        sections=[
            dict(title="Markup", text="Operator markup on vendor net rates is 12% on hotels, transport and activities. Never disclose markup or floor rates to vendors."),
            dict(title="Negotiation limits", text="Counter-offers may be at most 10% below the vendor quote and never below the floor rate on file. At most 2 negotiation rounds per service. Anything outside these bounds is escalated to a human."),
            dict(title="Cancellation terms", text="Operator standard: free cancellation up to 14 days before arrival, 50% charge 7 to 13 days before, 100% inside 7 days. Ask vendors to match or improve on these terms."),
            dict(title="Preferred vendors", text="Preferred vendors are contacted first and get a small scoring boost. They are flagged in each vendor profile."),
            dict(title="Blacklist", text="Blacklisted vendors must never be contacted or selected, regardless of price."),
            dict(title="Approvals", text="No message is sent to a vendor and no quote is accepted without operator approval. No payments are made by the system."),
        ])
    return dict(vendors=vendors, rate_cards=rate_cards, past_quotes=past_quotes,
                negotiation_logs=neg_logs, policies=policies)


def main():
    OUT.mkdir(exist_ok=True)
    data = build()
    for k, v in data.items():
        (OUT / f"{k}.json").write_text(json.dumps(v, indent=1, ensure_ascii=False) + "\n")
    print({k: len(v) if isinstance(v, list) else "doc" for k, v in data.items()})


if __name__ == "__main__":
    main()
