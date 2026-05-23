---
name: bmad-team-merge
description: Squash-merge a ready PR end-to-end without leaving zombie state. Runs the AGENTS.md merger checklist (no draft / title `(#issue)` / `Closes #issue` body / bmad-team-review comment / CI green), squash-merges, then atomically cleans up worktree + local branch + remote branch + closes the issue if the repo's default branch isn't the integration branch. Use when the user says "合 PR <N>" / "merge PR <N>" / "drive the merge" / "PR ready 了帮我合", or after another agent reports a PR ready for review.
license: MIT
metadata:
  version: "1.0.0"
  domain: workflow
  triggers: merge pr, 合 pr, squash merge, drive merge, pr ready, gh pr merge
  role: workflow
  scope: post-implementation
---

# bmad-team-merge

You are the merger. The PR is done. Your job is to land it on the integration branch (`dev` / `main` / whatever the repo uses) and then **leave zero zombies behind**: no draft state, no orphan worktree, no half-deleted local branch, no stale remote branch, no still-open issue.

Half-cleanup is worse than no cleanup — orphan remote branches accumulate fast in multi-agent repos, and a still-open issue after its PR merges silently breaks `Closes #N` audit assumptions.

## Core invariants

1. **Run the checklist before merging.** If any item fails, comment on the PR explaining what's missing and stop — do **not** merge a non-compliant PR to "be helpful". The checklist exists because someone got burned.
2. **One atomic cleanup pattern.** Merge ↔ cleanup ↔ issue-close are one operation. If you stop halfway, the repo carries inconsistent state.
3. **Never `--delete-branch` if a worktree still holds the branch.** `gh pr merge --delete-branch` deletes both local and remote; local delete fails when a worktree holds the branch, and the partial failure leaves the remote in a confusing state. Either remove the worktree first, or skip `--delete-branch` and clean up manually.
4. **Default branch ≠ integration branch matters.** If the repo's default branch is `main` but PRs merge to `dev` (common in this setup), `Closes #N` does **not** auto-close the issue on merge. You must close it manually.

## Pre-flight: AGENTS.md merger checklist

Pull the PR's metadata in one call and check:

```bash
gh pr view <N> --json isDraft,mergeable,mergeStateStatus,body,title,statusCheckRollup --jq \
  '{title, isDraft, mergeable, mergeStateStatus,
    has_closes: (.body | contains("Closes #")),
    ci_incomplete: [.statusCheckRollup[] | select(.status != "COMPLETED") | .name],
    ci_fail:      [.statusCheckRollup[] | select(.conclusion != null and ([.conclusion] | inside(["SUCCESS","NEUTRAL","SKIPPED"]) | not)) | .name]}'
# Require the actual cross-model-review artifact (its `## Cross-Model Review (round N)` heading),
# not just any comment mentioning the phrase — dev-loop's final brief contains a `**Cross-model review**`
# summary line that would otherwise satisfy this gate falsely.
gh pr view <N> --json comments --jq \
  '[.comments[] | select(.body | test("## Cross-Model Review \\(round|skipped review \\(small change\\)"; "i")) | .author.login]'
```

Then verify each item:

| # | Check | Hard fail if |
|---|---|---|
| 1 | `isDraft = false` | still draft → ask author to flip |
| 2 | title ends with `(#<issue>)` or `(no-issue)` | wrong format → ask author to fix title |
| 3 | body contains `Closes #<issue>` (or partial-scope `Refs #<issue>` if the PR ships only part of an umbrella issue) | neither → ask author to add |
| 4 | at least one bmad-team-review comment **OR** an explicit small-change skip marker on the PR (comment containing `skipped review (small change)`, per `bmad-team-review`'s `< 50 LOC` rule) | neither → block, ask author to run `bmad-team-review` and paste output, or paste the skip-marker comment if the change is genuinely trivial |
| 5 | `mergeable = MERGEABLE` and `mergeStateStatus = CLEAN` | `CONFLICTING` / `DIRTY` → ask author to rebase |
| 6 | `ci_incomplete` empty AND `ci_fail` empty. `ci_incomplete` catches queued / in-progress / cancelled checks (don't merge while CI is still running). `ci_fail` is non-success conclusions excluding `NEUTRAL`/`SKIPPED` (path-filtered steps that genuinely had no work). If incomplete, run `gh pr checks <N> --watch` and re-check. | either non-empty → block |

If `mergeable = UNKNOWN`, GitHub is still computing — wait 4-8 seconds and re-check; do not merge on UNKNOWN.

When you block a PR, leave a comment explaining what's missing. Example for missing bmad-team-review:

> [merger] no bmad-team-review comment yet — per AGENTS.md §4, please run `bmad-team-review` and paste the output as a PR comment before flipping ready. Then ping me.

## Merge sequence (the right way)

Once the checklist is green, run this **as one shell block** so partial failure is visible:

```bash
# 0. Resolve the PR's branch and its worktree path from git itself —
#    don't hardcode a path. The worktree may live anywhere the author
#    chose: `.claude/worktrees/<dir>`, `../<repo>-<agent>-<N>`, etc.
BR=$(gh pr view <N> --json headRefName --jq .headRefName)
WT=$(git worktree list --porcelain | awk -v br="refs/heads/$BR" '
  /^worktree /{w=$2} $0=="branch "br{print w}')
echo "branch: $BR"
echo "worktree: ${WT:-<none — author may have closed it already>}"

# 1. Squash-merge (do NOT use --delete-branch yet; worktree still holds the branch).
#    Check PIPESTATUS[0] explicitly — `gh pr merge ... | tail -2` masks the merge's
#    exit code and would let cleanup proceed on an unmerged PR (destructive).
gh pr merge <N> --squash 2>&1 | tail -2
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "❌ merge failed — leaving worktree/branches intact for diagnosis"; exit 1; }

# 2. Confirm it merged (in case --squash silently no-op'd)
gh pr view <N> --json state,mergedAt --jq '{state, mergedAt}'

# 3. Remove the worktree (releases the local branch). Skip if $WT empty.
[ -n "$WT" ] && git worktree remove "$WT"

# 4. Delete the local branch IF it exists locally. A merger running from a fresh
#    checkout may never have had this branch, in which case the remote-delete in
#    step 5 is still required — don't abort the rest of the cleanup.
git show-ref --verify --quiet "refs/heads/$BR" && git branch -D "$BR" || echo "  (no local branch $BR — nothing to delete locally)"

# 5. Delete the REMOTE branch — this is the step most often forgotten
git push origin --delete "$BR"

# 6. Sync local integration branch with the new squash commit. GitHub sometimes
#    hasn't propagated the squash by the time `gh pr merge` returns; pulling
#    immediately can land you on stale code that misses your own merge. Wait
#    until the merge SHA is fetchable, then pull.
SHA=$(gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid')
for i in 1 2 3 4 5; do
  git fetch --prune origin
  git cat-file -e "$SHA" 2>/dev/null && break
  echo "  (waiting for $SHA to propagate, retry $i/5)"; sleep 2
done
git cat-file -e "$SHA" 2>/dev/null || { echo "⚠️  squash commit $SHA not visible after retries — pull may leave you stale"; }
git pull origin <integration-branch>

# 7. Close the issue IF default branch ≠ integration branch AND PR fully closes it.
#    `Closes #N` auto-fires only on merge to default branch; here we do it manually.
#    For `Refs #N` partial-scope PRs, do NOT close — see "Partial-scope PRs" below
#    for the umbrella-comment pattern instead.
if gh pr view <N> --json body --jq '.body' | grep -q "Closes #"; then
  gh issue close <issue-num> --comment "Closed by PR #<N> (merged to <integration-branch>)."
else
  echo "ℹ️  partial-scope PR (Refs #...) — leaving issue open; post umbrella comment per the 'Partial-scope PRs' section."
fi
```

The remote-delete step (#5) is the one humans and agents both forget. It must be in the routine.

### Shorter alternative (worktree-first)

```bash
BR=$(gh pr view <N> --json headRefName --jq .headRefName)
WT=$(git worktree list --porcelain | awk -v br="refs/heads/$BR" '
  /^worktree /{w=$2} $0=="branch "br{print w}')
[ -n "$WT" ] && git worktree remove "$WT"
gh pr merge <N> --squash --delete-branch || { echo "❌ merge failed after worktree removed — re-add with: git worktree add $WT $BR"; exit 1; }
SHA=$(gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid')
for i in 1 2 3 4 5; do git fetch --prune origin; git cat-file -e "$SHA" 2>/dev/null && break; sleep 2; done
git pull origin <integration-branch>
if gh pr view <N> --json body --jq '.body' | grep -q "Closes #"; then
  gh issue close <issue-num> --comment "Closed by PR #<N>."
fi
```

Either path is fine — pick one and apply it consistently.

## Partial-scope PRs (umbrella issue stays open)

If the PR's body uses `Refs #<N>` instead of `Closes #<N>` because it only ships part of an umbrella issue's scope (common when an issue lists multiple deliverables):

- **Do not** auto-close the issue. The umbrella stays open until the last partial-scope PR.
- **Do** post a comment on the umbrella issue explaining which part landed and which is still pending:

```bash
gh issue comment <N> --body "Partial done: <what landed> by PR #<M>. Remaining: <what's left>, tracked in #<delta-issue>."
```

## When the next PR turns CONFLICTING after merging

Often happens when two in-flight PRs touch overlapping files. After your merge advances the integration branch, sibling PRs flip to `CONFLICTING`. **Do not rebase someone else's worktree branch yourself** (red line in AGENTS.md). Instead, post a comment on the affected PR with the exact rebase commands the author should run:

```
[merger] PR #<your merge> 已合到 <integration-branch>，本 PR 现在 CONFLICTING。请 rebase：

cd <your worktree>      # whichever path your claim's brief recorded
git fetch origin
git rebase origin/<integration-branch>
# 解冲突
git push -f origin <agent-id>/<branch>

rebase 完 ping 我合。
```

## Opening a PR: always set `--base` explicitly

`gh pr create` without `--base` falls back to the **repository default branch**, which in many setups (Picks included) is `main` even though PRs are supposed to merge into `dev`. A PR silently created against `main` will:

- Squash-merge into `main`, not `dev` — so the fix never reaches the integration branch where it's needed
- Often pass CI (it's the same diff), so nothing screams
- Diverge `main` and `dev` if they had different state on the touched files, causing later cherry-pick conflicts

Always pass `--base <integration-branch>` when creating PRs:

```bash
gh pr create --base dev --title "..." --body "..."
```

If you discover after merge that a PR landed on the wrong base, the fix is usually:

1. Cherry-pick the squash commit onto the correct branch
2. If cherry-pick conflicts (because the branches diverged on the touched file), abort and re-apply the diff manually
3. Push direct (with explicit user authorization) or open a fresh PR with the right `--base`

## Don't `git pull` immediately after `gh pr merge`

GitHub's API returns from `gh pr merge` before the server-side merge fully propagates. Running `git fetch && git pull` in the same shell block immediately after often shows "Already up to date" — the commit isn't on `origin/<branch>` yet from your fetch's perspective. Sleep 2-3 seconds before pulling, or verify with `gh pr view <N> --json mergedAt` first, or just re-fetch a few seconds later. If your "merged" PR's commit isn't on your local branch, the merge happened — your pull was just early.

## Alembic migration revision collisions (Picks-specific gotcha)

When two parallel PRs each add an Alembic migration, they often pick the same revision number (each saw the previous head but not each other's WIP). After both merge, `alembic heads` reports two heads and `upgrade head` fails — meaning the next deploy CrashLoopBackOffs.

**Detection**: after merging a PR that added a migration, run from any backend with alembic configured:

```bash
cd backend/<service>
uv run alembic heads 2>&1 | tail -3
```

Expect exactly one head. If you see two, you have a collision.

**Resolution**: chain the later-discovered migration after the earlier one (rename its revision and update `down_revision`). This requires writing actually-correct content to the file — `git mv` followed by `Edit` does NOT auto-stage the Edit; verify with `git diff --cached` before committing.

## Common failure modes

- **Forgetting remote-branch delete.** The repo accumulates orphan remote branches. Bake it into the routine.
- **Trying `--delete-branch` while worktree holds the branch.** Local delete fails; remote may or may not delete; you end up confused about what state things are in. Remove worktree first or skip the flag.
- **Merging on `mergeable = UNKNOWN`.** GitHub is recomputing. Wait 4-8 seconds; try again. Do not force.
- **Closing the issue when partial scope.** Read PR body carefully — if it says `Refs #N` instead of `Closes #N`, the author signaled the issue isn't fully resolved.
- **Editing a file after `git mv` and assuming `git add` picks it up.** Verify with `git diff --cached` before committing. The rename and the content edit are two separate stage events.
- **Trying to rebase someone else's worktree.** Red line. Comment with instructions instead.

## When NOT to use this skill

- The PR is not yours to merge (you didn't write it AND no merger role assigned).
- The repo allows direct push to `dev`/`main` and the user explicitly asked to bypass the PR ritual.
- The PR is intentionally being held open (e.g. waiting on a dependency PR to merge first) — close the loop by commenting, don't force-merge.

## Related skills

- [[bmad-team-claim]] — runs at the start of the work, claims the PR slot.
- [[bmad-team-review]] — runs before flipping draft → ready, produces the comment artifact this skill checks for.
- [[multi-agent-rules]] — bootstraps the AGENTS.md tiered rule system this skill enforces.
