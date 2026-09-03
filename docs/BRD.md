# Vendor Coordination Agent — Business Requirements Document

## Business objective

Let a tour operator or DMC run more vendor sourcing requests in parallel by automating the repetitive coordination work — drafting, sending, following up, tracking hold expiry, and comparing offers — while keeping negotiation within rules the operator controls.

## Background

Vendor coordination for trip requests is typically run by email and phone, one request at a time. A coordinator has to draft each request, chase vendors individually when they go quiet, track quoted-hold expiry by memory or inbox search, and manually assemble a comparison before deciding. This limits how many requests a single coordinator can realistically run at once, and threads get dropped when volume increases.

## Scope

**In scope:** request queue and status tracking, request/sourcing drafting, automated follow-up within bounded rules, hold-expiry and timeout checks, offer comparison, vendor directory, and client-facing proposal generation.

**Out of scope:** payment processing, vendor invoicing, direct vendor-side booking/inventory integration, fully autonomous negotiation without operator-set bounds.

## Stakeholders

- **Primary user:** tour operator / DMC coordinator running vendor sourcing
- **Indirect stakeholder:** the operator's client, who ultimately receives the generated proposal
- **Product owner:** Pryank Wadhera

## Success criteria

- Increase in number of vendor requests a coordinator can run concurrently without dropped threads
- Reduction in requests stalled by unfollowed-up vendor silence
- Faster turnaround from initial request to client-ready proposal

## Assumptions

- Vendor communication continues to happen over standard channels (email/inbox) that the system can poll and act on
- Operator is willing to define bounded negotiation rules up front rather than negotiate freeform per request

## Constraints

- Automated follow-up and negotiation must stay within operator-defined bounds — no unbounded autonomous commitments to vendors
- No payment or invoicing flows in v1; the system manages coordination and proposal generation only

## Risks

- Automation quality depends on the operator setting realistic negotiation bounds; overly rigid or loose rules could mishandle real vendor conversations
- Reliance on inbox polling means responsiveness depends on polling frequency and vendor reply patterns

## Status

Live — v1 built and deployed as a self-directed product project.
