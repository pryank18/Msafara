# Vendor Coordination Agent — Product Spec

## Problem

Tour operators and DMCs (destination management companies) coordinate vendors — hotels, transport, activity providers — largely by email and phone for every trip request. Someone has to draft the request, send it, chase vendors who go quiet, negotiate within the operator's rules, track how long a quoted hold is valid for, compare competing offers, and turn the winning offer into a client-facing proposal. Done manually, this is slow and easy to drop threads on when multiple requests are in flight at once.

## Target user

Tour operators and DMCs coordinating vendor sourcing for multiple simultaneous trip requests, without a dedicated vendor-management system.

## Goals

- Automate the repetitive parts of vendor coordination (drafting, sending, following up on silence) so a coordinator can run more requests in parallel
- Keep negotiation within bounded rules so automated follow-ups don't over-commit the operator
- Make hold expiry and offer comparison visible in one place instead of tracked by memory or inbox search

## Non-goals (v1)

- Payment processing or vendor invoicing
- Direct vendor-side booking/inventory system integration
- Fully autonomous negotiation without operator-set bounds

## Core features (v1 scope)

| Feature | Description |
| --- | --- |
| Request queue | Every vendor request, current status, and last-updated time in one view |
| New request / sourcing | Draft and send a new vendor request or sourcing round |
| Compare sourcing | Side-by-side comparison of competing vendor offers for the same request |
| Vendor directory | Reference list of vendors available for sourcing |
| Generate proposal | Turn a selected/winning offer into a client-facing proposal |
| Inbox polling & timeout checks | Poll for vendor replies and flag requests approaching hold expiry or timeout |

## User stories

- As a coordinator, I want to send a vendor request without drafting it from scratch each time.
- As a coordinator, I want silent vendors followed up on automatically, within rules I set, so requests don't stall.
- As a coordinator, I want to see all open requests and their status in one queue instead of searching my inbox.
- As a coordinator, I want to compare competing vendor offers side by side before deciding.
- As a coordinator, I want to generate a client-facing proposal directly from the winning offer.

## Status

Live — v1 is built and deployed at pryank18.github.io/Msafara/, including the request queue, new request/sourcing flows, offer comparison, vendor directory, and proposal generation.
