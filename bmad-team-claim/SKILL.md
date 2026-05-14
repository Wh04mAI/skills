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
  part-of: bmad-team
---

# bmad-team-claim

> Part of [BMAD-Team](../bmad-team/charter.md). Marker syntax is defined in [protocol.md](../bmad-team/protocol.md). This skill is the "claim" verb of that protocol.

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

Before doing anything else, check that the work isn't already claimed:

```bash
# Story-shaped work
gh pr list --search "story-<N.M>" --state all
git branch -r | grep "story-<N.M>"

# Issue-shaped work
gh pr list --search "issue-<N>" --state all
gh issue view <N> --json closedAt,assignees
```

If either hits, **pick something else**. Do not race for the claim.

## Claim sequence — story

```bash
# 1. Create worktree adjacent to the main checkout
git worktree add ../<repo>-<agent-id>-<N.M> -b <agent-id>/story-<N.M>-<slug>
cd ../<repo>-<agent-id>-<N.M>

# 2. Empty WIP commit so there is something to push
git commit --allow-empty -m "wip(story-<N.M>): claim"

# 3. Publish branch to origin
git push -u origin <agent-id>/story-<N.M>-<slug>

# 4. Open draft PR with machine-readable CLAIM marker in the body
gh pr create --draft \
  --base <base-branch> \
  --title "feat(story-<N.M>): <slug>" \
  --body '<!-- ORCHESTRATE:CLAIM agent=<agent-id> story=<N.M> -->
Claimed by <agent-id>.'
```

The `<!-- ORCHESTRATE:CLAIM ... -->` HTML comment is the discovery primitive: `gh pr list --search "ORCHESTRATE:CLAIM"` returns all live claims across the org.

## Claim sequence — GitHub issue (direct fix)

Same discipline, four substitutions:

| Field | Story form | Issue form |
|---|---|---|
| branch | `<agent-id>/story-<N.M>-<slug>` | `<agent-id>/issue-<N>-<slug>` |
| title | `feat(story-<N.M>): <slug>` | `fix\|feat\|chore\|docs(#<N>): <slug>` (verb matches the work) |
| body | `Claimed by <agent-id>.` | must include `Closes #<N>` so squash-merge auto-closes the issue |
| marker | `ORCHESTRATE:CLAIM agent=<id> story=<N.M>` | `ORCHESTRATE:CLAIM agent=<id> issue=<N>` |

## Releasing a claim

If you abandon the work — out-of-scope blocker, lost context, priority switch:

```bash
gh pr close <pr> --comment "Releasing claim: <one-line reason>."
git push origin --delete <agent-id>/<branch>
git worktree remove ../<repo>-<agent-id>-<N.M>
```

The next agent's pre-flight then comes up clean. **Zombie draft PRs are noise** — close them.

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

- [[bmad-team-review]] — runs after the work is done, before flipping draft → ready.
- [[multi-agent-rules]] — bootstraps the AGENTS.md tiered rule system that makes the claim mechanism worth following.
- [[bmad-team-orchestrate]] — multi-agent orchestrator that consumes the `ORCHESTRATE:CLAIM` / `ORCHESTRATE:COMPLETION` / `ORCHESTRATE:DECISION` markers this skill emits.
