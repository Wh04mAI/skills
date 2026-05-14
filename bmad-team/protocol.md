# BMAD-Team Protocol — `ORCHESTRATE:*` Markers

**Status**: v0 (internal). Candidate for external publication as `pr-claim-protocol`. Last revised: 2026-05-14.

## Why a protocol

Multi-agent coordination needs a **shared, durable, daemon-less,
cross-tool message bus**. Most alternatives we surveyed (April 2026
research) require one of:

- A long-running daemon (claude-flow's runtime).
- A vendor-locked panel (GitHub Agent HQ).
- A shared local file (`sprint-status.yaml`) that branch isolation hides.
- A bespoke task DB (Delega).

BMAD-Team's bet: **GitHub itself is the daemon.** Draft PRs and PR
comments already are durable, multi-process, cross-tool, network-accessible
state. We just need a stable comment syntax.

The protocol is HTML comments in PR bodies / comments. They render
invisibly on GitHub's UI but are trivially `gh pr list --search`able
and `gh pr view --json comments`able.

## Marker syntax

All markers follow:

```
<!-- ORCHESTRATE:<KIND> key1=value1 key2=value2 ... -->
```

Constraints:

1. **Single-line.** Wrapping breaks the search index.
2. **Keys are lowercase alphanumeric + underscore.** Values are URL-safe (no spaces, `,`, `=` in raw form).
3. **Prefix is literally `ORCHESTRATE`.** Do not rename without a protocol version bump.
4. **First marker on a PR body line.** Trailing prose is fine.

There is no JSON, no YAML, no escaping rules. Anything that wants
structure goes in the prose *after* the marker, not inside it.

## The five marker kinds

### `CLAIM`

```html
<!-- ORCHESTRATE:CLAIM agent=<agent-id> story=<N.M> -->
```

Or for an issue:

```html
<!-- ORCHESTRATE:CLAIM agent=<agent-id> issue=<N> -->
```

| Field | Meaning |
|---|---|
| `agent` | Agent ID, three-segment (see below) |
| `story` | Story key in `N.M` format |
| `issue` | GitHub issue number (use instead of `story` for direct issue fixes) |

**Lifecycle**: Posted in the draft PR body when the agent opens the PR.
Persists for the life of the PR. Closing the PR retires the claim.

**Discovery**: `gh pr list --search "ORCHESTRATE:CLAIM" --state open`
returns all live claims org-wide. Agents pre-flight by searching for
the specific `story=N.M` or `issue=N` before doing any work.

### `DECISION`

```html
<!-- ORCHESTRATE:DECISION agent=<agent-id> story=<N.M> timestamp=<ISO8601> -->
```

| Field | Meaning |
|---|---|
| `agent` | Asker |
| `story` | Scope |
| `timestamp` | ISO 8601 UTC, `2026-05-14T09:11:03Z` |

**Lifecycle**: Posted as a new PR **comment** when the agent hits an
ambiguity it cannot resolve from spec / prior context. The marker is on its
own line; below it is the question in this format:

```
**Question**: <one sentence>
**Options**: <comma-separated machine-readable options>
**Context**: <bulleted facts the responder needs>
```

The orchestrator (or human) is expected to reply with a `RESPONSE`
within `decision_response_sla` (default: 30 minutes). Past SLA, the
agent may choose a safe default or block.

### `RESPONSE`

```html
<!-- ORCHESTRATE:RESPONSE agent=<orchestrator-id> decision=<option> in_reply_to=<comment-id> -->
```

| Field | Meaning |
|---|---|
| `agent` | The orchestrator that decided (often `orchestrator`, but may be a human's GitHub login if hand-decided) |
| `decision` | One of the options from the `DECISION`'s `Options` field |
| `in_reply_to` | The comment-id of the `DECISION` (so multiple open decisions can be disambiguated) |

**Lifecycle**: Posted as a new PR comment by the orchestrator. The
dev agent polls for it (its monitor reads `gh pr view --json
comments` periodically) and resumes work.

### `COMPLETION`

```html
<!-- ORCHESTRATE:COMPLETION agent=<agent-id> story=<N.M> reviewer=<model> rounds=<N> ci=<sha> -->
```

| Field | Meaning |
|---|---|
| `agent` | The writer |
| `story` | Scope |
| `reviewer` | The cross-model reviewer (e.g. `codex`) — `none` if review was skipped (< 50 LOC) |
| `rounds` | Number of review rounds run |
| `ci` | The commit SHA that has green CI |

**Lifecycle**: Posted when the agent has finished implementing, run
cross-model review to convergence, and verified CI is green. This
flips the dev agent from "working" to "waiting for human merge".
The orchestrator's monitor looks for `COMPLETION` without
`APPROVAL` and treats those as the "ready to merge" queue.

### `APPROVAL`

```html
<!-- ORCHESTRATE:APPROVAL agent=<orchestrator-id> pr=#<N> -->
```

**Lifecycle**: Posted by the orchestrator after sanity-checking the
PR (CI markers match, no unresolved DECISION, scope matches the
claim). Humans still do the actual `git merge` / squash-merge —
`APPROVAL` only unblocks the dev agent's polling loop and signals
to a watching human that "everything that can be automated has been
automated; you may merge."

## Agent ID format

Three segments, lowercase, hyphen-separated:

```
<github-login>-<tool>-<seq>
```

| Segment | Constraint | Example |
|---|---|---|
| `<github-login>` | Operator's GitHub login. Resolve via `gh api user --jq .login`. Sanitize to `[a-z0-9]`, max 12 chars | `manyu`, `haojianggod` |
| `<tool>` | One of: `claude`, `codex`, `cursor`, `human` (for hand work without AI) | `claude` |
| `<seq>` | Local concurrency slot. Lowest free slot for the `<login>+<tool>` pair, scanned from open draft PRs | `1`, `2` |

Examples: `manyu-claude-1`, `manyu-codex-2`, `haojianggod-claude-1`.

**Invariant**: an agent ID is unique by construction. Two devs never
share a `<github-login>`, so cross-machine collision is impossible.

## Claim invariants

1. **The PR is the claim.** Until your draft PR exists on `origin`, no
   other agent can see you hold the work. *Push the draft PR before
   you start reading or editing code.*
2. **One agent, one worktree.** Never two agents in the same
   workdir — worktrees prevent contention on `git index.lock`, IDE
   state, and WIP.
3. **`<base>` defaults to the integration branch.** Usually `dev` /
   `develop` / `main`. Project's `_bmad/team/config.yaml` declares
   which.

## Status derivation

PR state, not a file column, drives story status:

| Story status | Signal |
|---|---|
| `claimed` | draft PR exists; only the empty WIP commit |
| `in-progress` | draft PR has commits beyond the WIP |
| `review` | PR moved from draft to ready; `COMPLETION` posted |
| `approved` | `APPROVAL` marker exists; awaiting human merge |
| `done` | PR squash-merged to base |
| `deployed-dev` / `deployed-staging` | `bmad-team-deploy` posted a deploy marker (v0.1+) |

If a project also keeps a BMAD `sprint-status.yaml`, the agent flips
its row inside the PR before marking ready; the change lands on base
via squash-merge.

## Anti-patterns

- ❌ Editing source files **before** the draft PR is pushed. The
  pre-flight search cannot see you.
- ❌ Multi-line markers, JSON in markers, escaping. Keep markers boring.
- ❌ Using `<agent-id>` you invented out of thin air (`agent-3`,
  `bot`). Always derive from `gh api user`.
- ❌ Force-pushing another agent's branch.
- ❌ Leaving a claim open after pivoting. Close the PR; reopen later if
  you come back.

## Reserved marker kinds (do not invent)

These names are reserved for future protocol versions and must not
be used for project-specific extensions:

- `CLAIM`, `DECISION`, `RESPONSE`, `COMPLETION`, `APPROVAL` (current)
- `BLOCKED`, `REASSIGN`, `DEPLOY`, `ROLLBACK` (planned for v0.1)

If you need a project-specific marker, use a different prefix
(e.g. `INVIST:`, `PICKS:`), not `ORCHESTRATE:`.

## Protocol versioning

This is v0. The version is encoded only in this document — there is
no version field in markers themselves yet. v1 will add `proto=1` to
each marker if breaking changes accumulate; until then, the marker
set is frozen and additive.

## Why HTML comments, specifically

- Render invisibly on GitHub UI (no noise to human reviewers).
- Survive PR description edits (unlike GitHub Reactions or Labels).
- Searchable via `gh pr list --search`.
- Plain text — works in any tool that can post a PR comment.
- No vendor coupling beyond GitHub itself.

GitLab and Bitbucket support the same HTML comments in PR/MR bodies;
the protocol ports trivially. Only `gh` CLI usage in skills is
GitHub-specific.

## External publication plan

This document, slightly genericized (remove BMAD specifics, keep
just the protocol), becomes `pr-claim-protocol`. Publication target:

- GitHub repo: `Invist-AI/pr-claim-protocol` (or similar)
- GitHub Discussions post on `bmad-code-org/BMAD-METHOD` to seed adoption
- Possibly a short writeup on the Anthropic engineering blog or our own

Timeline: after v0.1 stabilizes internally (~one quarter from now).
