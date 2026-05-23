---
name: priority-tags
description: Manage MoSCoW + incident priority labels on a GitHub repo. Bootstrap the label set, apply priorities with mutual exclusion, run a triage pass, and produce a health report. Use when the user says "/priority-tags ...", "set priority on issue X", "label this as must/should/could", "bootstrap priority labels in this repo", or "show me what needs triage".
metadata:
  short-description: Manage MoSCoW + incident priority labels on GitHub
---

# priority-tags

A small-team GitHub label scheme based on **MoSCoW** plus a dedicated **incident** lane, with a shell wrapper that keeps the labels mutually exclusive and produces a quick health report.

## Label set (6 labels)

| Label | Color | Meaning | SLA |
|---|---|---|---|
| `priority/now` | `#B60205` deep red | Incident — drop everything | minutes-to-respond, hours-to-fix |
| `priority/must` | `#D93F0B` red-orange | Must ship this iteration | this iteration |
| `priority/should` | `#FBCA04` yellow | Important; can slip | 1–2 iterations |
| `priority/could` | `#0E8A16` green | Nice to have | backlog |
| `priority/wont` | `#C5DEF5` light blue | Explicitly not this version | archive |
| `needs-triage` | `#BFD4F2` lavender | New / unprioritized | next triage meeting |

**Why `now` is separate from `must`**: `must` is a *planning* commitment (this sprint's scope); `now` is an *event* (system is on fire). Keeping them apart prevents `must` from getting abused as "very very urgent" and forces incidents into a real response flow.

**Why `wont` is in the set**: explicitly dropping work is healthier than letting a backlog accrete forever. "This version won't do" ≠ "never".

## Commands

When the user types `/priority-tags <subcommand> [args]`, run the script that ships with this skill:

```bash
<skill-dir>/priority_tags.sh <subcommand> [args] [--repo OWNER/NAME]
```

The skill directory is typically one of:
- `~/.claude/skills/priority-tags/` (symlinked / `npx skills add` install)
- `.claude/skills/priority-tags/` (submodule install in a project)

Pick whichever exists.

### Subcommands

| Command | Purpose |
|---|---|
| `init` | Create or update the 6 labels in the target repo. Idempotent. Run once per repo. |
| `list` | Show the priority labels with open-issue counts. |
| `set <num> <level>` | Apply `priority/<level>` to issue or PR `#num`, removing any other `priority/*` label first. `level` ∈ `now \| must \| should \| could \| wont`. |
| `clear <num>` | Remove all `priority/*` labels from `#num`. |
| `triage` | List open issues carrying `needs-triage` and open issues with no `priority/*` label. |
| `report` | Counts per level plus health warnings (stale incidents, must-inflation). |

All commands accept `--repo OWNER/NAME` to target a different repo; default is the repo detected from the current directory.

## Workflow examples

**Bootstrap a new repo**:
```
/priority-tags init
```

**Triage meeting**:
```
/priority-tags triage           # see what's unlabeled / pending
/priority-tags set 42 must      # commit issue #42 to this sprint
/priority-tags set 99 wont      # explicitly drop #99 for this version
/priority-tags report           # sanity-check distribution
```

**Incident response**:
```
/priority-tags set 137 now      # mark live incident
# ...resolve...
/priority-tags set 137 must     # downgrade follow-up work, or close the issue
```

## Health rules the `report` command enforces

- Any `priority/now` open longer than **24h** → escalation warning (incidents should be short-lived; if it's a multi-day problem it's not an incident, it's a project).
- More than **3** active `priority/now` items → systemic-problem warning (root-cause review).
- `priority/must` exceeding **60%** of all prioritized open items → priority-inflation warning (downgrade some).

These are advisory output only — the script never modifies labels except via explicit `set` / `clear`.

## Implementation notes

- Mutual exclusion is enforced inside `set`: it deletes any existing `priority/*` label on the target before applying the new one. Editing priority labels through the GitHub UI bypasses this — accept that, or always go through the skill.
- Label API calls go through the Issues endpoint (`/repos/{repo}/issues/{n}/labels`), which works identically for issues and pull requests since GitHub treats PRs as issues for labeling.
- Slash characters in label names are URL-encoded (`priority/now` → `priority%2Fnow`) when used in `DELETE` paths.

## Requirements

- `gh` CLI, authenticated (`gh auth status` should succeed).
- `jq` (used for timestamp math in `report`).

If either is missing, the script exits with a clear error.
