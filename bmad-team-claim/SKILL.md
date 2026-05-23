---
name: bmad-team-claim
description: Claim a story or issue safely when multiple AI agents work on the same repo in parallel. Combines git worktree isolation, a three-segment agent ID, and a draft-PR-as-claim marker so that two agents never silently pick up the same work. Use when starting a new piece of work in a repo that has more than one agent (or human + agent) collaborating, or when the user says "claim story X", "start work on issue N", "spin up a worktree", or "set up a parallel branch".
license: MIT
metadata:
  version: "1.0.0"
  domain: workflow
  triggers: claim story, claim issue, start work, parallel agents, worktree, draft pr, orchestrate
  role: workflow
  scope: setup
---

# bmad-team-claim

When several AI agents (and humans) share one repo, you need a **single source of truth for who is doing what** that survives across branches and machines. File-based claim lists (epics.md `status: in-progress`) fail because branch isolation hides them from other agents. This skill makes the claim itself a **published artifact on `origin`**: a draft PR with a machine-readable HTML marker. Agents discover claims via `gh pr list --search`; the act of pushing the draft PR is the claim.

## Core invariants

1. **PR is the claim.** Until your draft PR exists on `origin`, no other agent can see you hold the work. Push it *before* you start reading or editing code.
2. **One agent, one worktree.** Never two agents in the same workdir. Worktrees mean concurrent agents don't fight over `git index.lock`, IDE state, or each other's WIP.
3. **Agent ID is three segments.** `<github-login>-<tool>-<seq>`. Each segment is orthogonal, so collisions are impossible by construction.
4. **`<base>` defaults to the repo's integration branch (`dev` / `main` / `develop`).** Confirm before running commands.

## Agent ID format

```
<github-login>-<tool>-<seq>
```

- `<github-login>` — the human operator's GitHub login, sanitized to lowercase alphanumeric, ≤ 12 chars. Resolve via `gh api user --jq .login`. Two devs never share a login, so this prevents cross-machine collision.
- `<tool>` — the assistant family: `claude`, `codex`, `cursor`, or `human` for hand-rolled work without an AI.
- `<seq>` — local concurrency slot per `<login>+<tool>` pair: `1`, `2`, `3`. Pick the lowest free seq by scanning open draft PRs with the same `<login>+<tool>` prefix.

Examples: `manyu-claude-1`, `manyu-codex-2`, `haojianggod-claude-1`.

## Pre-flight (cheap, run from anywhere in the repo)

Two separate questions, two separate checks. **Don't conflate them.**

```bash
# Q1: is the issue claimable at all? (open, not yet assigned to someone else)
gh issue view <N> --json state,assignees --jq '{state, assignees: [.assignees[].login]}'

# Q2: has another agent already opened a claim PR for it? Search the CLAIM marker
# in PR bodies (canonical signal), not the title — the new title format is
# `<verb>: <slug> (#<N>)`, which a naive `issue-<N>` search would miss.
gh pr list --search 'ORCHESTRATE:CLAIM issue=<N>' --state all --json title,state,author,headRefName
git branch -r | grep "issue-<N>"

# Q2 (story flow — only if you still run BMAD sprint-status files):
gh pr list --search "story-<N.M> in:title" --state all
git branch -r | grep "story-<N.M>"
```

Skip rules:

- **Q1 returns `state=CLOSED`** → already done; pick something else.
- **Q1 returns `assignees != []` and you aren't one of them** → human says somebody else owns this; coordinate before claiming.
- **Q2 returns an open draft PR with `ORCHESTRATE:CLAIM ... issue=<N>`** → another agent holds the claim. Don't race.
- Q1 succeeding by itself (issue exists, open, unassigned) is **not** a hit — it's the green light.

## Claim sequence — GitHub issue (canonical)

```bash
# 1. Create worktree adjacent to the main checkout
git worktree add ../<repo>-<agent-id>-<N> -b <agent-id>/issue-<N>-<slug>
cd ../<repo>-<agent-id>-<N>

# 2. Empty WIP commit so there is something to push
git commit --allow-empty -m "wip(#<N>): claim"

# 3. Publish branch to origin
git push -u origin <agent-id>/issue-<N>-<slug>

# 4. Open draft PR with machine-readable CLAIM marker in the body
gh pr create --draft \
  --base <base-branch> \
  --title "<verb>: <slug> (#<N>)" \
  --body 'Closes #<N>.

<!-- ORCHESTRATE:CLAIM agent=<agent-id> issue=<N> -->
Claimed by <agent-id>.'
```

`<verb>` matches the work — `feat` / `fix` / `chore` / `docs` / `refactor` / `test` / `build` / `ci` / `perf` / `style`. The `(#<N>)` suffix in the title and the `Closes #<N>` in the body together let `bmad-team-merge`'s pre-flight pass and let GitHub auto-close the issue on squash-merge to the default branch.

The `<!-- ORCHESTRATE:CLAIM ... -->` HTML comment is the discovery primitive: `gh pr list --search "ORCHESTRATE:CLAIM"` returns all live claims across the org.

## Alternative: story flow (BMAD sprint-status files)

If the project still tracks work in a `sprint-status.yaml` rather than as GitHub issues, swap these four fields and run the same 4-step sequence:

| Field | Issue form (canonical) | Story form |
|---|---|---|
| worktree dir | `../<repo>-<agent-id>-<N>` | `../<repo>-<agent-id>-<N.M>` |
| branch | `<agent-id>/issue-<N>-<slug>` | `<agent-id>/story-<N.M>-<slug>` |
| title | `<verb>: <slug> (#<N>)` | `feat(story-<N.M>): <slug>` |
| body | `Closes #<N>.` + CLAIM marker | `Claimed by <agent-id>.` + CLAIM marker (no auto-close on merge — `bmad-team-merge` must close the story manually) |
| marker | `ORCHESTRATE:CLAIM agent=<id> issue=<N>` | `ORCHESTRATE:CLAIM agent=<id> story=<N.M>` |

Story-flow PRs will **not** pass `bmad-team-merge`'s pre-flight checklist as-is (title and body checks expect `(#<N>)` / `Closes #N`). If you stay on story flow, either upgrade `bmad-team-merge` locally to accept story titles or merge story PRs manually.

## After claiming: execution briefing

The skill is not finished when the draft PR is pushed. Emit a structured briefing to the operator so anyone (operator, future-you in a later session, an orchestrator) can pick up cold without re-deriving state. This is the handoff artifact between `bmad-team-claim` and the dev loop that follows.

Required fields (one line each, exact labels — orchestrators may parse):

```
**Claimed**: <issue #N | story N.M> — <slug>
**Agent ID**: <agent-id>
**Worktree**: <absolute path>
**Branch**: <agent-id>/<branch>
**Draft PR**: <URL>
**Base**: <integration-branch>
```

Then a short free-form section for context the operator might want before approving the dev loop:

```
**Repro / verification hint** (from the issue body, if any):
<command or 1-line description>

**Scope risk** (only if non-obvious):
- e.g. "touches libvirt CGo path — small diff but high blast radius; cross-model review is non-optional even under the < 50 LOC heuristic"
- e.g. "may force append on `vm_signals.go` registry — coordinate with [[orchestrate]] if other VMW-lane PRs in flight"

**Next step**: awaiting HUMAN CONFIRM before invoking [[bmad-team-dev]]. The execute brief is the operator's last cheap chance to catch a misread scope (wrong issue, wrong agent ID, wrong base, wrong path). Do not start implementing on your own.
```

If the issue body has nothing relevant for repro / scope risk, omit those sub-sections. Don't pad.

## Releasing a claim

If you abandon the work — out-of-scope blocker, lost context, priority switch:

```bash
gh pr close <pr> --comment "Releasing claim: <one-line reason>."
git push origin --delete <agent-id>/<branch>
git worktree remove <worktree-path>   # from the brief's **Worktree:** line — must precede branch -D
git branch -D <agent-id>/<branch>     # local branch — easy to forget
```

The next agent's pre-flight then comes up clean. **Zombie draft PRs are noise** — close them. (Successful merge cleanup is [[bmad-team-merge]]'s job, not this skill's.)

## In-flight cap

Hold at most **~2 in-flight PRs per agent**. Beyond that, context drift makes CR responses unreliable. The orchestrator should refuse to assign a third claim until one closes.

## Status derivation

PR state, not a file column, drives status:

| Status | Signal |
|---|---|
| claimed | draft PR exists, no commits beyond the empty WIP |
| in-progress | draft PR has real commits |
| review | PR is ready (not draft) |
| done | PR merged (squash-merge to base) |

If the project keeps an `epics.md` with a `status:` field, the implementing agent flips it inside their PR; it lands on base via the squash-merge.

## Append-only merge points

When multiple agents work in parallel, designate **append-only files** as designated merge points (e.g. a signal registry, a route table, a migration number list). Rebase resolution there is mechanical (always pick both, both sides are adding lines). Avoid editing such files except to append your registration line.

## Anti-patterns

- ❌ Reading or editing source files **before** pushing the draft PR. Another agent's pre-flight cannot see you yet.
- ❌ Doing `git checkout -b ...` in the main checkout instead of `git worktree add`. You will fight over `index.lock` and lose WIP.
- ❌ Using `<agent-id>` you invented out of thin air ("agent-3", "bot"). Always derive from `gh api user` so cross-machine merges work.
- ❌ Force-pushing someone else's branch. Only your own active worktree branch is yours to rewrite.
- ❌ Leaving a claim open after pivoting. Close it; reopen later if you come back.

## When NOT to use this skill

- Solo repos with one human + one agent and no concurrent work. The ceremony is overhead.
- Doc-only edits the maintainer is comfortable pushing directly to `dev` / `main` without a PR.
- Hotfixes where the operator has explicitly said "skip the claim ritual, push to dev".

## Related skills

- [[bmad-team-dev]] — runs immediately after this skill: implement → test → iterate → wrap-up briefing.
- [[bmad-team-review]] — runs at the end of the dev loop, before flipping draft → ready.
- [[bmad-team-merge]] — runs after review converges; squash-merges and cleans up worktree + branches + issue.
- [[multi-agent-rules]] — bootstraps the AGENTS.md tiered rule system that makes the claim mechanism worth following.
- [[bmad-team-orchestrate]] — multi-agent orchestrator that consumes the `ORCHESTRATE:CLAIM` / `ORCHESTRATE:COMPLETION` / `ORCHESTRATE:DECISION` markers this skill emits.

The full chain: `bmad-team-claim` → `bmad-team-dev` → `bmad-team-review` → `bmad-team-merge`.
