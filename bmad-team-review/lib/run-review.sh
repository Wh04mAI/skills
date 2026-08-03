#!/usr/bin/env bash
# Run a single reviewer pass and emit the review output.
#
# Usage:
#   run-review.sh <reviewer> <base> <pr_number> <round>
#
# reviewer:   codex | claude | deepseek
# base:       base branch (e.g. dev)
# pr_number:  used for output filename
# round:      review round (1, 2, ...)
#
# Output: writes to /tmp/cross-review-<pr>-<round>-<reviewer>.md
# Exit:   0 on success, non-zero if reviewer not available
#
# Override per host by sourcing customize.sh in your shell profile.

set -euo pipefail

REVIEWER="$1"
BASE="$2"
PR="$3"
ROUND="$4"
TITLE="${REVIEW_TITLE:-bmad-team-review}"

OUT="/tmp/cross-review-${PR}-${ROUND}-${REVIEWER}.md"

# Per-host overrides. Look first beside the script (project skill);
# fall back to a global ~/.claude location if present.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for CUSTOMIZE in "$SKILL_DIR/customize.sh" "$HOME/.claude/skills/bmad-team-review/customize.sh"; do
    if [[ -f "$CUSTOMIZE" ]]; then
        # shellcheck disable=SC1090
        source "$CUSTOMIZE"
        break
    fi
done

case "$REVIEWER" in
    codex)
        # Prefer the openai/codex-plugin-cc companion runtime when the
        # plugin is installed: it routes through a shared (warm) codex
        # session and logs each run as a job, so other Claude Code
        # sessions can observe progress via `/codex:status`. Fall back
        # to the direct `codex` CLI otherwise.
        #
        # The companion path is versioned, so glob and pick the highest.
        COMPANION=""
        for candidate in $(ls -d "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | sort -V); do
            COMPANION="$candidate"
        done

        if [[ -n "$COMPANION" && -f "$COMPANION" ]] && command -v node >/dev/null 2>&1; then
            echo "[codex reviewer] via codex-plugin-cc companion ($(basename "$(dirname "$(dirname "$COMPANION")")")) — warm runtime, /codex:status observable" >&2
            # companion's review subcommand does not accept --title; it
            # also reviews working-tree or branch-vs-base. We force base
            # review here because the skill always passes a base.
            node "$COMPANION" review --wait --base "$BASE" 2>&1 | tee "$OUT"
        elif command -v codex >/dev/null 2>&1; then
            echo "[codex reviewer] via direct codex CLI — no shared runtime, no /codex:status visibility" >&2
            echo "[codex reviewer] tip: install codex-plugin-cc for warm runtime + progress polling:" >&2
            echo "                 /plugin marketplace add openai/codex-plugin-cc && /plugin install codex@openai-codex" >&2
            codex review --base "$BASE" --title "$TITLE" 2>&1 | tee "$OUT"
        else
            echo "ERROR: neither codex-plugin-cc companion nor codex CLI found" >&2
            exit 2
        fi
        ;;

    claude)
        # Anthropic SDK CLI ("claude" command). Adjust the model flag and
        # prompt template to taste in customize.sh.
        if ! command -v claude >/dev/null 2>&1; then
            echo "ERROR: claude CLI not found in PATH" >&2
            exit 2
        fi
        DIFF=$(git diff "${BASE}...HEAD" || true)
        STAT=$(git diff --stat "${BASE}...HEAD" || true)
        PROMPT=$(cat <<EOF
You are reviewing a code diff. Your job: find substantive bugs, contract
violations, spec misalignments, missing edge cases, and architectural
issues. Mark each finding with one of [P0] / [P1] / [P2] / [P3] tags
where P0 = blocker, P1 = must fix, P2 = should fix, P3 = nit / style.

Skip pure style suggestions unless they cause real bugs (e.g. shadowed
variables that hide errors). Do not invent issues to fill space; if the
diff is clean, say so.

Output format:
- Two-paragraph summary at top.
- Then "### Findings" section with one bullet per finding:
  "- [P<N>] <one-line title> — <file:line>" followed by 1-3 sentences of
  explanation indented under the bullet.

Diff stat:
${STAT}

Diff:
${DIFF}
EOF
)
        # Stream mode: print char-count progress to stderr so callers can
        # see liveness; extract final review text to $OUT.
        # ${CLAUDE_REVIEW_MODEL:-claude-opus-4-7} is overridable via customize.sh
        printf '%s\n' "$PROMPT" \
            | claude \
                --model "${CLAUDE_REVIEW_MODEL:-claude-opus-4-7}" \
                --print --verbose \
                --output-format stream-json \
                --include-partial-messages \
                --no-session-persistence \
                2>/dev/null \
            | python3 -c '
import sys, json
chars = 0
result = ""
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    t = ev.get("type", "")
    if t == "stream_event":
        inner = ev.get("event", {})
        if inner.get("type") == "content_block_delta":
            text = inner.get("delta", {}).get("text", "")
            if text:
                chars += len(text)
                print(f"\r[claude reviewer] streaming... {chars:,} chars", end="", file=sys.stderr, flush=True)
    elif t == "result":
        result = ev.get("result", "")
        if ev.get("is_error") or ev.get("subtype") == "error":
            err = ev.get("result") or str(ev)
            print(f"\n[claude reviewer] API error: {err}", file=sys.stderr, flush=True)
            sys.exit(1)
        print(f"\r[claude reviewer] done — {len(result):,} chars          ", file=sys.stderr, flush=True)
print(result)
' | tee "$OUT"
        ;;

    deepseek)
        # User's DeepSeek setup is host-specific. customize.sh should
        # define DEEPSEEK_REVIEW_CMD, e.g.
        #   export DEEPSEEK_REVIEW_CMD="deepseek-coder review --base"
        # If not defined, fall back to codex (and the runner script will
        # log the fallback in the review footer).
        if [[ -z "${DEEPSEEK_REVIEW_CMD:-}" ]]; then
            echo "WARNING: DEEPSEEK_REVIEW_CMD not set in customize.sh; falling back to codex" >&2
            exec "$0" codex "$BASE" "$PR" "$ROUND"
        fi
        # shellcheck disable=SC2086
        $DEEPSEEK_REVIEW_CMD "$BASE" 2>&1 | tee "$OUT"
        ;;

    *)
        echo "ERROR: unknown reviewer '$REVIEWER'" >&2
        exit 2
        ;;
esac

echo
echo "Review written to: $OUT" >&2
