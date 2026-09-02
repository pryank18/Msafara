# Vendor Coordination Agent

A live multi-agent platform for tour operators/DMCs: coordinates vendor requests (accommodation, transport, activities) end to end — drafting, sending, following up on silence, negotiating within bounded rules, tracking hold expiry, comparing competitive offers, and generating client-facing proposals. Runs with mock AI/dispatch out of the box, so it's fully demoable with zero API keys.

## Quickstart

```
git clone <this-repo-url>
cd vendor_agent
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python3 app.py
```

Then open http://localhost:8000. The repo ships with `vendor_requests.db` already seeded with a realistic 12-person Kilimanjaro & Zanzibar trip scenario, so the dashboard has real data to look at immediately — no setup step required to see it working.

To reset to a fresh seeded state at any point:

```
rm vendor_requests.db && python3 seed_demo.py
```

To make it real instead of mock/demo mode, see "To go from demo to real" further down — in short: `ANTHROPIC_API_KEY` for real AI parsing/drafting, `SMTP_*`/`IMAP_*` for real email, `TWILIO_*` for real WhatsApp.

The `cli.py` script is also available as a terminal-only alternative to the web dashboard (`python3 cli.py` for usage).

## Development history

The sections below are a chronological record of how this was built, kept because they document real decisions and real bugs found along the way, not because they're required reading to use the tool.

The vendor coordination piece of a multi-agent travel-ops system for tour operators/DMCs. Handles the loop of: draft a request to a vendor, send it, wait, follow up on silence, parse the reply, and either resolve or escalate to a human.

### Files

- `state.py` — the state object that persists across the loop (status, message history, follow-up/negotiation counters, deadlines)
- `llm_nodes.py` — the two nodes that need real LLM judgment: drafting messages and parsing vendor replies. Runs in MOCK MODE (rule-based stand-ins) if `ANTHROPIC_API_KEY` isn't set — set that env var and it switches to real Claude calls automatically, no other code changes.
- `graph.py` — the LangGraph state machine wiring it together, plus the two re-entry points (`handle_incoming_reply`, `handle_scheduled_check`) you'd call from a real email webhook / cron job.
- `run_demo.py` — runs three core scenarios (happy path, partial availability, silence-then-reply) against the actual graph. Run with `python3 run_demo.py`.

### To make this real

1. Set `ANTHROPIC_API_KEY` — drafting and parsing switch from mock to real Claude calls immediately, no code changes needed elsewhere.
2. Swap `node_send`'s placeholder for real dispatch — SMTP for email, or a WhatsApp Business API call.
3. Wire an inbound email/webhook handler to call `handle_incoming_reply` when a vendor actually replies, instead of the hardcoded strings in `run_demo.py`.
4. Add a cron job calling `handle_scheduled_check` every few hours over every request currently `AWAITING_RESPONSE` — this is what actually triggers follow-ups and escalation on silence.
5. Swap the in-memory state dicts for a SQLite (or Postgres) table keyed by `request_id`, since state needs to survive between graph invocations that can be days apart.

### What's deliberately NOT built yet

- Multi-vendor competitive sourcing — needs a parent "need" object tracking several child requests
- Negotiation/counter-offer node (Scenario 4) — stubbed as escalate for now since price floor/ceiling logic needs your input first
- Real dispatch (email/WhatsApp sending) — currently just drafts text

## Now with persistence (storage.py + cli.py)

State now survives between runs via SQLite (`vendor_requests.db`, created automatically on first use). Try it:

```
python3 cli.py new       # create a request, prompts for details
python3 cli.py queue     # see all requests and their status
python3 cli.py reply <request_id>   # paste a vendor's reply, process it
python3 cli.py check     # run the timeout check over everything awaiting a reply
python3 cli.py show <request_id>    # full detail + message history
```

This is the loop you'd actually run day to day: create requests as you send them out, run `check` periodically (or cron it), and process replies as they land in your inbox by pasting them into `reply`.

## Now with real dispatch (dispatch.py + inbox_poll.py)

Outbound sends via SMTP, inbound replies via IMAP poll, both optional — everything still runs and prints clear "not configured" messages if you haven't set credentials yet, same as before.

To turn it on:

```
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=your-address@gmail.com
export SMTP_PASSWORD=your-app-password   # Gmail: App Password, not your real password
export IMAP_HOST=imap.gmail.com
export IMAP_USER=your-address@gmail.com
export IMAP_PASSWORD=your-app-password
```

How replies get matched to requests: every outbound subject line includes the request_id like `[req-81018e90] Request — ...`. Most email clients keep the subject on reply, so poll just regexes the id back out — no real threading/Message-ID tracking needed for MVP.

New command:

```
python3 cli.py poll      # check inbox for new vendor replies, process any matches
```

Run `check` and `poll` on a schedule (cron, every 1-4 hours) and this runs mostly hands-off — you only touch it to create new requests or handle something it escalates to you.

## Now with competitive sourcing (sourcing.py)

Send the same need to multiple vendors at once and compare responses as they land.

```
python3 cli.py source              # create a request, prompts for 2+ vendors
python3 cli.py compare <sourcing_id>   # see who's responded, ranked by price
```

Design note: each vendor still runs through the exact same single-vendor loop (draft/send/wait/parse/evaluate) — sourcing.py just tags them with a shared sourcing_id and gives you a read-side view across all of them. `check`, `poll`, and `reply` all work on the child requests exactly as before; nothing about the core loop changed.

`compare` never auto-picks a winner — price isn't the only factor (quantity match, exact dates, breakfast included or not), so it ranks confirmed offers by price and leaves the actual decision to you.

## Now with expiring-hold tracking (holds.py)

A vendor confirming with "rate held for 48 hours" isn't the end of the story — if nobody locks the booking in before the window closes, the hold lapses silently. `check` now flags these alongside the timeout sweep.

```
python3 cli.py check              # now also warns on holds expiring within 6h,
                                   # and flags any that already lapsed unlocked
python3 cli.py lock <request_id>  # mark a hold as actually booked — stops it
                                   # showing up in expiry warnings
```

A confirmed request keeps showing up in `check`'s warnings until you explicitly `lock` it — this is deliberate, since "vendor said yes" and "actually booked" are different states and nothing should let that gap go unnoticed.

## Now with negotiation (negotiation_rules.py) and multi-item replies

Negotiation: if a vendor's confirmed quote is above `need.budget_max`, the agent auto-drafts a counter-offer instead of confirming or escalating immediately. Bounded by `MAX_NEGOTIATION_ROUNDS` (default 2) — after that many rounds without the vendor matching budget, it escalates with the reason spelled out, rather than negotiating forever.

IMPORTANT — these are placeholder defaults, not your real rules. `negotiation_rules.py` documents this clearly: it always counters at your stated `budget_max`, doesn't split the difference, and doesn't vary by vendor relationship. Edit `counter_offer_rate()` and `MAX_NEGOTIATION_ROUNDS` in that file once you know your actual walk-away logic — nothing else needs to change, `evaluate()` in `graph.py` just calls into that file.

Multi-item replies: one vendor reply can now answer several asks at once (e.g. a DMC confirming transport AND a tour in one message) via `need.additional_asks` and `ParsedResult.line_items`. Only auto-confirms if every line item is a clear yes — any decline or ambiguity on one item escalates with that item named, rather than silently confirming a partial deal.

## Now with reopen-for-revision (revision.py)

Scenario 8: a client changes something (group size, dates) after a vendor already confirmed. Reopening reuses the exact same loop rather than inventing new machinery — it drops the request back to `AWAITING_RESPONSE` with a revision message sent, clears the stale confirmation and any hold lock, and it goes through parse -> evaluate exactly like a first-time reply.

```
python3 cli.py revise <request_id>   # only works on CONFIRMED requests
```

## Status: all 8 original scenarios built and tested

1. Happy path — `run_demo.py`
2. Partial availability -> escalate — `run_demo.py`
3. Silence -> follow-up -> late reply — `run_demo.py`
4. Price negotiation with bounded rounds — `negotiation_rules.py` + `graph.py` (defaults only — edit `negotiation_rules.py` with your real numbers)
5. Competitive sourcing across multiple vendors — `sourcing.py`
6. Expiring/conditional quotes — `holds.py`
7. Multi-service vendor replies (one reply, multiple line items) — `state.py` `LineItem` + `graph.py` `evaluate`
8. Reopen after confirmation when details change — `revision.py`

## What's still genuinely open (needs your input, not more engineering)

- `negotiation_rules.py` `counter_offer_rate()` — currently always counters at your stated `budget_max`, no vendor-specific logic, no splitting the difference between rounds. Real behavior needs your actual walk-away rules.
- Real vendor test data — every scenario above is tested against text I wrote to match the shapes we discussed. The mock parser (`llm_nodes.py` `_mock_parse`) is regex-based and has already needed three separate fixes for phrasing it didn't anticipate — this is expected and is exactly why it's a placeholder. Setting `ANTHROPIC_API_KEY` swaps it for a real LLM call with zero other code changes, and that's the real fix, not more regex patches.
- A live inbox — `dispatch.py`/`inbox_poll.py` are tested against unconfigured credentials only. First real test needs an actual email account wired in via the env vars in the README section above.

## Live platform (app.py + static/index.html)

A real web dashboard on top of everything above — FastAPI backend, single-page frontend, all wired to the actual agent logic (not a mockup).

```
pip install fastapi uvicorn
python3 seed_demo.py    # populates a realistic demo scenario (see below)
python3 app.py           # starts the server
```

Then open http://localhost:8000 in a browser.

### Seeded demo scenario

`seed_demo.py` populates a 12-person Kilimanjaro & Zanzibar trip with a vendor request in every state the system handles, so the dashboard is immediately demoable:

| Request | State | What it shows |
|---|---|---|
| `req-zanz-hotel` | confirmed, locked in | resolved, no action needed |
| `req-kili-lodge` | confirmed | hold expiring in ~4h — flag this with "Run check" |
| `req-transport` | negotiating | quote came in over budget, one counter already sent |
| `req-park-permits` | escalated | partial availability (8 of 12 permits) |
| `req-zanz-dmc` | escalated | multi-item reply, one of three items declined |
| sourcing request | in progress | same room block sent to 3 hotels, 2 responded at different prices, 1 still pending |

Delete `vendor_requests.db` and re-run `seed_demo.py` any time to reset.

### Dashboard sections

- Queue — every request, click any row for full detail: message history, paste a reply, lock a hold, or revise after confirmation. "Run check" triggers the timeout sweep + expiring-hold check on demand (this is what a cron job calls automatically in production). "Poll inbox" checks for real vendor replies if IMAP is configured.
- New Request — create and send a single-vendor request.
- New Sourcing — send the same need to 2+ vendors at once.
- Compare Sourcing — see responses ranked by price as they land.
- System Status — shows at a glance whether you're running in mock mode or wired to real Claude/SMTP/IMAP.

### Bug found and fixed while building this

The mock parser's rate regex broke on `$1,450` (comma in the number) — it silently parsed `$1` and confirmed a booking that should have triggered negotiation. Fixed in `llm_nodes.py`; caught because the seeded transport scenario used a realistic four-digit total and the platform showed the wrong status until I actually looked at it. Exactly the class of bug the real LLM parser (set `ANTHROPIC_API_KEY`) doesn't have — flagged again here since it's a good example of mock mode's actual limits, not just a disclaimer.

### To go from demo to real

- `ANTHROPIC_API_KEY` — real drafting/parsing instead of the regex mock
- `SMTP_*` / `IMAP_*` — real dispatch and inbox polling (see earlier section)
- `negotiation_rules.py` — replace the placeholder counter-offer logic with your actual walk-away rules
- A cron entry calling `POST /api/check` and `POST /api/poll` every few hours, so the loop runs without you opening the dashboard

## Dashboard v2: languages, automation transparency, visual polish

Languages — switcher in the sidebar, 5 languages fully translated (all UI chrome, not just a few labels — 89 keys per language, verified complete with no gaps): English, Arabic, French, Swahili, Hindi. Picked for your actual markets: Arabic for UAE/Dubai, French and Swahili cover Francophone and East African tourism, Hindi for home. Arabic gets real RTL layout — the whole page mirrors (sidebar moves to the right, text alignment flips, message threads reverse), not just translated text sitting in a left-to-right frame.

"How It Works" panel — new nav item laying out exactly what runs on its own versus what still needs you, split into two columns so it's unambiguous at a glance:

- Handled automatically: drafting requests, parsing replies, following up on silence, counter-offering within capped rounds, confirming clear wins, tracking hold expiry, ranking competitive offers, splitting multi-item replies.
- Still needs you: anything genuinely ambiguous, your actual negotiation rules (the shipped defaults are placeholders), marking a hold as truly locked in, reopening after a client change, sending manually if SMTP isn't wired up, and picking between ranked offers where price isn't the only factor.

This isn't just a description block — it matches exactly what the code does, since it's describing the same routing logic in `graph.py`.

Visual polish — stats strip at the top of the queue (open / confirmed / needs attention / negotiating, computed live from the data), refined color depth and spacing, subtle panel transitions, better button and input states. Kept the control-room identity from v1 rather than defaulting to a generic SaaS look — the queue table is still the hero, not a hero banner.

## Reviewer pass — gaps found and fixed

Went through the whole codebase as a reviewer rather than just skimming. Four real issues found and fixed, all tested against the actual running system (not just read through):

1. Functional bug — stalled negotiations never got followed up. `check`/`poll`'s timeout sweep only ever queried `AWAITING_RESPONSE` requests. A request in `NEGOTIATING` (a counter-offer sent, waiting on the vendor) was invisible to that query — if the vendor never replied to the counter, it sat there forever with no follow-up and no escalation. Fixed: `graph.TIMEOUT_CHECKABLE_STATUSES` now covers both statuses, and `storage.list_by_statuses()` queries both. Verified live through `/api/check`: a silently-stalled negotiation is now correctly picked up and followed up.

2. Crash risk — bad SMTP/IMAP credentials could 500 the whole request. `dispatch.send_email()` and `inbox_poll.fetch_unread_replies()` had no error handling around the actual network calls. A wrong password or unreachable host would raise an uncaught exception mid-`graph.invoke()`, before the request was ever saved — the user's input would just be lost behind a 500 error. Fixed: both now catch connection/auth failures, log them, and degrade to the same "would send" / "no new replies" behavior as being unconfigured. Verified with deliberately broken credentials against both.

3. Security — vendor reply text was inserted into the page unescaped. The dashboard used `innerHTML` to render vendor names, need descriptions, and — most importantly — the actual message history (real vendor email content) with no escaping. A vendor reply containing HTML or a `<script>` tag would have executed in the dashboard. Fixed: every place external text gets rendered now goes through an `esc()` helper; toasts use `textContent` exclusively so they're safe regardless of content.

4. Logic gap — multi-item replies skipped the budget check entirely. A reply confirming every line item as available auto-confirmed the whole booking, even if the combined total was wildly over budget — the budget check only ever ran on single-rate replies. Fixed: multi-item replies now sum priced line items and escalate (rather than silently confirm) if the total exceeds `budget_max`.

Also, while fixing #2 and #3, upgraded the frontend's error handling generally: every fetch call is now wrapped in try/catch with a toast on failure instead of an unhandled rejection or a silent no-op, and native `alert()` popups are gone entirely in favor of toast notifications — partly a robustness fix, partly because blocking browser dialogs don't belong in something meant to look like a real product.

### Known gaps not fixed (scope, not oversight)

- No authentication. Every API endpoint is open — anyone who can reach the server can create, modify, or read all requests. Fine for a local demo; not fine to expose on a network without adding an auth layer (API key middleware or a reverse proxy) first.
- No concurrency control. SQLite with no locking strategy — fine for one person using the dashboard, not built for multiple people hitting it at once.
- Currency is cosmetic. `need.currency` exists on the model but the UI hardcodes `$` everywhere; multi-currency sourcing would display wrong.
- `negotiation_rules.counter_offer_rate()`'s `round_number` parameter is accepted but unused — the default rule counters at the same target every round by design (documented in the file), but the parameter is there for when you actually want round-dependent logic.

## Three integrations added: vendor directory, WhatsApp, proposal docs

### Vendor directory (vendor_directory.py)

Builds itself from usage — every vendor registers automatically the first time you send them a request (single or via sourcing), no separate data entry. Stats (confirm rate, request count, category) are computed live from actual request history rather than kept as running counters, so they can never drift out of sync. New "Vendor Directory" panel in the dashboard.

```
GET  /api/vendors                        # list, with live stats
GET  /api/vendors/{contact_address}      # single vendor
PATCH /api/vendors/{contact_address}/notes
```

### WhatsApp dispatch (whatsapp_dispatch.py + message_router.py)

`VendorInfo.contact_channel` already supported "whatsapp" as a value — now it actually routes there. `message_router.send()` is the single place that decides email vs. WhatsApp based on the vendor's channel; `graph.py` and `revision.py` call that instead of `dispatch.py` directly. Same degrade-gracefully contract as SMTP: unconfigured or failing just logs and falls back to manual-send.

```
export TWILIO_ACCOUNT_SID=...
export TWILIO_AUTH_TOKEN=...
export TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # keep the whatsapp: prefix
```

Real limitation, not a bug: unlike email, there's no IMAP-style poll for WhatsApp — Twilio delivers replies via a webhook to a URL you host. This module only covers outbound. Recording an inbound WhatsApp reply still goes through the same manual reply path as email until a webhook receiver endpoint is added (that's a real follow-up piece, not built here — needs a public HTTPS endpoint, which this local dev setup doesn't have).

The New Request and New Sourcing forms now have an email/WhatsApp channel selector per vendor.

### Client proposal generation (proposal_doc.py)

The actual deliverable a tour operator sends a client — not just internal coordination. Pick any set of confirmed requests in the new "Generate Proposal" panel, and it produces a real .docx with an itemized table (vendor, dates, rate) and a total, pulling rates directly from what vendors actually confirmed rather than retyped by hand. Runs server-side with `python-docx` (a different tool than the docx skill I use for one-off chat deliverables — this is code inside the running app, generating documents on demand for whoever's using the dashboard).

Requests that aren't confirmed are never priced in the document — the generator explicitly refuses to run if nothing is confirmed yet, and notes excluded items by name rather than showing a blank or misleading rate.

```
POST /api/proposal  {"trip_title": "...", "client_name": "...", "request_ids": [...]}
```

returns the .docx directly as a file download.

### Testing note

All three were tested against the real running server (not just the underlying Python functions) — proposal generation was verified by actually rendering the output to an image and looking at it, not just confirming the file saved without crashing.
