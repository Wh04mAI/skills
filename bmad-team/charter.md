# BMAD-Team — Charter

**Status**: v0 (internal). Last revised: 2026-05-14.

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

## The three gaps it fills

| BMAD core | Gap | BMAD-Team addition |
|---|---|---|
| `sprint-status.yaml` is the source of truth for "who is doing what" | The file is branch-local; another agent on another worktree cannot see it | **`bmad-team-claim`** publishes the claim as a draft PR on `origin` with a machine-readable HTML marker. Any agent on any machine can `gh pr list --search` to discover claims. |
| Stories are picked up serially by one agent | No dispatcher, no asynchronous decision protocol when an agent gets stuck | **`bmad-team-orchestrate`** runs as a monitor that polls PR comments, applies `decision-rules.yaml`, and posts auto-responses or escalates to a human. |
| `bmad-code-review` is same-model adversarial review | A model rationalizes its own choices; cross-model ensembles catch ~17–30% more bugs (arxiv 2509.12629, 2510.21513) | **`bmad-team-review`** picks a reviewer model **different** from the writer, routed by what file paths the PR touches, with hard stop rules to prevent endless review loops. |
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

## Skill inventory

| Skill | One-line | Notes |
|---|---|---|
| [bmad-team-claim](../bmad-team-claim/SKILL.md) | Publish a draft PR with HTML claim marker; one agent, one worktree | Lifted from Picks/multi-agent-claim |
| [bmad-team-orchestrate](../bmad-team-orchestrate/SKILL.md) | Dispatch agents; monitor PR markers; apply decision rules | Lifted from vmcp/orchestrate (Python lib) |
| [bmad-team-review](../bmad-team-review/SKILL.md) | Pick a reviewer ≠ writer based on paths; max 2 rounds; P3 ignore | Lifted from cross-model-review (identical in both projects) |
| [bmad-team-deploy](../bmad-team-deploy/SKILL.md) | `apply -k` + rollout restart on jump host, per env | Consolidated from `deploy-to-dev` + `deploy-to-staging` |
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
- ✅ `bmad-team-claim`, `bmad-team-review`, `bmad-team-orchestrate`, `bmad-team-deploy` migrated
- ✅ Per-project config template

### v0.1 (next 2 sprints, still internal)
- Adopt as submodule in Picks + vmcp; remove the per-project copies
- Reconcile `bmad-team-orchestrate` decision rules so they work without vmcp-specific assumptions
- Define and ship `bmad-team-verify` as a generic shape (project provides
  one shell hook that returns 0/non-zero); document via vmcp's existing
  `integrate-test` as a reference implementation

### v0.2
- Adopt [bmad-autonomous-development](https://github.com/stephenleo/bmad-autonomous-development)'s
  dependency-graph idea for ordering stories — orthogonal to our PR-claim,
  not a replacement

### v1.0 — external publication
- Publish `bmad-team/protocol.md` as a standalone spec on GitHub
  Discussions / Pages under the name **`pr-claim-protocol`** (working title)
- Submit `bmad-team-review` upstream against BMAD issues #446 and #685
- Drop `bmad-team-deploy` from external scope — it's generic CD glue

## Watch list

- **GitHub Agent HQ** — if GitHub introduces a native draft-PR claim primitive, our HTML markers may become redundant. Re-evaluate quarterly.
- **CodeRabbit / Greptile / Qodo Merge** — if any of them ship multi-model routing, the differentiation of `bmad-team-review` narrows.
- **BMAD upstream issues** #446, #685, #761 — if upstream ships official multi-agent extensions, plan a graceful merge.

## Why not just use $TOOL?

Briefly, the alternatives and why we did not adopt them:

| Alternative | Why not |
|---|---|
| `bmad-autonomous-development` | Uses dependency graph + counter, no PR-claim, same-vendor model tiering (sonnet/opus), no deploy. Useful for *ordering* only; doesn't solve cross-tool coordination. |
| `ccswarm`, `claude-flow`, `parallel-worktrees` | Worktree mechanics only. No cross-tool coordination, no review, no deploy. |
| `Delega` agent-to-agent code review | Configured per-task, not per-path. No stop rules. |
| GitHub Agent HQ | Centralizes coordination in GitHub's panel; not portable across Cursor/Codex/Claude as a *protocol*. |
| CodeRabbit `path_instructions` | Routes prompts, not model identity. We need writer ≠ reviewer enforced at the model level. |

## Maintenance

- This charter and `protocol.md` are reviewed quarterly.
- Each skill has its own SKILL.md; this charter is the meta-doc, not a per-skill spec.
- Issues against this repo: file under `Invist-AI/skills`.
