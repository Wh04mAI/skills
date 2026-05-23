#!/usr/bin/env bash
# priority_tags.sh — manage MoSCoW + incident priority labels on a GitHub repo.
# Requires: gh (authenticated), jq.
set -euo pipefail

# name|color|description
LABELS=(
  "priority/now|B60205|Incident — drop everything; minutes-to-respond, hours-to-fix"
  "priority/must|D93F0B|Must ship this iteration"
  "priority/should|FBCA04|Important; can slip 1-2 iterations"
  "priority/could|0E8A16|Nice to have; backlog"
  "priority/wont|C5DEF5|Explicitly not this version"
  "needs-triage|BFD4F2|New / unprioritized; review at next triage"
)

VALID_LEVELS="now must should could wont"

usage() {
  cat <<'EOF'
priority-tags — MoSCoW + incident priority labels on a GitHub repo

Usage:
  priority-tags init                       Create/update the 6 labels (idempotent)
  priority-tags list                       Show labels + open counts
  priority-tags set <num> <level>          Apply priority (mutually exclusive)
                                           level: now|must|should|could|wont
  priority-tags clear <num>                Remove all priority/* labels
  priority-tags triage                     Issues needing triage
  priority-tags report                     Counts + health warnings

Flags:
  --repo OWNER/NAME    target repo (default: detected from cwd)
  -h, --help           show this help

Examples:
  priority-tags init
  priority-tags set 42 must
  priority-tags set 1337 now --repo Wh04mAI/skills
  priority-tags report
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "error: '$1' not installed or not on PATH" >&2; exit 127; }
}

resolve_repo() {
  if [[ -n "${REPO:-}" ]]; then echo "$REPO"; return; fi
  gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null \
    || { echo "error: cannot detect repo; pass --repo OWNER/NAME" >&2; exit 2; }
}

url_encode_label() {
  printf '%s' "$1" | sed 's|/|%2F|g'
}

current_priority_labels() {
  # echo each priority/* label currently on issue/PR #$1
  gh api "repos/$REPO/issues/$1/labels" --jq '.[].name | select(startswith("priority/"))' 2>/dev/null || true
}

# Count open issues + PRs carrying a given label. `gh issue list` returns
# issues only; since `cmd_set` accepts PR numbers too, aggregate queries
# must combine both feeds to avoid undercounting.
count_labeled_open() {
  local label=$1
  local n_issues n_prs
  n_issues=$(gh issue list --repo "$REPO" --label "$label" --state open --limit 1000 --json number --jq 'length' 2>/dev/null || echo 0)
  n_prs=$(gh pr list --repo "$REPO" --label "$label" --state open --limit 1000 --json number --jq 'length' 2>/dev/null || echo 0)
  echo $((n_issues + n_prs))
}

cmd_init() {
  # Snapshot all existing labels in one paginated call instead of per-label lookup
  # (saves N round-trips) and instead of a fixed --limit 200 (would miss labels on
  # repos with bigger taxonomies, then create-then-fail).
  local existing
  existing=$(gh api --paginate "repos/$REPO/labels" --jq '.[].name' 2>/dev/null || true)
  for entry in "${LABELS[@]}"; do
    IFS='|' read -r name color desc <<<"$entry"
    # Use a here-string, not `printf | grep -q`: under `set -o pipefail` the latter
    # returns 141 (SIGPIPE) when grep exits early on a match, which a strict caller
    # then misreads as "label missing" and tries to create-then-fail.
    if grep -Fxq "$name" <<<"$existing"; then
      gh label edit "$name" --repo "$REPO" --color "$color" --description "$desc" >/dev/null
      printf "updated   %s\n" "$name"
    else
      gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" >/dev/null
      printf "created   %s\n" "$name"
    fi
  done
}

cmd_list() {
  printf "Priority labels in %s:\n\n" "$REPO"
  printf "  %-22s %-6s %s\n" "LABEL" "OPEN" "DESCRIPTION"
  printf "  %-22s %-6s %s\n" "----------------------" "-----" "-----------"
  for entry in "${LABELS[@]}"; do
    IFS='|' read -r name _ desc <<<"$entry"
    local count
    count=$(count_labeled_open "$name")
    printf "  %-22s %-6s %s\n" "$name" "$count" "$desc"
  done
}

cmd_set() {
  local num=$1 level=$2
  case " $VALID_LEVELS " in
    *" $level "*) ;;
    *) echo "error: level must be one of: $VALID_LEVELS" >&2; exit 1 ;;
  esac
  local removed=0
  while IFS= read -r lbl; do
    [[ -z "$lbl" ]] && continue
    gh api -X DELETE "repos/$REPO/issues/$num/labels/$(url_encode_label "$lbl")" >/dev/null
    printf "removed   %s\n" "$lbl"
    removed=$((removed + 1))
  done < <(current_priority_labels "$num")
  printf '{"labels":["priority/%s"]}' "$level" \
    | gh api -X POST "repos/$REPO/issues/$num/labels" --input - >/dev/null
  printf "added     priority/%s\n" "$level"
  # Assigning a priority counts as triage — clear needs-triage if present (404 = wasn't there, ignore).
  if gh api -X DELETE "repos/$REPO/issues/$num/labels/needs-triage" >/dev/null 2>&1; then
    printf "removed   needs-triage (auto-cleared on triage)\n"
  fi
  [[ $removed -eq 0 ]] && printf "(no previous priority labels)\n"
}

cmd_clear() {
  local num=$1
  local removed=0
  while IFS= read -r lbl; do
    [[ -z "$lbl" ]] && continue
    gh api -X DELETE "repos/$REPO/issues/$num/labels/$(url_encode_label "$lbl")" >/dev/null
    printf "removed   %s\n" "$lbl"
    removed=$((removed + 1))
  done < <(current_priority_labels "$num")
  [[ $removed -eq 0 ]] && echo "(no priority labels to remove)"
}

cmd_triage() {
  echo "## Open issues carrying 'needs-triage':"
  gh issue list --repo "$REPO" --label "needs-triage" --state open --limit 1000 || true
  echo
  echo "## Open issues with NO priority/* label:"
  gh issue list --repo "$REPO" --state open --limit 1000 --json number,title,labels \
    --jq '.[] | select([.labels[].name] | map(startswith("priority/")) | any | not) | "#\(.number)\t\(.title)"'
}

cmd_report() {
  echo "Priority report — $REPO"
  echo "================================"
  # macOS ships bash 3.2 by default — avoid `declare -A`. Use named scalars
  # via printf -v + ${!var} indirect expansion, which work in bash 3.2+.
  local total=0
  local count_now=0 count_must=0 count_should=0 count_could=0 count_wont=0
  for level in now must should could wont; do
    local n
    n=$(count_labeled_open "priority/$level")
    printf -v "count_$level" '%d' "$n"
    printf "  priority/%-7s %4d open\n" "$level" "$n"
    total=$((total + n))
  done
  echo "  ------------------------"
  printf "  total prioritized:   %4d\n" "$total"

  # ---- health warnings ----
  if (( count_now > 0 )); then
    echo
    echo "OPEN INCIDENTS (priority/now):"
    gh issue list --repo "$REPO" --label "priority/now" --state open --limit 1000 --json number,title,createdAt \
      --jq '.[] | "  #\(.number)        age=\((now - (.createdAt | fromdateiso8601)) / 3600 | floor)h  \(.title)"'
    gh pr list --repo "$REPO" --label "priority/now" --state open --limit 1000 --json number,title,createdAt \
      --jq '.[] | "  #\(.number) (PR)  age=\((now - (.createdAt | fromdateiso8601)) / 3600 | floor)h  \(.title)"'
    local stale_i stale_p stale
    stale_i=$(gh issue list --repo "$REPO" --label "priority/now" --state open --limit 1000 --json createdAt \
      --jq '[.[] | select((now - (.createdAt | fromdateiso8601)) > 86400)] | length' 2>/dev/null || echo 0)
    stale_p=$(gh pr list --repo "$REPO" --label "priority/now" --state open --limit 1000 --json createdAt \
      --jq '[.[] | select((now - (.createdAt | fromdateiso8601)) > 86400)] | length' 2>/dev/null || echo 0)
    stale=$((stale_i + stale_p))
    if (( stale > 0 )); then
      echo
      echo "WARN: $stale incident(s) older than 24h — escalate, split, or downgrade. Incidents should be short-lived."
    fi
  fi
  if (( count_now > 3 )); then
    echo "WARN: more than 3 active incidents — likely systemic; consider a root-cause review."
  fi
  if (( count_must > 0 && total > 0 )); then
    local pct=$(( count_must * 100 / total ))
    if (( pct > 60 )); then
      echo "WARN: priority/must is ${pct}% of prioritized backlog — priority inflation. Move some to 'should' or 'could'."
    fi
  fi
}

main() {
  require_cmd gh
  require_cmd jq

  REPO="${REPO:-}"
  local args=()
  while [[ $# -gt 0 ]]; do
    case $1 in
      --repo) REPO="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) args+=("$1"); shift ;;
    esac
  done
  [[ ${#args[@]} -eq 0 ]] && { usage; exit 0; }
  set -- "${args[@]}"

  REPO=$(resolve_repo)

  local sub=$1; shift
  case "$sub" in
    init)   [[ $# -eq 0 ]] || { usage; exit 1; }; cmd_init ;;
    list)   [[ $# -eq 0 ]] || { usage; exit 1; }; cmd_list ;;
    set)    [[ $# -eq 2 ]] || { usage; exit 1; }; cmd_set "$1" "$2" ;;
    clear)  [[ $# -eq 1 ]] || { usage; exit 1; }; cmd_clear "$1" ;;
    triage) [[ $# -eq 0 ]] || { usage; exit 1; }; cmd_triage ;;
    report) [[ $# -eq 0 ]] || { usage; exit 1; }; cmd_report ;;
    help|"") usage ;;
    *) echo "error: unknown subcommand '$sub'" >&2; usage; exit 1 ;;
  esac
}

main "$@"
