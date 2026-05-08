#!/usr/bin/env bash
# differential_probe.sh — gold-standard backend identity verification.
#
# Baseline: local 'claude' CLI (your OAuth/Max subscription, hits api.anthropic.com)
# Suspect:  docker container running real Claude Code with ANTHROPIC_BASE_URL=ENDPOINT
#
# Same model ID, same prompts, same SDK — only the backend URL differs. Identical
# responses across both => suspect serves real model. Divergence => mismatch.
#
# Why this works:
# - Both sides use real @anthropic-ai/claude-code, so TLS / headers / API shape are
#   identical (suspect can't fingerprint-discriminate against the suspect call).
# - Anthropic baseline is ground truth.
# - Hard-fact probes (release dates, model IDs, post-cutoff events) cannot be faked
#   by system-prompt injection alone — they require the actual training data.

set -euo pipefail

ENDPOINT=${ENDPOINT:-}
TOKEN=${TOKEN:-}
MODEL=${MODEL:-claude-opus-4-7}
DOCKER_IMAGE=${DOCKER_IMAGE:-mfi-claude-code:latest}

usage() {
  cat >&2 <<EOF
Usage: ENDPOINT=https://... TOKEN=sk-... MODEL=... $0

Required env vars:
  ENDPOINT   suspect gateway URL (e.g. https://wcnbai.com)
  TOKEN      API token for the suspect endpoint
  MODEL      claimed model ID (default: claude-opus-4-7)

Prerequisites:
  - 'claude' CLI installed and authenticated locally (claude --version)
  - Docker image built:
      docker build -t $DOCKER_IMAGE \\
        \$SKILL_DIR/docker/
EOF
  exit 2
}

[[ -z "$ENDPOINT" || -z "$TOKEN" ]] && usage

command -v claude >/dev/null 2>&1 || { echo "ERROR: 'claude' CLI not found in PATH" >&2; exit 3; }
command -v docker >/dev/null 2>&1 || { echo "ERROR: 'docker' not found in PATH" >&2; exit 3; }

# Hard-fact probes (require real training data; resistant to prompt injection)
PROMPTS=(
  "P1_post_cutoff_event|Who won the 2025 Nobel Prize in Physics? If unknown say exactly 'unknown'. One line answer only."
  "P2_recent_model_id|Quote the official Anthropic API model ID for Claude Haiku 4.5 with the date suffix. If unknown say exactly 'unknown'. One line only."
  "P3_in_window_event|When was claude-opus-4-20250514 (Claude Opus 4) first released? Just YYYY-MM-DD or 'unknown'. One line only."
  "P4_cutoff_self|Your knowledge cutoff date? Format YYYY-MM (year-month). One line only."
)

normalize() {
  # Trim, collapse whitespace, strip backticks for fair comparison
  tr '\n' ' ' | sed 's/  */ /g; s/^ *//; s/ *$//; s/`//g'
}

probe_baseline() {
  claude -p "$1" --model "$MODEL" --output-format text 2>/dev/null | head -5 | normalize
}

probe_suspect() {
  docker run --rm \
    -e ANTHROPIC_BASE_URL="$ENDPOINT" \
    -e ANTHROPIC_API_KEY="$TOKEN" \
    "$DOCKER_IMAGE" \
    -p "$1" --model "$MODEL" --output-format text 2>/dev/null | head -5 | normalize
}

echo "================================================"
echo "Differential Probe Report"
echo "  model:    $MODEL"
echo "  baseline: local 'claude' (api.anthropic.com)"
echo "  suspect:  docker → $ENDPOINT"
echo "================================================"

match=0
total=0
for entry in "${PROMPTS[@]}"; do
  NAME=${entry%%|*}
  PROMPT=${entry#*|}
  total=$((total + 1))

  BASELINE=$(probe_baseline "$PROMPT" || echo "[error]")
  SUSPECT=$(probe_suspect "$PROMPT" || echo "[error]")

  echo
  echo "--- $NAME ---"
  echo "  baseline: $BASELINE"
  echo "  suspect:  $SUSPECT"
  if [ "$BASELINE" = "$SUSPECT" ]; then
    echo "  -> MATCH ✓"
    match=$((match + 1))
  else
    echo "  -> DIVERGE ⚠️"
  fi
done

echo
echo "================================================"
echo "Score: $match/$total exact matches"
if [ "$match" -eq "$total" ]; then
  echo "Verdict: backend appears genuine (full match on hard facts)"
elif [ "$match" -ge $((total * 3 / 4)) ]; then
  echo "Verdict: likely genuine; review divergences for interpretive vs factual differences"
else
  echo "Verdict: divergence dominant — backend likely substituted"
fi
echo "================================================"
