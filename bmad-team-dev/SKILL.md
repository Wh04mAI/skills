---
name: bmad-team-dev
description: Drive the implementation phase between claim and merge. Reads the issue/spec, plans, then runs an inner loop of implement → verify → bmad-team-review → maybe-fixup until review converges, and emits a structured final briefing for operator sign-off before merge. Cross-model review is part of this loop, not a downstream skill. Use right after bmad-team-claim's execute briefing has been confirmed by the operator, or when the user says "开工", "开始干", "implement #N", "dev #N", "做 issue X", "继续干".
license: MIT
metadata:
  version: "2.0.0"
  domain: workflow
  triggers: dev loop, implement, 开工, 开始干, 做 issue, dev story, write code, iterate, fixup, smoke test, run tests, wrap up, 收工
  role: workflow
  scope: implementation
---

# bmad-team-dev

The phase between claim and merge used to be unmanaged. An agent would claim, then disappear into ad-hoc "write code, run tests, push, post completion" with no shared shape, no checkpoint criteria, no handoff format. Two failure modes followed:

1. **Silent drift** — the agent solves the wrong problem, or solves it in a way the operator can't sanity-check until merge time when reversal is expensive.
2. **Self-graded review** — the agent declares the work done without a different-model check, and a real bug ships.

This skill makes the phase explicit. It owns the **inner agent-autonomous loop** (implement ↔ bmad-team-review) and produces two operator-facing artifacts: nothing in the middle, and a **final briefing** at the end.

## The pipeline this skill sits in

```
bmad-team-claim
    └─ execute brief
        ↓
    ┌── HUMAN CONFIRM (scope) ──┐
    ↓                            │
bmad-team-dev                         │  ← operator can interrupt here if scope is wrong
    ├─ orient + plan             │
    ├─ INNER LOOP (autonomous):  │
    │   ├─ implement / fixup     │
    │   ├─ verify                │
    │   ├─ commit + push         │
    │   └─ bmad-team-review    │
    │       ├─ findings? → loop again
    │       ├─ clean?    → exit loop
    │       └─ escalate? → DECISION marker, surface to operator
    └─ final brief
        ↓
    ┌── HUMAN CONFIRM (ship) ────┐
    ↓                             │
bmad-team-merge                          │  ← operator can interrupt here if briefing reads wrong
    ├─ checklist
    ├─ squash merge
    ├─ delete remote + local branch + worktree
    └─ close issue
```

Two human gates, one autonomous loop. The agent does not surface mid-loop unless `bmad-team-review` escalates (architectural finding, max rounds reached, repeated finding without progress).

## Core invariants

1. **Wait for HUMAN CONFIRM after the execute brief.** `bmad-team-claim` produces an execute brief. Do not start implementing until the operator confirms the scope is right. This is the cheapest place to catch a misread.
2. **Cross-model review is inside this loop.** Every loop iteration ends with a review call. If the review finds nothing, you exit the loop. If it finds something, you fix and re-iterate. The review is not a separate downstream stage — it is the loop's exit gate.
3. **Wait for HUMAN CONFIRM after the final brief.** Do not invoke `bmad-team-merge` autonomously. The final brief gives the operator the artifact they need to approve shipping; merging is their decision.
4. **Work in the worktree, never the main checkout.** `bmad-team-claim` set this up. Editing in the main checkout fights with other agents and loses WIP.
5. **Reproduce before fixing** for any bug-shaped task. If you can't observe the bug, you can't trust the fix.
6. **Stop conditions are real.** Three iterations of the same failure mode → surface, don't grind. Two rounds of the same review finding without progress → escalate.

## Phase 1 — Orient (pre-loop, after HUMAN CONFIRM scope)

Before touching code, build a working model:

- Read the issue/story body in full. Note repro command, AC, constraints, hypothesized fix paths.
- `git log --oneline -10 -- <suspected dir>` and `git show <sha>` for the commit that introduced the regression. The author's commit message often explains the design intent the bug violates.
- Find the test/smoke that will verify the fix. If none exists, plan to add one.
- Identify cross-cutting risk: CGo, syscalls, multi-process state, migration, an interface implemented by mocks, an append-only registry. This drives Phase 2-Verify depth and Phase 5-Review override decisions.

Output: one paragraph in chat describing what you understand and what you plan. The operator can intercept here cheaply.

## Phase 2 — Plan (pre-loop)

For non-trivial work, surface 2-3 candidate fix paths (A/B/C) with one-line tradeoffs each, and pick. State the choice and why before implementing. Most tasks don't need this; bugs with non-obvious root causes do.

If the issue body already lists candidates, evaluate those rather than inventing new ones — the author has context you don't.

## Phase 3 — Implement (inner loop)

On loop iteration 1, this is the initial implementation. On iterations 2+, this is a fixup addressing review findings.

Standard rules apply (project AGENTS.md tiers govern: hard / soft / red lines). Notable habits:

- Edit the **minimum** set of files for the chosen path. Don't refactor on the side. Cleanup ideas → file an issue or note in final brief under "out of scope".
- For interface changes: update mocks (search for `MockX_methodName`) in the same commit so siblings don't break.
- For append-only files (signal registries, route tables, migrations), only append. Never reorder.
- For fixups responding to review findings: address each finding explicitly. If you disagree with one, push back in the briefing rather than silently ignoring.

## Phase 4 — Verify (inner loop)

Verification depth must match the change's blast radius:

| Change shape | Minimum verification |
|---|---|
| Pure refactor / docs / typo | `go vet` (or equivalent) + run any unit test that covers the touched function |
| Logic change in pure Go | unit tests in the touched package + `go test ./...` for the parent |
| Interface or contract change | unit tests + run any test that uses a mock of the interface |
| CGo / syscall / cross-process | unit tests + integration on a real environment (e.g. project's `integrate-test` skill) |
| Migration / schema | run the migration up + down on a real Postgres |
| Workflow / saga | unit + integration on a real Temporal |

For bug fixes:
1. **Reproduce on the real environment with pre-fix code** — confirm the bug exists where the issue says. If you can't reproduce, the model is wrong; stop and re-orient.
2. **Re-run after the fix** — confirm green. Bonus: temporarily revert your fix and confirm the test goes red. If it doesn't, your test isn't exercising the fix.

When the project ships an `integrate-test` (or equivalent) skill, prefer it over hand-rolled commands. If it doesn't cover your target host or scenario, file a follow-up to extend it rather than re-inventing the recipe inline.

When tests fail:
- Read the failure, form a hypothesis about root cause, then fix. Don't patch the symptom.
- Same failure mode 3× → stop and surface. You're modeling something wrong or the failure is environmental.
- Don't `--no-verify` past lint or pre-commit hook failures.

## Phase 5 — Commit + push (inner loop)

- Conventional commit subject: `<type>(#<issue> | story-N.M): <slug>`. Subject ≤ 70 chars.
- Body explains *why* the change is right, not what the diff already shows. Include verification artifacts (test name, duration, env).
- One commit per logical step is fine; squash-merge collapses them.
- Push to your own branch. PR stays draft until the loop converges.
- **Do not** add `Co-Authored-By` to intermediate WIP commits — only the final squash commit gets it (project AGENTS.md §4).

## Phase 6 — Cross-model review (inner loop, exit gate)

Invoke `bmad-team-review`. Its decision tree determines whether the loop continues:

- **0 P0/P1/P2 findings** → exit the loop, proceed to final brief.
- **1+ P1/P2 findings, under the round cap** → loop again with Phase 3 (fixup), then re-run review.
- **1+ P0 finding OR architectural / spec-level finding OR repeated finding without progress OR round cap reached** → `bmad-team-review` posts a `DECISION` marker; exit the loop and surface to the operator instead of producing the final brief. The loop terminates abnormally; do not invoke `bmad-team-merge`.

**Small-diff blast radius override**: `bmad-team-review` ships a "< 50 LOC → skip" heuristic tuned for typo fixes and trivial refactors. It is wrong for libvirt CGo paths, syscall-adjacent code, anything that replaces a process-wide invariant, anything that removes/replaces an interface method. If your change is small AND high-blast-radius, **explicitly request the override** when invoking `bmad-team-review` (don't trust the default skip).

(Story: PR #274 in vm-control-plane shipped a 48-LOC fix for issue #263 — well under skip threshold. Cross-model review was skipped initially. Operator pushed to run it anyway. Codex round 1 found a real P1 durability gap. Round 2, after a 122-LOC fixup, was clean. Without the override the gap would have shipped.)

CI verification (typically inside `bmad-team-review` itself, or right after) must pass before exiting the loop. A failing CI counts as a finding — fix and loop again.

## Phase 7 — Final brief (post-loop, before HUMAN CONFIRM ship)

Before composing the brief, flip the PR from draft to ready — `bmad-team-claim` opened it as draft, review has converged, so the WIP signal no longer applies. `bmad-team-merge`'s pre-flight hard-rejects drafts, so skipping this step dead-ends the pipeline at the operator's hands.

```bash
gh pr ready $PR_NUMBER
```

The final brief is the one mandatory operator-facing artifact this skill produces. Emit it in chat AND post it as a PR comment. Format:

```
## Dev loop final brief

**Scope**: <issue/story id + 1-line summary of what shipped>

**Files changed**:
- `path/to/file.go` — <one-line what + why if non-obvious>
- `path/to/file_test.go` — <new tests / updated assertions>
- `path/to/another.go:42` — <one-line what>

**Verification ran**:
- Unit: `<command>` → PASS (<duration if interesting>)
- Integration: `<command>` → PASS (<duration>)
- Smoke (env: <hostname / libvirt version / etc.>): `<command>` → PASS (<duration>)
- Pre-fix repro: <yes — same error as issue body | yes — different error: <what> | no — see "open questions">
- Post-fix re-run: <yes — green | no, because <reason>>
- CI: <green | failing — see open questions>

**Cross-model review** (rounds: <N>):
- Reviewer: <model> (selected by <path-rule>; writer was <self_model>)
- Round 1: <findings summary, e.g. "1 P1: durability gap on Undefine">
- Round N: <"clean — converged" | "..."]>

**Open questions for operator before merge**:
- <design tradeoff worth a second opinion>
- <thing the agent isn't sure about — say "not sure" out loud, don't paper over>
- <if no open questions, write "none — straightforward fix" so the operator knows you considered>

**Out of scope (filed as follow-up if non-trivial)**:
- <stale TODO discovered nearby + issue link if filed>
- <tech debt the agent saw but didn't touch>

**Diff size**: <total LOC across N files vs <base>>

**Next step**: awaiting HUMAN CONFIRM before invoking `bmad-team-merge`.
```

After posting, **stop**. Do not invoke `bmad-team-merge` on your own. Wait for the operator to say "ship it" / "合" / "merge" / equivalent. The operator may also intercept with corrections, additional review requests, or scope changes — handle those before re-emitting the brief.

## Anti-patterns

- ❌ Starting Phase 3 before HUMAN CONFIRM on the execute brief. Cheap to wait, expensive to redo.
- ❌ Treating `bmad-team-review` as optional or as a downstream stage. It is the loop's exit gate. No exit without a clean review (or an explicit operator override on the skip threshold).
- ❌ Auto-invoking `bmad-team-merge` after the final brief. The final brief is a handoff, not a self-authorization.
- ❌ Editing in the main checkout. Use the worktree from `bmad-team-claim`.
- ❌ Skipping the pre-fix repro because "the issue body says it's broken". The issue body is a hypothesis; verify.
- ❌ Burying open questions ("ah, this might be fine, let me not mention it"). The operator will find it; better you flag it.
- ❌ Filing 3 follow-up issues for things noticed in passing. Bundle related cleanup into one issue.
- ❌ `--no-verify` past lint or pre-commit hooks.
- ❌ Letting the loop run silently for 20 minutes between status updates.

## When NOT to use this skill

- Doc-only edits the operator is comfortable pushing direct.
- Hotfix where the operator explicitly said "skip the ceremony, push to dev".
- A pure exploration session ("can you check if X is feasible?") that won't produce a PR.

## Composition with project-specific skills

This skill is generic. Project-local skills handle the build/deploy/integration recipe used in Phase 4:

- `vm-control-plane`: `integrate-test` automates host-agent build + SSH tunnel + smoke run on vm-test-1 (and should be extended to vm-test-2/N).
- Other repos: typically a `Makefile` target like `make smoke` or `make integration` plays the same role.

If the project has no such skill and you find yourself hand-rolling the recipe twice, file a follow-up to extract one.

## Related skills

- [[bmad-team-claim]] — runs immediately before this skill; produces the worktree, draft PR, and execute brief that gates entry to this skill.
- [[bmad-team-review]] — invoked **inside** this skill's inner loop as the exit gate. Not a downstream skill.
- [[bmad-team-merge]] — runs after this skill's final brief is approved by HUMAN CONFIRM. Not invoked autonomously.
- [[multi-agent-rules]] — the AGENTS.md tier system that defines hard / soft / red-line rules this skill enforces.

The full pipeline: `bmad-team-claim` → [HUMAN CONFIRM] → `bmad-team-dev` (which contains `bmad-team-review`) → [HUMAN CONFIRM] → `bmad-team-merge`.
