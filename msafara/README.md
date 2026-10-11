# Msafara trip workflow: what is built, what is measured, what you can claim

This package adds the multi-agent trip workflow to the vendor-coordination platform. Run it with
`python app.py` and open `/trips`. Tests: `pip install pytest httpx && pytest` (76 tests).
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

## Numbers from the current build (mock mode, no API key)

Run `intake-v2 (mock rules)` over 25 synthetic trips: 25/25 cases pass every check; bound violations 0 across
31 counter-offers; escalation precision 1.0 and recall 1.0 (9 true positives); message grounding 100%;
unapproved sends 0 of 332; vendor-match precision 1.0.
`intake-v1 (mock rules)` on the same trips: 1/25 pass, extraction 66%, escalation precision 0.43.

**Read these correctly.**
* Vendors are simulated by a deterministic simulator I wrote, and the dataset, simulator and agents share an author.
  25/25 shows the pipeline and guardrails are internally consistent and regression-tested. It does not show the
  system works on real vendor email.
* The metrics can fail: `tests/test_evals.py` sabotages the system (counter below floor, 20% discounts, invented
  prices, floor leaks, never/always escalating, blacklisted vendor) and checks the metrics notice.
* Mock "intake v1 vs v2" compares two regex parsers, not two prompts. v2 was tuned against the 25 cases.
  The honest generalisation number is the held-out set: v2 91.7% field accuracy, 8 of 12 fully exact; v1 60.4%.
  Known v2 misses: "check-in 14 September, 5 nights", "between March 3 and March 8", "8 friends", "group size: 18".
* **No run with a real LLM has been made.** There is no measured improvement across LLM prompt versions yet.

## What the resume can and cannot claim

Safe (the prototype shows it):
* Built a LangGraph multi-agent workflow (intake, vendor matching, outreach, negotiation, escalation) over one shared trip state.
* RAG over vendor profiles, rate cards, past quotes, negotiation logs and operator policy, in Chroma, filtered by vendor ID plus semantic ranking; every price in an outreach or counter-offer is checked against retrieved data.
* Negotiation bounds (max 10% below quote, never below floor, max 2 rounds) enforced in code; 0 violations in 25 simulated trips (31 counter-offers) and tests that try to break them.
* Human-in-the-loop with LangGraph interrupts: approve / edit / reject before any vendor message and before accepting a quote; full audit log per trip.
* Eval harness: 25 synthetic trips, 6 metric families, rule-based checks + LLM-judge hook, pass rate logged per run; Pydantic-validated JSON outputs; versioned prompt files with a changelog.

Not safe yet:
* "Improved pass rate across prompt versions" for LLM prompts: not measured. To earn it, set `LLM_API_KEY` and run
  `MSAFARA_PROMPT_INTAKE=v1 python -m msafara.evals.run --label intake-v1` then the same with `v2`, then `--report`.
  Claim only what that table shows (the scores may well not improve).
* "Vector search with neural embeddings" (it is hashing TF-IDF), pgvector/Supabase (not used), real vendors or real prices
  (all fictional), "tested with real operators", inbound WhatsApp handling (outbound only; replies are pasted in),
  a separate LLM vendor-matching agent (matching is retrieval + scoring).

Interview line, accurate version:
* RAG: "Retrieves vendor profiles, rate cards, past quotes, negotiation logs and operator policies from Chroma by vendor ID plus semantic match; outreach and counter-offers are anchored on a vendor's own past quotes, and any LLM-written message that contains an ungrounded price is rejected and replaced by a template."
* Evals: "I run 25 synthetic trips against simulated vendors and score extraction accuracy, vendor-match relevance, bound violations, escalation precision and recall, and rate grounding, logging a pass rate per run. Bound violations are 0 across 31 counter-offers. I also check the checks: tests sabotage the agent and confirm the metrics catch it. The rule-based baseline is logged; LLM prompt-version comparison is the next run."

## Known limits
Synthetic KB; deterministic simulated vendors; lexical embeddings; confidence label is rule-derived, not calibrated;
no authentication on the API; one operator, no concurrency model beyond a process lock; the old single-vendor dashboard
(`/`) and the trip desk (`/trips`) are separate flows.

## Running the prompt-version eval for free

Two zero-cost routes; both write labelled rows to `msafara/evals/runs.jsonl`.

A. Local model server, no account and no key (needs ~8 GB RAM; runs on your own machine):

    local pull local-model          # or a smaller tag such as local-model
    export MSAFARA_LLM_PROVIDER=local MSAFARA_MODEL=local-model
    bash scripts/run_prompt_eval.sh

B. Free hosted-model API key (no card):

    export MSAFARA_LLM_KEY=...       # keep out of the repo; .env is gitignored
    bash scripts/run_prompt_eval.sh

Results are labelled with the model used and support only "ran prompt versions v1 and v2 on <that model>", not any claim about the default model.
