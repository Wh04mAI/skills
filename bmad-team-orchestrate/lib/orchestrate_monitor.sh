#!/bin/bash
# orchestrate-monitor.sh — One-shot poll: discover pending events on claimed PRs.
# Output: JSON lines, one per event. Empty = nothing to do.
# Intended to be called repeatedly by the orchestrator (Claude session).
#
# Events:
#   {"event":"decision","pr":88,"story":"8-1-otel","agent":"claudeA","question":"..."}
#   {"event":"approval_needed","pr":88,"story":"8-1-otel"}
#
# Note: code review is NOT triggered by the monitor. Dev agents run
# `/bmad-team-review` themselves before posting COMPLETION (see
# .claude/skills/bmad-team-review/ and AGENTS.md "Pre-merge self-review").
# The monitor watches for two things:
#   1. DECISION markers that need an orchestrator response
#   2. PRs that finished (COMPLETION + green CI + no APPROVAL yet) so the
#      orchestrator knows to verify and post ORCHESTRATE:APPROVAL — without
#      this, agents block forever polling for a marker no one produces.
# Human always does the merge.

set -euo pipefail

POLL_INTERVAL="${1:-60}"

# Resolve [github].required_checks from base + project-override TOML so
# approval_needed isn't blocked by optional/experimental checks. Empty
# string falls back to "all non-skipped must be SUCCESS".
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIRED_CHECKS_JSON=$(python3 - "$SKILL_DIR" <<'PY' 2>/dev/null || echo "[]"
import json, pathlib, sys, tomllib
skill = pathlib.Path(sys.argv[1])
cfg = tomllib.loads((skill / "customize.toml").read_text())
override = pathlib.Path.cwd() / "_bmad" / "custom" / "orchestrate.toml"
if override.exists():
    extra = tomllib.loads(override.read_text())
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
print(json.dumps(cfg.get("github", {}).get("required_checks", [])))
PY
)

# Get all open PRs for the current author
PRS=$(gh pr list --author @me --state open --json number,title,headRefName,isDraft 2>/dev/null)

if [ -z "$PRS" ] || [ "$PRS" = "[]" ]; then
  exit 0
fi

echo "$PRS" | jq -c '.[]' | while read -r pr; do
  PR_NUMBER=$(echo "$pr" | jq -r '.number')
  IS_DRAFT=$(echo "$pr" | jq -r '.isDraft')

  # Get comments and check markers
  COMMENTS=$(gh pr view "$PR_NUMBER" --json comments 2>/dev/null)

  # Detect DECISION without RESPONSE
  DECISIONS=$(echo "$COMMENTS" | jq '[.comments[] | select(.body | contains("ORCHESTRATE:DECISION"))]' 2>/dev/null || echo "[]")
  RESPONSES=$(echo "$COMMENTS" | jq '[.comments[] | select(.body | contains("ORCHESTRATE:RESPONSE"))]' 2>/dev/null || echo "[]")

  PENDING_DECISIONS=$(echo "$DECISIONS" | jq --argjson responses "$RESPONSES" '
    [.[] | select(.body as $d |
      ($responses | any(.body | contains("ORCHESTRATE:RESPONSE") and (.createdAt > $d.createdAt))) | not
    )]
  ' 2>/dev/null || echo "[]")

  HAS_COMPLETION=$(echo "$COMMENTS" | jq '[.comments[].body | select(contains("ORCHESTRATE:COMPLETION"))] | length > 0' 2>/dev/null || echo "false")
  HAS_APPROVAL=$(echo "$COMMENTS" | jq '[.comments[].body | select(contains("ORCHESTRATE:APPROVAL"))] | length > 0' 2>/dev/null || echo "false")

  # Determine story ID from CLAIM marker
  STORY=$(echo "$COMMENTS" | jq -r '[.comments[].body | capture("ORCHESTRATE:CLAIM[^>]*story=(?<s>[^ ]+)") | .s] | first' 2>/dev/null || echo "")

  # --- Emit events ---

  # Pending DECISION
  PENDING_COUNT=$(echo "$PENDING_DECISIONS" | jq 'length' 2>/dev/null || echo "0")
  if [ "$PENDING_COUNT" -gt 0 ]; then
    FIRST_Q=$(echo "$PENDING_DECISIONS" | jq -r '.[0].body | capture("\\*\\*Question\\**:\\s*(?<q>[^\\n]+)") | .q' 2>/dev/null || echo "pending decision")
    AGENT=$(echo "$PENDING_DECISIONS" | jq -r '.[0].body | capture("ORCHESTRATE:DECISION[^>]*agent=(?<a>[^ ]+)") | .a' 2>/dev/null || echo "unknown")
    echo "{\"event\":\"decision\",\"pr\":$PR_NUMBER,\"story\":\"$STORY\",\"agent\":\"$AGENT\",\"question\":\"$FIRST_Q\",\"count\":$PENDING_COUNT}"
  fi

  # PR finished — needs orchestrator to post APPROVAL.
  # Conditions: ready (not draft) + COMPLETION posted + no APPROVAL yet
  #   + CI green + no pending DECISION (an unanswered escalation must
  #   not be jumped past).
  # CI green means: every required check (per [github].required_checks) is
  # SUCCESS. If required_checks is empty, fall back to "every non-skipped
  # check must be SUCCESS" — keeps the original behavior for unconfigured
  # projects but lets configured projects ignore optional/experimental jobs.
  if [ "$IS_DRAFT" = "false" ] && [ "$HAS_COMPLETION" = "true" ] && [ "$HAS_APPROVAL" = "false" ] && [ "$PENDING_COUNT" -eq 0 ]; then
    STATUS=$(gh pr view "$PR_NUMBER" --json statusCheckRollup 2>/dev/null)
    if [ "$(echo "$REQUIRED_CHECKS_JSON" | jq 'length' 2>/dev/null || echo 0)" -gt 0 ]; then
      CI_GREEN=$(echo "$STATUS" | jq --argjson req "$REQUIRED_CHECKS_JSON" '
        ($req | map({(.): true}) | add) as $needed
        | [.statusCheckRollup[] | select($needed[.name])]
        | (length == ($req | length))
          and all(.conclusion == "SUCCESS")
      ' 2>/dev/null || echo "false")
    else
      CHECKS=$(echo "$STATUS" | jq '[.statusCheckRollup[] | select(.conclusion != "SKIPPED") | .conclusion]' 2>/dev/null || echo "[]")
      HAS_CHECKS=$(echo "$CHECKS" | jq 'length > 0' 2>/dev/null || echo "false")
      ALL_SUCCESS=$(echo "$CHECKS" | jq 'all(. == "SUCCESS")' 2>/dev/null || echo "false")
      if [ "$HAS_CHECKS" = "true" ] && [ "$ALL_SUCCESS" = "true" ]; then
        CI_GREEN=true
      else
        CI_GREEN=false
      fi
    fi
    if [ "$CI_GREEN" = "true" ]; then
      echo "{\"event\":\"approval_needed\",\"pr\":$PR_NUMBER,\"story\":\"$STORY\"}"
    fi
  fi
done
