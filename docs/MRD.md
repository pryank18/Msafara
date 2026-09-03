# Vendor Coordination Agent — Market Requirements Document

## Market problem

Tour operators and DMCs coordinate hotels, transport, and activity vendors by email and phone for essentially every trip request. This is manageable at low volume but breaks down as request volume grows: coordinators end up chasing silent vendors, tracking hold expiry from memory, and assembling offer comparisons by hand — work that scales linearly with headcount rather than with better tooling.

## Target market

Small and mid-size tour operators and DMCs that run vendor sourcing in-house without a dedicated vendor-management system, particularly those handling multiple simultaneous trip requests where manual coordination starts to strain.

## Why now

Operators in this segment are typically too small to justify building custom vendor-management tooling themselves, but the coordination workload doesn't shrink — it's the same repetitive drafting, following up, and comparing regardless of company size. A lightweight, rules-bounded automation layer fits a segment that enterprise travel-tech platforms don't typically serve.

## Competitive landscape

- **Enterprise travel/DMC management suites** — broad functionality but built for larger operators, with corresponding cost and implementation overhead
- **Generic CRM / email tooling** — flexible but provides no purpose-built logic for vendor follow-up, hold-expiry tracking, or offer comparison
- **Status quo (manual email/phone coordination)** — the default today; no software cost, but limits how many concurrent requests a coordinator can run

## Value proposition

The Vendor Coordination Agent automates the repetitive, time-sensitive parts of vendor sourcing — follow-up, hold tracking, comparison, and proposal generation — within rules the operator sets, letting a coordinator run more requests in parallel without losing control over negotiation.

## Positioning

A rules-bounded coordination layer for operators who need to scale vendor sourcing without either hiring more coordinators or adopting enterprise-scale travel-tech software.

## Status

Live v1, built as a self-directed product project; not yet validated with paying operator customers.
