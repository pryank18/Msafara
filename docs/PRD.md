# Vendor Coordination Agent — Product Requirements Document

## Summary

A vendor coordination platform for tour operators and DMCs that automates the repetitive parts of sourcing — drafting and sending requests, following up on silence, negotiating within bounded rules, tracking hold expiry, comparing offers, and generating client-facing proposals.

## Target users

- **Coordinator / operator** — runs vendor sourcing for multiple trip requests at once, sets negotiation rules and bounds
- **Vendor** — receives requests, quotes, and holds via the standard channels the system automates around (email/inbox)

## Problem statement

Manually running vendor sourcing means drafting each request from scratch, chasing vendors individually when they go quiet, tracking hold expiry by memory or inbox search, and manually assembling a comparison before deciding — all of which limits how many requests a coordinator can run in parallel.

## Functional requirements

1. **Request queue** — every vendor request with current status and last-updated time, in one view
2. **New request / new sourcing** — draft and send a request or sourcing round without starting from a blank page
3. **Automated follow-up** — silent vendors are followed up on automatically within operator-set bounds
4. **Hold expiry & timeout tracking** — requests approaching expiry or timeout are flagged automatically (via scheduled checks and inbox polling)
5. **Compare sourcing** — side-by-side comparison of competing offers for the same request
6. **Vendor directory** — reference list of vendors available for sourcing
7. **Generate proposal** — turn a selected offer into a client-facing proposal

## Out of scope (v1)

- Payment processing or vendor invoicing
- Direct integration with vendor-side booking/inventory systems
- Fully autonomous negotiation without operator-set bounds

## Success metrics

- Increase in number of concurrent vendor requests a single coordinator can run
- Reduction in requests that stall due to unfollowed-up silence
- Reduction in time from request to client-ready proposal

## Status

Live at pryank18.github.io/Msafara/, with the queue, sourcing, comparison, vendor directory, and proposal-generation flows implemented.
