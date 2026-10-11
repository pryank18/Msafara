# Prompt changelog

Rule: an entry may only quote a score that was actually measured. The eval harness
(`python -m msafara.evals.run`) logs every run to `evals/runs.jsonl`; `--report` prints the history.

**Two kinds of score exist, and they are not interchangeable:**
* `mock` runs use rule-based stand-ins (no API key). Their numbers describe the rule-based baseline
  and the pipeline around it. They are NOT evidence that a prompt works with an LLM.
* `llm` runs use a real model with the versioned prompt files below. These are the only numbers that
  can support a prompt-improvement claim. **No llm run has been recorded yet.**

| Agent | Version | Date | What changed | Eval score moved |
|---|---|---|---|---|
| intake | v1 | 2026-10-12 | First version: field list only. | llm: not yet measured. mock-parser v1 (weak rules, see below): extraction field-accuracy recorded in runs.jsonl |
| intake | v2 | 2026-10-12 | Added explicit rules: DD/MM/YYYY, "N nights" arithmetic, words for numbers, lakh/k/Indian grouping, currency defaults per city, city aliases, and "never guess, list missing fields". Reason: these are the formats v1's rule-based twin fails on. | llm: not yet measured. mock-parser v2: see runs.jsonl |
| outreach | v1 | 2026-10-12 | First version: grounded-price rule (only the vendor's own past-quote anchor), no floor/markup/budget disclosure. | not yet measured with an LLM (template path scored in mock runs) |
| parse_quote | v1 | 2026-10-12 | First version: status taxonomy incl. tax_unclear/partial/ambiguous, tax-inclusive normalisation, honest confidence. | not yet measured with an LLM (rule parser scored in mock runs) |
| counter | v1 | 2026-10-12 | First version: amount is computed by code and only worded by the LLM. | not yet measured with an LLM |
| judge | v1 | 2026-10-12 | First version of the LLM-as-judge rubric. | not yet measured with an LLM (heuristic judge used in mock runs) |

## How "mock" intake v1/v2 relate to the prompt files
`MSAFARA_MOCK_INTAKE=v1|v2` selects between a deliberately basic and a more complete regex parser so the
eval harness can show a score moving between versions without an API key. The prompt files `intake_v1.md`
and `intake_v2.md` are what the LLM would see; their effect has to be measured with `LLM_API_KEY` set:

    MSAFARA_PROMPT_INTAKE=v1 python -m msafara.evals.run --label intake-v1
    MSAFARA_PROMPT_INTAKE=v2 python -m msafara.evals.run --label intake-v2
    python -m msafara.evals.run --report
