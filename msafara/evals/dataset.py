"""25 synthetic trip requests with known outcomes.

Each case: free-text request, ground-truth brief, per-category vendor behaviour profile (see
simulator.py) and whether a correct system must escalate to a human at some point.
Phrasing is deliberately varied: date formats, number words, lakh / k / Indian digit grouping,
currency symbols, city aliases, two intentionally incomplete requests.
"""
from datetime import date

from .. import simulator as S

D = date
C = "clean"


def case(cid, text, city, ci, co, pax, star, budget, cur, profile=None, incomplete=False):
    prof = {"hotel": C, "transport": C, "activity": C}
    if isinstance(profile, str):
        prof = {k: profile for k in prof}
    elif profile:
        prof.update(profile)
    esc = incomplete or any(p in S.ESCALATING for p in prof.values())
    return dict(id=cid, text=text, profile=prof, incomplete=incomplete, expected_escalation=esc,
                truth=dict(city=city, check_in=ci, check_out=co, nights=(co - ci).days if ci and co else None, pax=pax,
                           hotel_star=star, budget_total=budget, currency=cur))


CASES = [
    case("c01", "Need a 4-star in Jaipur for 12 people, 14-17 March 2027, budget around INR 3.2 lakh",
         "Jaipur", D(2027, 3, 14), D(2027, 3, 17), 12, 4, 320000.0, "INR"),
    case("c02", "We are a group of 20 looking at Goa from 2027-04-02 to 2027-04-06. 5-star hotel please. Budget ₹12,50,000.",
         "Goa", D(2027, 4, 2), D(2027, 4, 6), 20, 5, 1250000.0, "INR", {"hotel": "negotiable"}),
    case("c03", "Zanzibar trip: 8 pax, 3 star hotel, 10/05/2027 - 14/05/2027, budget $6,500",
         "Zanzibar", D(2027, 5, 10), D(2027, 5, 14), 8, 3, 6500.0, "USD"),
    case("c04", "Looking for a five star stay in Arusha for a party of eight, Jun 12 to 15, 2027. Budget USD 9000.",
         "Arusha", D(2027, 6, 12), D(2027, 6, 15), 8, 5, 9000.0, "USD", {"hotel": "two_round"}),
    case("c05", "Please arrange 4* accommodation in Jaipur for 16 guests from 3 Apr 2027 for 4 nights. Budget 4 lakh.",
         "Jaipur", D(2027, 4, 3), D(2027, 4, 7), 16, 4, 400000.0, "INR",
         {"transport": "negotiable", "activity": "firm_slightly_high"}),
    case("c06", "Goa, 6 travellers, 4 star, 22 Nov 2026 to 25 Nov 2026, no fixed budget",
         "Goa", D(2026, 11, 22), D(2026, 11, 25), 6, 4, None, None),
    case("c07", "Corporate offsite in Zanzibar, 24 people, 4-star, December 28 2026 - January 2 2027. Budget $18k.",
         "Zanzibar", D(2026, 12, 28), D(2027, 1, 2), 24, 4, 18000.0, "USD",
         {"hotel": "firm_slightly_high", "activity": "two_round"}),
    case("c08", "5 star hotel in Arusha for 10 adults, 2027-07-08 to 2027-07-12. $15,000 budget.",
         "Arusha", D(2027, 7, 8), D(2027, 7, 12), 10, 5, 15000.0, "USD", {"hotel": "firm_high"}),
    case("c09", "Jaipur wedding party of 30 people, 5-star, 5-8 February 2027, budget Rs 9 lakh",
         "Jaipur", D(2027, 2, 5), D(2027, 2, 8), 30, 5, 900000.0, "INR", "mixed_one_ambiguous"),
    case("c10", "Goa 3-star for four people from 2027-01-10 to 2027-01-13, INR 55,000 all in",
         "Goa", D(2027, 1, 10), D(2027, 1, 13), 4, 3, 55000.0, "INR", "negotiable"),
    case("c11", "Stone Town, Zanzibar for 14 guests, 4 star, 20/09/2027 to 24/09/2027, budget $10k",
         "Zanzibar", D(2027, 9, 20), D(2027, 9, 24), 14, 4, 10000.0, "USD", {"transport": "declines_all"}),
    case("c12", "Arusha 3-star, 6 pax, 1 Aug 2027 for 3 nights. Budget 2,500 dollars",
         "Arusha", D(2027, 8, 1), D(2027, 8, 4), 6, 3, 2500.0, "USD"),
    case("c13", "North Goa, group of 18, 4-star, Oct 5-9, 2027, budget 6 lakh",
         "Goa", D(2027, 10, 5), D(2027, 10, 9), 18, 4, 600000.0, "INR", {"activity": "partial_all"}),
    case("c14", "Need a hotel in Jaipur for 10 people on 12-15 May 2027, budget INR 2 lakh",
         "Jaipur", D(2027, 5, 12), D(2027, 5, 15), 10, None, 200000.0, "INR", incomplete=True),
    case("c15", "4-star in Goa for 12 people, budget ₹3 lakh, sometime in March",
         "Goa", None, None, 12, 4, 300000.0, "INR", incomplete=True),
    case("c16", "Group of 9, Jaipur, 5-star, 2027-11-20 to 2027-11-23, INR 7.5 lakh",
         "Jaipur", D(2027, 11, 20), D(2027, 11, 23), 9, 5, 750000.0, "INR", {"hotel": "tax_unclear_all"}),
    case("c17", "Zanzibar beach week: 10 people, 4 star, 7 Mar 2027 - 14 Mar 2027, budget $12,000",
         "Zanzibar", D(2027, 3, 7), D(2027, 3, 14), 10, 4, 12000.0, "USD"),
    case("c18", "Arusha lodge stay (4-star) for 6 travelers, 2027-02-14 to 2027-02-18, budget USD 7,200",
         "Arusha", D(2027, 2, 14), D(2027, 2, 18), 6, 4, 7200.0, "USD", {"activity": "ambiguous_all"}),
    case("c19", "Goa 5-star for 25 pax, 14-17 January 2027, ₹15 lakh budget",
         "Goa", D(2027, 1, 14), D(2027, 1, 17), 25, 5, 1500000.0, "INR", {"hotel": "two_round", "transport": "negotiable"}),
    case("c20", "Jaipur 3 star, 11 people, dates: 6/12/2026 - 9/12/2026. Rs. 1,80,000 budget",
         "Jaipur", D(2026, 12, 6), D(2026, 12, 9), 11, 3, 180000.0, "INR", {"activity": "silent_all"}),
    case("c21", "Zanzibar 3-star, 2 adults and 3 kids, 1-4 September 2027, $2,400",
         "Zanzibar", D(2027, 9, 1), D(2027, 9, 4), 5, 3, 2400.0, "USD", "negotiable"),
    case("c22", "Arusha, 5-star, group of 15, 15 Jan 2027 to 19 Jan 2027, budget USD 22k.",
         "Arusha", D(2027, 1, 15), D(2027, 1, 19), 15, 5, 22000.0, "USD", {"hotel": "firm_high"}),
    case("c23", "Goa 4-star for 7 guests from 2027-08-01 for 2 nights, INR 90,000",
         "Goa", D(2027, 8, 1), D(2027, 8, 3), 7, 4, 90000.0, "INR", "two_round"),
    case("c24", "Need 4 star Jaipur, pax 12, check in 2027-09-03, check out 2027-09-06, budget ₹3,00,000",
         "Jaipur", D(2027, 9, 3), D(2027, 9, 6), 12, 4, 300000.0, "INR"),
    case("c25", "Zanzibar Nungwi 5-star, 12 people, 20-24 October 2027, budget around USD 14,000",
         "Zanzibar", D(2027, 10, 20), D(2027, 10, 24), 12, 5, 14000.0, "USD",
         {"hotel": "mixed_one_ambiguous", "transport": "firm_slightly_high"}),
]
assert len(CASES) == 25 and len({c["id"] for c in CASES}) == 25


# ---------------------------------------------------------------------------------------------
# HELD-OUT intake phrasings. Written AFTER the mock parser was frozen and scored once, without tuning
# the parser to them; failures are reported, not fixed. Use these (not CASES) when judging whether
# intake generalises. Extraction-only: no vendor behaviour attached.
HELDOUT = [
    dict(id="h01", text="Hi! Can you source a 4 star property in Jaipur for our team of 14, arriving 8 Feb 2027 and leaving 11 Feb 2027? We can spend up to INR 5 lakh.",
         truth=dict(city="Jaipur", check_in=D(2027, 2, 8), check_out=D(2027, 2, 11), nights=3, pax=14, hotel_star=4, budget_total=500000.0, currency="INR")),
    dict(id="h02", text="Zanzibar, five-star, 6 guests, 2027-06-01 to 2027-06-05, around $9,000 total",
         truth=dict(city="Zanzibar", check_in=D(2027, 6, 1), check_out=D(2027, 6, 5), nights=4, pax=6, hotel_star=5, budget_total=9000.0, currency="USD")),
    dict(id="h03", text="Looking at Goa in late January. 3-star is fine. There will be 9 of us from 24/01/2027 until 28/01/2027, budget Rs 1.2 lakh.",
         truth=dict(city="Goa", check_in=D(2027, 1, 24), check_out=D(2027, 1, 28), nights=4, pax=9, hotel_star=3, budget_total=120000.0, currency="INR")),
    dict(id="h04", text="Safari group (Arusha), 12 pax, 4*, check-in 14 September 2027, 5 nights. Budget: $11,500",
         truth=dict(city="Arusha", check_in=D(2027, 9, 14), check_out=D(2027, 9, 19), nights=5, pax=12, hotel_star=4, budget_total=11500.0, currency="USD")),
    dict(id="h05", text="Reunion trip to Jaipur, 22 people, five star, 3-6 December 2027. Budget is INR 12,00,000.",
         truth=dict(city="Jaipur", check_in=D(2027, 12, 3), check_out=D(2027, 12, 6), nights=3, pax=22, hotel_star=5, budget_total=1200000.0, currency="INR")),
    dict(id="h06", text="Need 3 star rooms in Zanzibar for ten people between March 3 and March 8, 2027. No budget given yet.",
         truth=dict(city="Zanzibar", check_in=D(2027, 3, 3), check_out=D(2027, 3, 8), nights=5, pax=10, hotel_star=3, budget_total=None, currency=None)),
    dict(id="h07", text="South Goa beach holiday: 5 adults and 2 children, 4-star, 2027-11-05 to 2027-11-09, budget 2.5 lakh rupees",
         truth=dict(city="Goa", check_in=D(2027, 11, 5), check_out=D(2027, 11, 9), nights=4, pax=7, hotel_star=4, budget_total=250000.0, currency="INR")),
    dict(id="h08", text="Quote request - Arusha - 5-star - 16 guests - 10 to 14 April 2027 - budget USD 20k",
         truth=dict(city="Arusha", check_in=D(2027, 4, 10), check_out=D(2027, 4, 14), nights=4, pax=16, hotel_star=5, budget_total=20000.0, currency="USD")),
    dict(id="h09", text="we're 8 friends heading to Goa on 7 Mar 2027 for 3 nights, want a 4 star, around ₹1,60,000 budget",
         truth=dict(city="Goa", check_in=D(2027, 3, 7), check_out=D(2027, 3, 10), nights=3, pax=8, hotel_star=4, budget_total=160000.0, currency="INR")),
    dict(id="h10", text="Jaipur 4-star, group size: 18, from 12/04/2027 to 15/04/2027, budget 4.5 lakh",
         truth=dict(city="Jaipur", check_in=D(2027, 4, 12), check_out=D(2027, 4, 15), nights=3, pax=18, hotel_star=4, budget_total=450000.0, currency="INR")),
    dict(id="h11", text="Honeymoon in Nungwi (Zanzibar), 2 guests, 5-star, 10-14 Aug 2027, up to $5k",
         truth=dict(city="Zanzibar", check_in=D(2027, 8, 10), check_out=D(2027, 8, 14), nights=4, pax=2, hotel_star=5, budget_total=5000.0, currency="USD")),
    dict(id="h12", text="Please find a 4-star hotel in Arusha for 20 delegates from 2027-05-17 to 2027-05-20. Budget about 14,000 USD.",
         truth=dict(city="Arusha", check_in=D(2027, 5, 17), check_out=D(2027, 5, 20), nights=3, pax=20, hotel_star=4, budget_total=14000.0, currency="USD")),
]


# =============================================================================================
# EXTENDED SET (75 more trips -> 100). Written to break things, not to be passed:
#   * M-cases: messy but complete requests (typos, chat style, forwarded mail, a correction after the
#     original ask, headcount instead of "people"). A careful reader can extract every field.
#   * X-cases: requests a correct system must NOT run on (relative dates, two cities, two group sizes,
#     unsupported city, no star rating, no year). Expected outcome: escalate at intake.
#   * V-cases: complete requests in varied phrasing, paired with vendor behaviours that push past limits,
#     change terms mid-thread, inject instructions, or quote in informal formats (simulator.ADVERSARIAL).
M = [
    case("m01", "Fwd: group enquiry\n\n---\nHi team, pls quote Jaipr, 4 star, 12 ppl, 9-12 Feb 2027, budget INR 3 lakh. thx",
         "Jaipur", D(2027, 2, 9), D(2027, 2, 12), 12, 4, 300000.0, "INR"),
    case("m02", "zanzibar!! 4 star pls. we r 10. 3rd to 7th of june 2027. $8k max",
         "Zanzibar", D(2027, 6, 3), D(2027, 6, 7), 10, 4, 8000.0, "USD"),
    case("m03", "Original request: Goa, 3-star, 8 guests, 2027-03-01 to 2027-03-04.\nUPDATE: make that 10 guests and 4-star please, "
                "same dates. Budget INR 1.5 lakh.",
         "Goa", D(2027, 3, 1), D(2027, 3, 4), 10, 4, 150000.0, "INR"),
    case("m04", "Arusha safari group - eleven travellers - five-star lodge - 2027-08-20 to 2027-08-24 - USD 16,000",
         "Arusha", D(2027, 8, 20), D(2027, 8, 24), 11, 5, 16000.0, "USD"),
    case("m05", "Could you check a 5* place in Stone Town for 6 of us, 14/02/2027 to 17/02/2027? budget roughly 4500 USD",
         "Zanzibar", D(2027, 2, 14), D(2027, 2, 17), 6, 5, 4500.0, "USD"),
    case("m06", "jaipur trip for the family: 3 adults + 4 kids, 3 star, 18 Dec 2027 for 3 nights, around Rs 90,000",
         "Jaipur", D(2027, 12, 18), D(2027, 12, 21), 7, 3, 90000.0, "INR"),
    case("m07", "Goa offsite. Headcount: 26. Hotel: 4 star. Check-in 2027-09-10, check-out 2027-09-13. Budget: 9 lakh",
         "Goa", D(2027, 9, 10), D(2027, 9, 13), 26, 4, 900000.0, "INR"),
    case("m08", "Hello,\nWe'd like to book Nungwi beach for our team of 16 (5-star), from the 1st of October 2027 until the 5th "
                "of October 2027. Budget $20,000.\nRegards, Anna",
         "Zanzibar", D(2027, 10, 1), D(2027, 10, 5), 16, 5, 20000.0, "USD"),
    case("m09", "ARUSHA | 3 STAR | 9 PAX | 05/11/2027-09/11/2027 | USD 3,600",
         "Arusha", D(2027, 11, 5), D(2027, 11, 9), 9, 3, 3600.0, "USD"),
    case("m10", "need hotels in goa for 14 ppl (4star) from jan 20 to jan 24 2027 budget 4 lakhs",
         "Goa", D(2027, 1, 20), D(2027, 1, 24), 14, 4, 400000.0, "INR"),
]

X = [
    case("x01", "Jaipur, 4-star, 15 people, sometime next month, budget 4 lakh",
         "Jaipur", None, None, 15, 4, 400000.0, "INR", incomplete=True),
    case("x02", "Goa or Jaipur, not decided yet. 10 people, 3-star, 2027-05-01 to 2027-05-04",
         None, D(2027, 5, 1), D(2027, 5, 4), 10, 3, None, None, incomplete=True),
    case("x03", "Zanzibar 4 star 2027-07-10 to 2027-07-14, 12 or maybe 14 people, $11k",
         "Zanzibar", D(2027, 7, 10), D(2027, 7, 14), None, 4, 11000.0, "USD", incomplete=True),
    case("x04", "Arusha for 8 guests, 2027-04-10 to 2027-04-13, a nice hotel, budget USD 6,000",
         "Arusha", D(2027, 4, 10), D(2027, 4, 13), 8, None, 6000.0, "USD", incomplete=True),
    case("x05", "Book something in Mombasa for 10 people, 4-star, 2027-06-01 to 2027-06-04",
         None, D(2027, 6, 1), D(2027, 6, 4), 10, 4, None, None, incomplete=True),
    case("x06", "Goa, 4-star, 12 guests, from 14 March for 4 nights, budget 3 lakh",
         "Goa", None, None, 12, 4, 300000.0, "INR", incomplete=True),
    case("x07", "Jaipur 5 star for the usual group, 2027-10-02 to 2027-10-05, budget 8 lakh",
         "Jaipur", D(2027, 10, 2), D(2027, 10, 5), None, 5, 800000.0, "INR", incomplete=True),
    case("x08", "Zanzibar, 3-star, 6 pax, check-in 2027-02-10, budget $2,000",
         "Zanzibar", D(2027, 2, 10), None, 6, 3, 2000.0, "USD", incomplete=True),
    case("x09", "Can you sort the Arusha trip? Same as last time.",
         "Arusha", None, None, None, None, None, None, incomplete=True),
    case("x10", "4-star in Goa for 18 people after Diwali, about a week, budget 6 lakh",
         "Goa", None, None, 18, 4, 600000.0, "INR", incomplete=True),
]


def _v_cases():
    from datetime import timedelta
    areas = {"Jaipur": ["Jaipur", "the Pink City (Jaipur)"], "Goa": ["North Goa", "Goa"],
             "Zanzibar": ["Stone Town, Zanzibar", "Nungwi, Zanzibar"], "Arusha": ["Arusha"]}
    cities = ["Jaipur", "Goa", "Zanzibar", "Arusha"]
    word = {3: "three", 4: "four", 5: "five"}
    adv = S.ADVERSARIAL
    std = ["clean", "negotiable", "two_round", "firm_slightly_high", "firm_high", "mixed_one_ambiguous",
           "partial_all", "declines_all", "silent_all", "tax_unclear_all", "ambiguous_all"]
    cats = ["hotel", "transport", "activity"]
    out = []
    for i in range(55):
        city = cities[i % 4]
        cur = "INR" if city in ("Jaipur", "Goa") else "USD"
        star = 3 + (i % 3)
        pax = 4 + (i * 7) % 25
        nights = 2 + i % 4
        d1 = D(2027, 1 + (i * 5) % 12, 3 + (i * 3) % 16)
        d2 = d1 + timedelta(days=nights)
        per = {3: 3500, 4: 7000, 5: 15000}[star] if cur == "INR" else {3: 110, 4: 220, 5: 320}[star]
        budget = float(round(per * pax * nights * (1.6 if cur == "INR" else 1.5), -3))
        if cur == "INR":
            bstr = [f"INR {budget:,.0f}", f"Rs {budget / 100000:g} lakh" if budget % 10000 == 0 else f"Rs {budget:,.0f}",
                    f"₹{budget:,.0f}"][i % 3]
        else:
            bstr = [f"${budget:,.0f}", f"USD {budget / 1000:g}k", f"{budget:,.0f} USD"][i % 3]
        area = areas[city][i % len(areas[city])]
        t = i % 6
        if t in (0, 5) and d1.month != d2.month:
            t = 1
        text = [
            f"{star}-star in {area} for {pax} people, {d1.day}-{d2.day} {d1:%B %Y}, budget {bstr}",
            f"Group of {pax} heading to {area} from {d1:%Y-%m-%d} to {d2:%Y-%m-%d}. {star} star hotel. Budget {bstr}.",
            f"{area}: {pax} pax, {star}*, {d1:%d/%m/%Y} - {d2:%d/%m/%Y}, {bstr}",
            f"Hi team, please quote a {word[star]}-star property in {area} for {pax} guests arriving {d1.day} {d1:%b %Y} for "
            f"{nights} nights. Budget {bstr}.",
            f"Need {area} {star} star, pax {pax}, check in {d1:%Y-%m-%d}, check out {d2:%Y-%m-%d}, budget {bstr}",
            f"We're {pax} travellers looking at {area}, {d1:%B} {d1.day} to {d2.day}, {d1.year}, {star}-star, budget around {bstr}",
        ][t]
        # 33 adversarial assignments (each behaviour 3-4 times, rotating service), 22 standard ones
        if i < 33:
            prof = {cats[i % 3]: adv[i % len(adv)]}
            if i % 4 == 0:
                prof[cats[(i + 1) % 3]] = std[i % 3]          # a second, ordinary behaviour on another service
        else:
            prof = {cats[i % 3]: std[i % len(std)]}
        out.append(case(f"v{i + 1:02d}", text, city, d1, d2, pax, star, budget, cur, prof))
    return out


V = _v_cases()
EXTRA = M + X + V
ALL = CASES + EXTRA
assert len(ALL) == 100 and len({c["id"] for c in ALL}) == 100
