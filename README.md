# Vendor Coordination Agent

A multi-agent platform for tour operators and DMCs that automates vendor coordination end-to-end: drafting requests, sending, following up on silence, negotiating within bounded rules, tracking hold expiry, comparing competitive offers, and generating client-facing proposals. Runs in a fully demoable mock mode with zero API keys required.

**Live demo:** https://pryank18.github.io/Msafara/

## Product decisions

- **Bounded negotiation, never autonomous.** The agent counters within operator-set rules and a capped number of rounds; fully autonomous negotiation is out of scope.
- **Escalate ambiguity to a human.** Partial or unclear vendor replies go back to the coordinator instead of being guessed at.
- **Runs with zero API keys.** A rule-based mock mode means anyone can demo the full flow; AI drafting and parsing switch on when a key is added.
- **Meet vendors where they already are.** Email and WhatsApp, not a vendor portal nobody will log into.

**How I'd measure it:** concurrent requests one coordinator can run, requests that stall on unanswered silence, and time from request to client-ready proposal.

## Quickstart

```
git clone <this-repo-url>
cd vendor_agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open http://localhost:8000. The repo ships with a seeded database (`vendor_requests.db`) containing a realistic 12-person Kilimanjaro & Zanzibar trip scenario, so the dashboard has real data immediately.

Reset to a fresh seeded state at any time:

```
rm vendor_requests.db && python3 seed_demo.py
```

A terminal-only alternative is available via `python3 cli.py`.

## Core capabilities

- Drafts and sends vendor requests (accommodation, transport, activities)
- Follows up automatically on silence and tracks response deadlines
- Negotiates within configurable bounded rules (capped rounds)
- Tracks expiring holds and flags lapsed ones
- Supports competitive sourcing across multiple vendors, ranked by price
- Handles multi-item vendor replies, escalating on partial or ambiguous responses
- Supports reopening a confirmed request for revision
- Generates client-facing proposal documents (.docx) from confirmed requests
- Email (SMTP/IMAP) and WhatsApp (Twilio) dispatch, both optional
- Multi-language dashboard: English, Arabic (full RTL), French, Swahili, Hindi

## Configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enables AI-based drafting and reply parsing (falls back to a rule-based mock if unset) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | Outbound email dispatch |
| `IMAP_HOST` / `IMAP_USER` / `IMAP_PASSWORD` | Inbound email polling |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` | WhatsApp dispatch |

Everything degrades gracefully when credentials aren't set — the app runs fully in demo/mock mode with clear "not configured" messaging.

## Architecture

- `state.py` — persistent state object for each request (status, message history, follow-up/negotiation counters, deadlines)
- `llm_nodes.py` — drafting and reply-parsing logic; uses a rule-based mock unless `ANTHROPIC_API_KEY` is set
- `graph.py` — the state machine wiring the workflow together, plus entry points for inbound replies and scheduled checks
- `storage.py` / `cli.py` — persistence layer and terminal interface
- `dispatch.py` / `inbox_poll.py` — email send/poll
- `whatsapp_dispatch.py` / `message_router.py` — WhatsApp dispatch and channel routing
- `sourcing.py` — competitive sourcing across multiple vendors
- `holds.py` — expiring-hold tracking
- `negotiation_rules.py` — bounded negotiation logic (placeholder defaults — replace with your actual walk-away rules)
- `revision.py` — reopening confirmed requests for revision
- `vendor_directory.py` — vendor stats built automatically from request history
- `proposal_doc.py` — client-facing proposal generation
- `app.py` / `static/index.html` — FastAPI backend and single-page dashboard

## CLI reference

```
python3 cli.py new              # create a request
python3 cli.py queue            # list all requests
python3 cli.py reply <id>       # process a vendor reply
python3 cli.py check            # run timeout + hold-expiry sweep
python3 cli.py show <id>        # full detail + message history
python3 cli.py poll             # check inbox for new replies
python3 cli.py source           # create a multi-vendor sourcing request
python3 cli.py compare <id>     # compare sourcing responses
python3 cli.py revise <id>      # reopen a confirmed request
```

## API

```
GET  /api/vendors
GET  /api/vendors/{contact_address}
PATCH /api/vendors/{contact_address}/notes
POST /api/proposal   {"trip_title": "...", "client_name": "...", "request_ids": [...]}
POST /api/check
POST /api/poll
```

## How it was built

Built AI-assisted with Claude as coding partner. Product scope, requirements (see `docs/`), and QA are mine.

## Known limitations

- No authentication — every endpoint is open; add an auth layer before exposing beyond local/demo use
- No concurrency control — SQLite without locking, suited for single-user use
- Currency is cosmetic — the UI displays `$` regardless of the stored currency
- Negotiation logic uses placeholder defaults; replace `counter_offer_rate()` in `negotiation_rules.py` with real walk-away rules
- WhatsApp replies require a webhook receiver (not included) — outbound dispatch only for now
