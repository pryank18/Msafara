# Msafara — Vendor Coordination Agent

> 📘 **[Read the product case study on Notion](https://fern-appliance-85f.notion.site/3ebefd17b766814b8760e8717345b4a7)** — problem, key decisions, what was cut and how I'd measure it.
>
> **Live demo:** [pryank18.github.io/Msafara](https://pryank18.github.io/Msafara/) · **Docs:** [PRD](https://github.com/pryank18/Msafara/blob/main/docs/PRD.md) · [BRD](https://github.com/pryank18/Msafara/blob/main/docs/BRD.md) · [MRD](https://github.com/pryank18/Msafara/blob/main/docs/MRD.md) · [Spec](https://github.com/pryank18/Msafara/blob/main/docs/product-spec.md) · **More work:** [Notion portfolio](https://fern-appliance-85f.notion.site/Pryank-Wadhera-3eaefd17b76680c88283e85a3217ff52)

A multi-agent platform for tour operators and DMCs that automates vendor coordination end-to-end: drafting requests, sending, following up on silence, negotiating within bounded rules, tracking hold expiry, comparing competitive offers, and generating client-facing proposals. Runs in a fully demoable mock mode with zero API keys required.

**Live demo:** https://pryank18.github.io/Msafara/

## Product decisions

- **Bounded negotiation, never autonomous.** The agent counters within operator-set rules and a capped number of rounds; fully autonomous negotiation is out of scope.
- **Escalate ambiguity to a human.** Partial or unclear vendor replies go back to the coordinator instead of being guessed at.
- **Runs with zero API keys.** A rule-based mock mode means anyone can demo the full flow; AI drafting and parsing switch on when a key is added.
- **Meet vendors where they already are.** Email and WhatsApp, not a vendor portal nobody will log into.

**How I'd measure it:** concurrent requests one coordinator can run, requests that stall on unanswered silence, and time from request to client-ready proposal.

## What's in this repo

This repo contains the live demo and core workflow modules (`graph.py`, `storage.py`, `dispatch.py`, `inbox_poll.py`). The full backend (FastAPI server, CLI, and seeded demo database) is not published here; the live demo above runs the complete flow in the browser with sample data (`demo-api.js`).

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
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | Outbound email dispatch (`dispatch.py`) |
| `IMAP_HOST` / `IMAP_USER` / `IMAP_PASSWORD` | Inbound email polling (`inbox_poll.py`) |

## Architecture

- `graph.py` — the LangGraph state machine wiring the workflow together, plus entry points for inbound replies and scheduled checks
- `storage.py` — SQLite persistence layer
- `dispatch.py` — outbound email send
- `inbox_poll.py` — inbound email polling and reply matching
- `index.html` — the single-page dashboard
- `demo-api.js` — in-browser stand-in for the API on GitHub Pages, seeded with a 12-person Kilimanjaro & Zanzibar trip (does nothing when served by the real backend)

## How it was built

Built AI-assisted with Claude as coding partner. Product scope, requirements (see `docs/`), and QA are mine.

## Known limitations

- No authentication — add an auth layer before exposing beyond local/demo use
- No concurrency control — SQLite without locking, suited for single-user use
- Currency is cosmetic — the UI displays `$` regardless of the stored currency
- Negotiation logic uses placeholder defaults, not real walk-away rules
- WhatsApp replies require a webhook receiver (not included) — outbound dispatch only for now
