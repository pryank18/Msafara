#!/usr/bin/env bash
# Free prompt-version eval. Pick ONE backend:
#   Ollama (no signup, runs on your machine):  ollama pull qwen2.5:7b ; export MSAFARA_LLM_PROVIDER=ollama
#   Free hosted-model API key:                 export MSAFARA_LLM_KEY=... MSAFARA_HOSTED_URL=... MSAFARA_HOSTED_MODEL=...
# Then: bash scripts/run_prompt_eval.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${MSAFARA_LLM_PROVIDER:-}" = "ollama" ]; then M="ollama/${MSAFARA_MODEL:-qwen2.5:7b}"; else M="${MSAFARA_HOSTED_MODEL:-${MSAFARA_MODEL:-hosted}}"; fi
for v in v1 v2; do
  MSAFARA_PROMPT_INTAKE=$v python -m msafara.evals.run --intake-only --label "intake-$v ($M)"
done
python -m msafara.evals.run --report
