# Msafara trip workflow: what is built, what is measured, what you can claim

This package adds the multi-agent trip workflow to the vendor-coordination platform. Run it with
`python app.py` and open `/trips`. Tests: `pip install pytest httpx && pytest` (102 tests).
Evals: `python -m msafara.evals.run` then `python -m msafara.evals.run --report`.

## Spec coverage

| Spec item | Status | Where |
|---|---|---|
| LangGraph graph with intake, matching, outreach, negotiation, escalation | Built | `graph.py`, `agents.py` |
| Intake: dates, pax, city, budget, hotel category -> structured brief | Built | `agents.run_intake`, `TripBrief` |
| Vendor matching: 3-5 candidates per service | Built, **not an LLM agent**: RAG filter + rank, score = 0.5 semantic + 0.3 reliability + 0.2 preferred | `agents.match_vendors` |
| Outreach: drafts availability + quote requests | Built (template in mock mode, LLM with validation otherwise) | `agents.draft_outreach` |
| Negotiation: <=10% below quote, never below floor | Built **in code**, not left to an LLM; 2 rounds (10% then 5%) | `compute_counter`, `assert_counter_bounds` |
| Escalation node with reasons | Built: ambiguous / partial / tax unclear / low confidence / all declined / no response / over tolerance / incomplete intake / guardrail hit | `decide_service`, `graph.escalate*` |
| One shared trip state (brief, vendors, quotes, round, status, audit) | Built | `models.TripState` |
| RAG over profiles, rate cards, past quotes, negotiation logs, policies | Built on a **synthetic** KB | `kb.py`, `rag.py`, `seed_kb.py` |
| Chunk -> embed -> store -> retrieve by vendor ID + semantic | Built: sentence chunker, **hashing TF-IDF embedder (not neural)**, **Chroma** (numpy fallback, tested equal) | `rag.py` |
| Evals: 25 synthetic trips with known outcomes | Built | `evals/dataset.py` (+12 held-out intake phrasings) |
| Metrics: extraction, match relevance, bound violations, escalation P/R, grounding | Built, plus reply-parse accuracy and unapproved sends | `evals/metrics.py` |
| Rule-based checks + LLM-as-judge for tone/clarity | Built; judge is a **heuristic in mock mode**, a live LLM when a key is set | `metrics.judge`, `prompts/judge_v1.md` |
| Pass rate logged per run | Built | `evals/runs.jsonl`, `--report`, Evals tab |
| Versioned prompts per agent (v1, v2, ...) | Built: files + `active.json` + env override. Intake has v1 and v2; the others have v1 only | `prompts/` |
| Structured JSON validated with Pydantic | Built (parse, retry once, fall back) | `llm.structured_call` |
| Changelog: what changed and which score moved | Built, and honest: LLM scores say "not yet measured" | `prompts/CHANGELOG.md` |
| HITL approval before any vendor message and before accepting a final quote | Built with LangGraph `interrupt()`; approve / edit / reject | `graph.gate_*` |
| Every escalation shows its reason | Built | gate payload + UI |
| Operator screen: per-service status, one-line reasoning, approve/edit, confidence label | Built, custom page | `static/trips.html` |
| Guardrails: round + discount caps, no commitments/payments unapproved, full audit | Built. No payment capability exists at all | `dispatch_draft`, `HARD_*` constants |

## Numbers from the current build

The current results are in [`docs/eval-results.md`](../docs/eval-results.md), generated from `evals/runs.jsonl`.
On 100 trips (25 core + 75 messy or adversarial), LLM agents vs the rules engine on the same simulated vendors:
task completion 100% vs 86%, price-limit violations 0 vs 4, sends without approval 0 vs 0, escalation precision /
recall 1.00 / 1.00 vs 0.87 / 0.90, intake field accuracy 99.9% vs 97.4%, $0.0117 and 74 s per trip on a small hosted
model. The extended set was written after the rules engine and is what it was not tuned on.

**Read these correctly.** The vendors are a deterministic simulator I wrote, and the dataset, simulator and agents
share an author. The operator in evals is scripted to approve every gate. One run per system. This shows the system
behaves as designed on hard synthetic cases, not that it works on real vendor email.

## What the resume can and cannot claim

Safe (the prototype and the logged run show it):
* Built an LLM multi-agent prototype (LangGraph, native tool calling, RAG) with a human approval gate: intake,
  sourcing, reply-reading and negotiation agents over one shared trip state; the negotiation agent chooses accept,
  counter (including the amount) or escalate by calling tools.
* On 100 synthetic trips: 100% task completion (rules baseline 86%), 0 unapproved sends, 0 price-limit violations,
  escalation recall 1.00 (precision 1.00), about $0.01 per trip.
* Limits enforced in code inside the tools (counter range, round cap, accept ceiling, must-escalate cases), with a
  rules engine as guardrail and fallback; a scripted hostile model and an independent review were used to try to
  break them.
* Eval harness comparing the LLM path to the rules baseline, scored against simulator ground truth, with cost and
  latency per trip.

Not safe:
* Real vendors, real prices, or "tested with operators" (all synthetic).
* Neural embeddings (the retriever is hashing TF-IDF in Chroma), pgvector/Supabase, inbound WhatsApp.
* "Improved X% across prompt versions": only one prompt set has an LLM run.
* Generalising the 100% figures beyond this synthetic set.

Interview line, accurate version:
* "The model decides, the code holds the line. The negotiation agent reads the thread through tools and proposes a
  counter or an accept; the tool refuses anything outside the price limits and the agent corrects itself. On 100
  synthetic trips it completed every one with zero limit breaches; the rules baseline completed 86% and broke the
  limits four times, booking tax-exclusive 'agreements' as if tax were included."

## Known limits
Synthetic KB; deterministic simulated vendors; lexical embeddings; confidence label is rule-derived, not calibrated;
no authentication on the API; one operator, no concurrency model beyond a process lock; the old single-vendor dashboard
(`/`) and the trip desk (`/trips`) are separate flows.

## Running the prompt-version eval for free

Two zero-cost routes; both write labelled rows to `msafara/evals/runs.jsonl`.

A. A local model server, no account and no key (needs ~8 GB RAM; runs on your own machine). Start any server that
   exposes a chat endpoint at /api/chat, then:

    export MSAFARA_LLM_PROVIDER=local MSAFARA_MODEL=<your local model tag>
