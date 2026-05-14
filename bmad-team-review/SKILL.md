---
name: bmad-team-review
description: Self-driven code review by a model different from the writing model. Dev agent invokes this before posting COMPLETION. Skill picks a reviewer (codex / claude / deepseek) based on what the PR touches, runs the review, and decides whether to fixup, post completion, or escalate. Use when the dev agent has finished a story and wants pre-merge review without waiting for an operator. Triggers - "review my changes", "code review this PR", "review before completion", "self-review".
license: MIT
metadata:
  version: "1.0.0"
  domain: workflow
  triggers: code review, self-review, pre-merge review, cross-model review
  role: workflow
  scope: review
  output-format: markdown PR comment
  part-of: bmad-team
---

# bmad-team-review

> Part of [BMAD-Team](../bmad-team/charter.md). Runs after `bmad-team-claim` + BMAD `dev-story`, before posting `<!-- ORCHESTRATE:COMPLETION -->` (see [protocol.md](../bmad-team/protocol.md)).

A dev agent invokes this skill once it has finished implementing a story and pushed code. The skill runs a code review using **a different model** than the one that wrote the code, decides whether the residual issues warrant a fixup loop, and either:

- pushes a fixup and re-runs (max 2 rounds), or
- posts COMPLETION (with reviewer trace), or
- escalates via ORCHESTRATE:DECISION when residual P1+ issues exceed budget.

## Core invariant

**The reviewer model must be different from the writing model.** Same-model self-review is suspect — the writer rationalizes its own choices. Cross-model review is the trick that makes self-driven review trustworthy.

## When to invoke

- After agent has finished implementing a story
- After agent has run local tests / lints and they pass
- Before posting `<!-- ORCHESTRATE:COMPLETION ... -->` on the PR
- Before changing the PR from draft to ready

Do **not** invoke if:
- < 50 LOC change (skip review entirely; not worth the cost)
- The user explicitly asks for a single-shot output without review
- You are a one-off agent not running under orchestrate / multi-agent flow

## Reviewer selection — by what the PR touches

Run `lib/select-reviewer.py <self_model>` from the repo root. It reads `git diff --name-only <base>..HEAD` and emits a JSON object with the chosen reviewer, fallbacks, and reasoning.

Path-based mapping (`reviewer-mapping.yaml`):

| Path pattern | Primary reviewer | Why |
|---|---|---|
| `cmd/egress-gateway/**`, `cmd/host-agent/**`, `internal/hostagent/**`, system / data-plane / syscall code | codex | Linux internals, syscall correctness, fd / socket lifecycle |
| `internal/workflow/**`, `internal/api/service/**`, saga / state machine | claude | Multi-state reasoning, cross-module consistency |
| `migrations/**`, `internal/db/query/**`, schema / sqlc | codex + claude (dual) | Codex for column / FK / PK precision; Claude for ADR / contract alignment |
| `internal/api/handler/**`, `internal/api/response/**`, API boundary | claude | Boundary lints, contract alignment |
| `tools/lint/**`, lint code itself | codex | Tight code, type-level precision |
| `_bmad-output/**`, ADR / spec / `*.md` | claude | Long-form prose, argumentative reasoning |
| `deploy/ansible/**`, `.github/workflows/**`, `Dockerfile`, ops scripting | deepseek | Idiomatic ops code |
| Pure refactor / dedup / cleanup PR | deepseek | Pattern recognition |
| `*_test.go`, test code | codex | Test logic = tight code |
| Cross-epic / impacts ≥ 5 dirs | claude + codex (dual, claude first) | Architecture pass + correctness pass |

### Self-model exclusion

After path-based selection, drop the writer's own model and pick the next-best from the fallback chain:

```
Codex fallback chain: codex > claude > deepseek
Claude fallback chain: claude > codex > deepseek
DeepSeek fallback chain: deepseek > codex > claude
```

In a Claude-written PR routed to "claude" by path, the writer is excluded → reviewer becomes "codex" (next in claude's fallback).

### Size override

- < 50 LOC → skip review
- 50-500 LOC → single reviewer
- \> 500 LOC AND ≥ 3 path categories touched → dual review (run two reviewers, dedupe overlapping findings)

## Reviewer commands

The skill invokes one of these per the chosen reviewer. Override via `.claude/skills/bmad-team-review/customize.sh` if your CLI flags differ.

| Reviewer | Command | Notes |
|---|---|---|
| codex | `codex review --base <base> --title "<title>" 2>&1` | Use the existing `codex` CLI. Tested against real diffs in this repo. |
| claude | `claude --model claude-opus-4-7 -p "$(cat lib/prompts/review-claude.md)" --files-changed-against <base>` | Anthropic SDK CLI. Falls back to `claude --print` if `--files-changed-against` not supported in installed version. |
| deepseek | `deepseek-coder review --base <base>` (placeholder — fill in user's actual CLI in `customize.sh`) | DeepSeek CLI install varies. v1 leaves this as a hook. |

If a reviewer command is not available on the host, fall back per the chain above and log the substitution in the review's metadata footer.

## Run flow

1. **Pre-flight**: `git diff --stat <base>..HEAD` — if total < 50 LOC, skip and post COMPLETION with note "skipped review (small change)".
2. **Selection**: `lib/select-reviewer.py <self_model>` → JSON with `reviewer`, `dual` (bool), `stopping_rules`, `reasoning`. Read `stopping_rules.max_rounds` from this JSON; that is the live cap (call it `MAX_ROUNDS` below). Do **not** hardcode a number — the TOML is the source of truth.
3. **Run**: invoke the chosen reviewer's command, capture output to `/tmp/cross-review-<pr>-<round>.md`.
4. **Parse findings**: extract P0 / P1 / P2 / P3 lines (regex on `[P\d]`).
5. **Decide**:
    - 0 P0+P1+P2 → proceed
    - 1+ P0 OR fundamentally architectural OR review claims spec violation → DECISION (escalate, no fixup)
    - 1+ P1 OR P2 AND `round < MAX_ROUNDS` → push fixup addressing each, re-run from step 3 with `round + 1`
    - `round == MAX_ROUNDS AND P1+P2 still present` → DECISION (escalate, "max rounds reached")
    - Reviewer flagged the same finding twice without agent action → DECISION (don't keep arguing — operator decides)
    - All findings are P3 / nit only → proceed
6. **CI verification** (before posting COMPLETION): `gh pr checks <pr> --watch --interval 30 --fail-level high --log-dir /tmp/ci-logs-<pr>`. This waits for all remote CI checks to finish, not just reads the last cached result. If CI fails → fix the issue, push again, and go to step 3. If CI passes → proceed.
7. **Post**: write review to PR via `gh pr comment <pr> --body "$(cat /tmp/cross-review-<pr>-<round>.md)"`. Include footer with `reviewer model`, `round`, `LOC reviewed`, `findings: P0=N P1=N P2=N P3=N`. Only post COMPLETION after step 6 confirms CI is green.
8. **Loop**: if step 5 said fixup, push commits, then go to step 3.

## Stopping rules — "适可而止"

These rules exist because reviewers can always find more nits if you let them:

- `MAX_ROUNDS` comes from `stopping_rules.max_rounds` in `lib/reviewer-mapping.toml` (surfaced in the `select-reviewer.py` JSON). After `MAX_ROUNDS` rounds with residual P1+, stop and escalate. Do not keep iterating. To change the cap, edit the TOML — do not hardcode a number in this doc or in agent reasoning.
- **P3 / style-only findings do not trigger fixup.** They land as a footer note in the COMPLETION comment for the operator's awareness. Examples of P3: variable naming, comment phrasing, "could be clearer", lint preferences not in repo lints.
- **Repeated same finding without progress → escalate.** If the reviewer flags the same `<file:line> + <issue>` two rounds in a row and the agent's fixup attempt didn't address it, that is a sign of disagreement, not a sign that the agent should try harder. Operator decides.
- **Architectural / spec-level findings always escalate, never auto-fix.** A P2 that says "this violates ADR-X" is not a fixup item; it is a scope decision. Post DECISION.

## Output format — review PR comment

```markdown
## Cross-Model Review (round <N>)

**Reviewer**: <model> (writer was <self_model>; selected by <path-rule>)
**Diff**: <LOC> LOC across <N> files vs `<base>`
**Findings**: P0=N P1=N P2=N P3=N

### Findings

[review body verbatim from reviewer]

---

**Action taken**: <"posted COMPLETION" | "pushing fixup, re-review next round" | "escalated via DECISION (reason)">
**Round**: <N> / 2
```

## Escalation format — DECISION

When residual P1+ exceeds budget or finding is architectural, post:

```markdown
<!-- ORCHESTRATE:DECISION agent=<id> story=<slug> timestamp=<ISO> -->

**Question**: Cross-model review (<rounds> rounds, <reviewer>) flags <N> P1/P2 findings unresolved. <one-line summary of the hardest finding>.

**Options**: accept_residual_findings, redesign_scope, escalate_to_arch_review

**Context**: <list each unresolved finding with file:line + 1-line summary>
```

Operator picks an option in the RESPONSE.

## Configuration

- Per-host overrides: `.claude/skills/bmad-team-review/customize.sh` (reviewer commands, fallback chains)
- Per-project overrides: `.claude/skills/bmad-team-review/customize.toml` in the project root, if present
- Mapping: `lib/reviewer-mapping.yaml` (edit to add new path categories)

## What this skill does NOT do

- It does not replace the human operator final merge gate. Even after self-review converges, a human still merges.
- It does not auto-trigger codex via the orchestrate `review-triggered` label. That label remains semantically "agent self-review converged, please human-review for merge".
- It does not handle review of the review (no recursion).

## Roadmap

- v1 (this version): path-based reviewer pick, codex working, claude / deepseek as command hooks, 2-round cap, P3 ignore.
- v2: capability detection (skip a reviewer that's not installed), per-project mapping override, per-finding-category routing (route an arch finding to claude even if codex is the primary).
- v3: cost cap (per-PR / per-day), reviewer-history learning (track which reviewer flags what kinds of issues, weight findings).
