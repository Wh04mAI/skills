# Per-host overrides for cross-model-review.
# Sourced by lib/run-review.sh before the reviewer dispatch.
# Edit this file to set CLI commands and flags that vary by host.

# Claude reviewer model (anthropic SDK CLI). Default: claude-opus-4-7.
# Use a different model than the writer.
# export CLAUDE_REVIEW_MODEL="claude-opus-4-7"

# DeepSeek review command. The script will pass <base> as the last arg.
# Examples:
#   export DEEPSEEK_REVIEW_CMD="deepseek-coder review --base"
#   export DEEPSEEK_REVIEW_CMD="ollama run deepseek-coder:33b /tmp/review-prompt.txt --base"
# If unset, run-review.sh falls back to codex.
# export DEEPSEEK_REVIEW_CMD=""
