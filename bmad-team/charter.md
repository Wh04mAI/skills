# BMAD-Team — Charter

**Status**: v0.1 (internal). Last revised: 2026-08-02.

## What it is

BMAD-Team is a set of skills that extends the [BMAD](https://github.com/bmad-code-org/BMAD-METHOD)
methodology to **multi-agent, multi-environment software delivery**.

BMAD provides a single-threaded agentic SDLC: PRD → Architecture →
Epics → Stories → `dev-story` → `code-review` → done, with state in
`sprint-status.yaml`. It works beautifully for one agent at a time.

BMAD-Team kicks in when:

- More than one AI agent (or AI + human) is editing the same repo concurrently.
- You want pre-merge review by a model **different** from the one that wrote the code.
- A story is not "done" when the PR merges — it is done when it reaches a deployed environment.

## Direction (2026-05-23): GitHub Issues are the canon work registry

BMAD-Team initially leaned on BMAD's `sprint-status.yaml` as its source of truth
for what work exists and who's doing it. We're moving off that. File-based sprint
tracking is rigid and drifts (one branch's sprint file diverges from another's),
and the team is small enough that GitHub Issues + draft-PR-as-claim covers it
without a sidecar registry.

Concretely:

- **`bmad-team-claim`** (canon): claim a GitHub issue. PR title `<verb>: <slug> (#<N>)`,
  body `Closes #<N>`, branch `<agent-id>/issue-<N>-<slug>`. This is what
  `bmad-team-merge` expects.
- **`bmad-team-claim`** (alternative, documented): story-flow for projects that
  still run sprint-status files.
- **`bmad-team-orchestrate`**: still reads sprint-status today. v0.2 plan is to
  add a `github_issues` work-source adapter (the abstraction already exists in
  `lib/adapters/`) and make it the default. The sprint adapter stays as an
  opt-in. Not done in 2026-05-23 — tracked as follow-up.
- This charter's "three gaps" table below still describes BMAD-Team in terms of
  the sprint-status framing because that's where it started. The narrative will
  be rewritten when the orchestrate adapter lands; the **plumbing** decision
  (issues > sprint files) is final as of this revision.

## Direction (2026-08-02): official-first — the inner loop belongs to bmad-loop

BMAD upstream now ships [bmad-loop](https://github.com/bmad-code-org/bmad-loop)
(v0.9.0, official external module): an unattended dev → verify → review → commit
orchestrator with a deferred-work ledger, escalation/resolve flow, and —
critically — **per-stage model adapters** (`[adapter.dev]` / `[adapter.review]`
in policy.toml), which absorb BMAD-Team's writer ≠ reviewer invariant at the
orchestrator level.

What bmad-loop does **not** do (checked against v0.9.0): parallel fan-out
(`max_parallel` is validated and clamped to 1), GitHub Issues/PRs as work
registry, any cross-machine or cross-tool coordination, deploy past `done`.
That is exactly the territory BMAD-Team keeps.

Consequences, applied in this revision:

- **BMAD-Team = the GitHub coordination layer.** Claim, marker protocol,
  merge, and marker monitoring are the assets. The inner implement loop is
  official territory — we do not compete with bmad-loop on it.
- **`bmad-team-dev` is re-scoped** to the PR-flow shell: orient/plan, the two
  operator briefings, and the PR/marker glue. When the project runs bmad-loop,
  the inner iterate loop is delegated to it; the manual phases remain as the
  fallback for projects without bmad-loop.
- **`bmad-team-review` is repositioned**: the writer ≠ reviewer invariant is
  now achievable in official config; what stays ours is path-based reviewer
  routing, self-model exclusion chains, stop rules, and standalone PR-flow
  review outside a loop run. Projects can override the mapping via
  `_bmad/team/reviewer-mapping.toml` without touching the skill.
- **`bmad-team-orchestrate`'s dispatch role is legacy.** Serial story driving
  is bmad-loop's job today, and parallel fan-out is on its official roadmap.
  We keep the marker monitor (`--monitor`: DECISION auto-response, APPROVAL)
  — the part with no official counterpart. The sprint-status dispatch path is
  frozen: no new features.
- **`bmad-team-deploy` stays out of BMAD scope** (unchanged decision) — it is
  generic CD glue, maintained for our projects, not part of the external story.

## The three gaps it fills

| BMAD core | Gap | BMAD-Team addition |
|---|---|---|
| `sprint-status.yaml` is the source of truth for "who is doing what" | The file is branch-local; another agent on another worktree cannot see it | **`bmad-team-claim`** publishes the claim as a draft PR on `origin` with a machine-readable HTML marker. Any agent on any machine can `gh pr list --search` to discover claims. |
| Stories are picked up serially by one agent | No dispatcher, no asynchronous decision protocol when an agent gets stuck | **`bmad-team-orchestrate`** runs as a monitor that polls PR comments, applies `decision-rules.yaml`, and posts auto-responses or escalates to a human. |
| `bmad-code-review` is same-model adversarial review | A model rationalizes its own choices; cross-model ensembles catch ~17–30% more bugs (arxiv 2509.12629, 2510.21513) | **`bmad-team-review`** picks a reviewer model **different** from the writer, routed by what file paths the PR touches, with hard stop rules to prevent endless review loops. *(2026-08: official bmad-loop's `[adapter.review]` now covers writer ≠ reviewer inside loop runs — what remains ours is path-routing, fallback chains, stop rules, and standalone PR-flow review.)* |
| BMAD stops at `done` | No representation of "shipped to dev" or "shipped to staging" | **`bmad-team-deploy`** wires the story to a kustomize-based k3s deploy via SSH + jump host. Story status can therefore evolve past `done` to `deployed-dev` / `deployed-staging` / `released`. |

## The novel piece — `ORCHESTRATE:*` markers

The single most important abstraction in BMAD-Team is the
**marker protocol** in `protocol.md`.

Instead of inventing a new state file, BMAD-Team uses **HTML comments
in GitHub PR bodies and comments** as a durable, daemon-less,
cross-tool message bus:

```
<!-- ORCHESTRATE:CLAIM agent=manyu-claude-1 story=6.2 -->
<!-- ORCHESTRATE:DECISION agent=manyu-claude-1 story=6.2 timestamp=2026-05-14T09:11:03Z -->
<!-- ORCHESTRATE:RESPONSE agent=orchestrator decision=accept_residual_findings in_reply_to=2113456789 -->
<!-- ORCHESTRATE:COMPLETION agent=manyu-claude-1 story=6.2 reviewer=codex rounds=2 ci=a1b2c3d -->
<!-- ORCHESTRATE:APPROVAL agent=orchestrator pr=#42 -->
```

> Examples above are conforming. See [protocol.md](./protocol.md) for the full required-field list per marker — copy from there, not from the snippet, if you're hand-writing markers.

A Claude agent and a Codex agent can collaborate on the same repo
**without ever talking to each other directly** — they read each
other's markers off of GitHub. GitHub is the daemon.

This is the candidate we will eventually publish externally (see
"Roadmap → External publication").

## Lifecycle

```
┌────────────┐    ┌────────────┐    ┌──────────┐    ┌──────────┐    ┌────────────┐    ┌──────────┐    ┌────────────┐    ┌─────────┐
│ BMAD-PLAN  │ →  │ BMAD-SPRINT│ →  │   TEAM-  │ →  │  BMAD-DEV│ →  │  TEAM-     │ →  │  HUMAN   │ →  │  TEAM-     │ →  │  done   │
│            │    │            │    │   CLAIM  │    │          │    │  REVIEW    │    │  MERGE   │    │  DEPLOY    │    │  (env)  │
│ PRD/Arch   │    │ sprint-    │    │ draft PR │    │ dev-     │    │ cross-     │    │ APPROVAL │    │ kubectl    │    │ tagged  │
│ Epics      │    │ status.yml │    │ + CLAIM  │    │ story    │    │ model      │    │ marker   │    │ apply -k   │    │ dev /   │
│            │    │            │    │ marker   │    │          │    │ + CI green │    │          │    │ overlays   │    │ staging │
└────────────┘    └────────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └────────────┘    └─────────┘
   (BMAD)            (BMAD)         (BMAD-Team)      (BMAD)         (BMAD-Team)       (Human)        (BMAD-Team)       (Human)
```

Parallel agents run independent paths through `CLAIM → DEV → REVIEW`,
serialized only at the `HUMAN MERGE` step. The merge order is
controlled by `bmad-team-orchestrate`'s decision rules and human
oversight.

As of 2026-08-02 the `BMAD-DEV` box is preferentially an official
`bmad-loop` run inside the claim's worktree; the manual `dev-story`
flow is the fallback (see `bmad-team-dev`'s scope note).

## Skill inventory

| Skill | One-line | Notes |
|---|---|---|
| [bmad-team-claim](../bmad-team-claim/SKILL.md) | Publish a draft PR with HTML claim marker; one agent, one worktree | **Core asset** — no official counterpart |
| [bmad-team-dev](../bmad-team-dev/SKILL.md) | PR-flow shell: orient/plan, two operator briefings, PR/marker glue; inner loop delegates to official bmad-loop when present | **Re-scoped 2026-08-02**; manual phases remain as fallback |
| [bmad-team-review](../bmad-team-review/SKILL.md) | Pick a reviewer ≠ writer routed by paths; bounded rounds; P3 ignore | **Repositioned 2026-08-02** — invariant absorbed by bmad-loop `[adapter.review]`; routing + stop rules + standalone PR review stay ours |
| [bmad-team-merge](../bmad-team-merge/SKILL.md) | Squash-merge a ready PR + atomically clean up worktree, local/remote branches, issue | **Core asset** — BMAD still stops at `done`; bmad-loop merges locally only |
| [bmad-team-orchestrate](../bmad-team-orchestrate/SKILL.md) | Monitor PR markers; apply decision rules; post APPROVAL | **Monitor kept; dispatch legacy** (frozen pending bmad-loop parallel fan-out) |
| [bmad-team-deploy](../bmad-team-deploy/SKILL.md) | `apply -k` + rollout restart on jump host, per env | Project CD glue; out of external/BMAD scope |
| `bmad-team-verify` (deferred) | Run integration tests on a shared remote env | vmcp's `integrate-test` is too project-specific; needs generic redesign — see Roadmap |

## Per-project config

A project that imports BMAD-Team as `.claude/skills` submodule must
provide `_bmad/team/config.yaml` (copy from
`_bmad/team/config.yaml.example`). It is the single source for:

- Agent ID / base branch / max concurrency
- Reviewer model mapping (paths → preferred reviewer)
- Decision rule file path
- Deploy environments (host / overlay / namespace / smoke URL)
- Verify env hooks (when verify is implemented)

Skills must not hard-code project-specific paths or hosts. If you
find one doing so, file a bug.

## Roadmap

### v0 (this version, internal)
- ✅ Charter + protocol spec
- ✅ `bmad-team-claim`, `bmad-team-dev`, `bmad-team-review`, `bmad-team-merge`, `bmad-team-orchestrate`, `bmad-team-deploy` migrated
- ✅ Per-project config template

### v0.1 — official alignment (landed 2026-08-02)
- ✅ Re-scope `bmad-team-dev` to the PR-flow shell; inner loop delegates to
  official bmad-loop when the project runs it
- ✅ Reposition `bmad-team-review`; add project-level mapping override
  (`_bmad/team/reviewer-mapping.toml` beats the skill default)
- ✅ Mark `bmad-team-orchestrate`'s dispatch role legacy; keep the marker monitor
- ✅ Genericize `_bmad/team/config.yaml.example` (no real hosts/paths in a public repo)
- Adopt as submodule in Picks + vmcp; remove the per-project copies (still open)
- Define and ship `bmad-team-verify` as a generic shape (project provides
  one shell hook that returns 0/non-zero) — still open

### v0.2
- Express simple reviewer-routing cases directly as bmad-loop `policy.toml`
  so projects that live entirely inside bmad-loop don't need `bmad-team-review`
- Re-evaluate `bmad-team-orchestrate` when bmad-loop's parallel fan-out lands —
  keep only what GitHub-side coordination still needs (likely: monitor only)

### v1.0 — external publication
- Publish `bmad-team/protocol.md` as a standalone spec on GitHub
  Discussions / Pages under the name **`pr-claim-protocol`** (working title)
- ~~Submit `bmad-team-review` upstream against BMAD issues #446 and #685~~ —
  window closed 2026-08: bmad-loop's `[adapter.review]` covers the invariant.
  New target: contribute path-based routing to bmad-loop upstream if demand shows
- Drop `bmad-team-deploy` from external scope — it's generic CD glue

## Watch list

- **bmad-loop parallel fan-out** — v0.9.0 clamps `max_parallel` to 1 "until
  parallel fan-out lands". When it lands, re-evaluate what's left of
  `bmad-team-orchestrate` dispatch (likely: delete it) and whether claim/merge
  should become bmad-loop hooks.
- **GitHub Agent HQ** — if GitHub introduces a native draft-PR claim primitive, our HTML markers may become redundant. Re-evaluate quarterly.
- **CodeRabbit / Greptile / Qodo Merge** — if any of them ship multi-model routing, the differentiation of `bmad-team-review` narrows further.
- **BMAD upstream issues** #446, #685, #761 — partially resolved: upstream shipped
  bmad-loop (official module) with per-stage model adapters. The graceful merge
  was executed in the 2026-08-02 revision of this charter.

## Why not just use $TOOL?

Briefly, the alternatives and why we did not adopt them:

| Alternative | Why not |
|---|---|
| Official `bmad-loop` | Not an alternative — the foundation. We delegate the inner implement loop to it. It is serial (`max_parallel` clamped to 1), local, has no PR/issue work registry and no deploy; BMAD-Team is the GitHub coordination layer on top. |
| `bmad-autonomous-development` | Uses dependency graph + counter, no PR-claim, same-vendor model tiering (sonnet/opus), no deploy. Useful for *ordering* only; doesn't solve cross-tool coordination. |
| `ccswarm`, `claude-flow`, `parallel-worktrees` | Worktree mechanics only. No cross-tool coordination, no review, no deploy. |
| `Delega` agent-to-agent code review | Configured per-task, not per-path. No stop rules. |
| GitHub Agent HQ | Centralizes coordination in GitHub's panel; not portable across Cursor/Codex/Claude as a *protocol*. |
| CodeRabbit `path_instructions` | Routes prompts, not model identity. We need writer ≠ reviewer enforced at the model level. |

## Maintenance

- This charter and `protocol.md` are reviewed quarterly.
- Each skill has its own SKILL.md; this charter is the meta-doc, not a per-skill spec.
- Issues against this repo: file under `Wh04mAI/skills`.
