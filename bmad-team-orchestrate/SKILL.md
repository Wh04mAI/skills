---
name: bmad-team-orchestrate
description: Multi-agent orchestrator for parallel story implementation. Compact-by-default CLI; dispatch copies the first prompt to clipboard so users paste it into a new agent session. `--monitor` makes Claude the orchestrator — polls PR events and handles them in-session (decisions, approval). Code review is run by the dev agent itself via `/bmad-team-review` before posting COMPLETION. Invoke with `/orchestrate` (slash command alias retained for ergonomics).
metadata:
  part-of: bmad-team
---

# bmad-team-orchestrate

> Part of [BMAD-Team](../bmad-team/charter.md). Consumes the marker protocol defined in [protocol.md](../bmad-team/protocol.md). User-facing slash command is `/orchestrate` (short alias).

Multi-agent orchestrator for parallel story implementation. Output is
compact by default (status ≤ 8 lines, dispatch ≤ 5 lines) so it doesn't
flood the conversation.

## How to invoke

When the user runs `/orchestrate <args>`:

- **status / dispatch / list**: run `orchestrate.py` directly
- **`--monitor`**: I (Claude) become the orchestrator — see Monitor section below

```bash
# For status/dispatch/list only
.claude/skills/bmad-team-orchestrate/orchestrate.py <args>
```

No `--quiet` / `--verbose` flags — the script self-throttles.

## Commands

| Command | Action |
|---|---|
| `/orchestrate` | Status + next-step hint (local-only, < 1s) |
| `/orchestrate <ids...>` | Dispatch stories. First prompt → clipboard, rest → `/tmp/orchestrate-prompts/` |
| `/orchestrate --all` | Dispatch all ready (capped at `max_concurrent_agents`) |
| `/orchestrate --bundle <ids...>` | Bundle stories to one agent (serial) |
| `/orchestrate --agent-id claudeD …` | Override agent slot (**must** pair with **`--bundle` or exactly one story id**) |
| `/orchestrate --monitor` | **Orchestrator mode** — poll PR events, process in-session |
| `/orchestrate --list` | List every ready story |
| `/orchestrate --dry-run <ids...>` | Preview dispatch without writing state |
| `/orchestrate --help` | Full help |

Story IDs accept shorthand (`6.1`, `6-1`) or full names. Dispatch
auto-resolves and errors with fuzzy suggestions on miss.

By default dispatch picks the first free `claudeA`, `claudeB`, … by
consulting **`ORCHESTRATE:CLAIM` markers on open PRs** (`gh`; honors
`[monitor].author_filter`, e.g. `@me`; on `gh` failure it falls open).
Recopy hints use **`python3 -c … pbcopy`** on macOS (`pbcopy < file`
truncates large prompts).

## Monitor — Orchestrator Mode

When the user runs `/orchestrate --monitor`, I (Claude) become the
orchestrator. I don't start a background daemon — instead:

1. **Start Monitor tool** running `lib/orchestrate_monitor.sh` (polling loop)
2. **Events arrive as notifications** in the conversation
3. **I process each event in-session**, then resume monitoring

### Event handling

| Event | Action |
|---|---|
| `decision` | Agent posted DECISION → apply `decision-rules.yaml` or ask user |
| `approval_needed` | PR ready + COMPLETION posted + CI green + no APPROVAL yet → verify and post `ORCHESTRATE:APPROVAL` |

**Code review is not a monitor event.** Dev agents run
`/bmad-team-review` themselves before posting COMPLETION (skill at
`.claude/skills/bmad-team-review/`, contract in AGENTS.md
"Pre-merge self-review"). The orchestrator only weighs in when the
agent escalates a residual issue via `ORCHESTRATE:DECISION` or when a
finished PR needs APPROVAL.

For `decision` events, post the response directly to the PR via
`gh pr comment` with an `ORCHESTRATE:RESPONSE` marker — no user
confirmation when `decision-rules.yaml` matches. Escalate to the user
only when no rule matches and the decision is genuinely ambiguous.

For `approval_needed` events, sanity-check the PR (CI markers match
expectations, no unresolved DECISION, scope matches the claim), then
post `<!-- ORCHESTRATE:APPROVAL -->`. Human still does the actual
merge — APPROVAL just unblocks the dev agent's polling loop.

### Poll interval

The monitor script polls every 60 seconds via `gh` CLI. On each poll
it discovers active PRs and outputs JSON events for unanswered
DECISION markers and PRs that have reached COMPLETION + green CI but
have no APPROVAL yet.

### Stop

The Monitor tool runs persistently. Stop with:
- `Ctrl+C` in the terminal, or
- Ask me to stop monitoring

## Decision Protocol

All orchestrator ↔ sub-agent messaging flows through GitHub PR
comments with `<!-- ORCHESTRATE:* -->` markers — no local queue file.

- `CLAIM`: agent claims story (posted on PR open)
- `DECISION`: agent asks orchestrator (auto-decided or escalated)
- `RESPONSE`: orchestrator answers (auto-rule or human)
- `COMPLETION`: agent reports done after running `/bmad-team-review`
- `REVIEW`: review findings posted by the dev agent's bmad-team-review run (not the monitor)
- `APPROVAL`: orchestrator approves merge (human always does the merge)

## Workflow

1. `/orchestrate` — see what's ready and what to do next.
2. `/orchestrate 6.1 6.2` — first prompt is on the clipboard; paste
   it into a fresh agent session. For `claudeB`, run the printed
   `pbcopy <` command and paste again.
3. `/orchestrate --monitor` — enter orchestrator mode. Poll PRs,
   respond to decisions, post approval. Code review is the dev agent's
   job (`/bmad-team-review` before COMPLETION); human is consulted
   only for escalated decisions.

## Adapters

- **github_pr** (default for vm-control-plane): derives story status from GitHub PR state (merged/draft/ready) + story file `Status:` fallback
- **file_scanner**: scans story files in filesystem (any project)

## Configuration

- Base: `.claude/skills/bmad-team-orchestrate/customize.toml`
- Project override: `_bmad/team/orchestrate.toml` (lives alongside the unified `_bmad/team/config.yaml`; skill-internal knobs stay in TOML, cross-skill shared values go in `config.yaml`)
- Decision rules: `.claude/skills/bmad-team-orchestrate/decision-rules.yaml`
- Logs: `~/.claude/logs/orchestrate-YYYYMMDD.log`

## State

Stateless across runs. Source of truth is GitHub:
- Active agents → discovered via PR `CLAIM` markers each `--monitor` poll
- Pending decisions → DECISIONs without matching RESPONSE
- Story status → derived from PR state (draft / ready / merged)

`/orchestrate` (status) reads story files and queries GitHub PR state
(~2 `gh` calls).
