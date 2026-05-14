# Orchestrate Skill

Multi-agent orchestrator for parallel story implementation. Operator-facing
docs live here; the agent-facing summary is in [SKILL.md](SKILL.md).

For day-to-day usage (commands, workflow, decision protocol), see
[SKILL.md](SKILL.md). This file covers configuration, extension, and
troubleshooting.

## Architecture

```
orchestrate/
├── orchestrate.py            # Entry point (~2-3s for gh-backed status)
├── SKILL.md                  # Agent-facing summary
├── README.md                 # This file (operator-facing)
├── customize.toml            # Base config
├── decision-rules.yaml       # Auto-decision patterns
├── requirements.txt
├── lib/
│   ├── orchestrator.py       # Dispatch engine, prompt generation
│   ├── unified_monitor.py    # Single-process monitor loop
│   ├── decision_monitor.py   # DECISION ↔ RESPONSE handler
│   ├── pr_monitor.py         # PR state polling, status sync
│   ├── github_channel.py     # gh CLI wrapper + marker parsing
│   ├── next_advisor.py       # Bundle detection (used by status hint)
│   └── adapters/
│       ├── base.py
│       ├── github_pr_adapter.py  # Derives status from GitHub PR state
│       └── file_scanner.py
└── templates/
    ├── dispatch-bmad.md      # BMad project template (full Pre-flight)
    ├── dispatch-bundle.md    # Serial-bundle template
    └── dispatch-minimal.md   # Generic template
```

## Configuration

### Base config (`customize.toml`)

```toml
[orchestrator]
max_concurrent_agents = 5
poll_interval_seconds = 60          # decision queue
pr_poll_interval_seconds = 120      # PR state

[adapter]
type = "github_pr"                  # or "file_scanner"

[adapter.github_pr]
story_files_dir = "_bmad-output/implementation-artifacts"

[dispatch]
template = "dispatch-bmad.md"
agent_id_prefix = "claude"
agent_id_sequence = ["A", "B", "C", "D", "E", "F"]

[github]
required_checks = ["Lint", "Test", "Replay Test"]

[security]
trusted_authors = ["haojianggod", "wswuai"]
```

### Project override (`_bmad/custom/orchestrate.toml`)

Same structure, deep-merged on top of the base. Override only the keys
you need:

```toml
[orchestrator]
max_concurrent_agents = 3

[github]
required_checks = ["build", "lint"]
```

## Decision Rules (`decision-rules.yaml`)

`--monitor` matches each DECISION marker against these patterns. Edit
to customize auto-decisions:

```yaml
rules:
  - id: cross-cutting-defer
    pattern: "cross-cutting|mark.*OpFailed.*孤儿"
    action: defer
    reason: "与已合并 story 同形，需 cross-cutting 改造"

  - id: high-severity-apply
    pattern: "HIGH.*(goroutine 泄漏|鉴权绕过)"
    action: apply_patch
    reason: "合并阻塞级别，必须修"

escalate:
  - pattern: "架构.*决议|ADR|breaking change"
    reason: "架构级决策需人工审批"
```

A DECISION that matches no rule is escalated to the human (printed by
the monitor and surfaced in `/orchestrate` status's next-step hint).

## Adapters

Built-in:

- **github_pr** — derives story status from GitHub PR state (merged=done,
  open+draft=in-progress, open+!draft=review). Falls back to the `Status:`
  field in story files for stories without PRs. Requires `gh` CLI.
- **file_scanner** — scans story Markdown files, parses status from frontmatter

### Adding a new adapter

1. Create `lib/adapters/my_adapter.py`:

   ```python
   from adapters.base import StoryAdapter

   class MyAdapter(StoryAdapter):
       def list_ready_stories(self) -> list[str]: ...
       def get_story_details(self, story_id: str) -> dict: ...
       def update_story_status(self, story_id: str, status: str) -> None: ...
       def get_project_context(self) -> dict: ...
   ```

2. Register in `orchestrate.py` (`initialize_adapter`):

   ```python
   elif adapter_type == 'my_adapter':
       return MyAdapter(config['adapter']['my_adapter'])
   ```

3. Add config section to `customize.toml`:

   ```toml
   [adapter.my_adapter]
   api_url = "https://api.example.com"
   ```

## Code review — dev-agent driven

The orchestrator monitor does **not** trigger code review. Each dev
agent runs `/bmad-team-review` itself before posting
`ORCHESTRATE:COMPLETION`:

1. Agent finishes implementation, local tests / lints pass
2. Agent runs `.claude/skills/bmad-team-review/` — the skill picks a
   reviewer model **different from the writer** based on the touched
   paths, runs the review, and decides per its decision tree (fixup
   and re-run, post COMPLETION, or escalate via `ORCHESTRATE:DECISION`)
3. Review findings are posted to the PR as `ORCHESTRATE:REVIEW`
4. After at most 2 fixup rounds, agent posts `ORCHESTRATE:COMPLETION`
5. Orchestrator (`--monitor`) responds to any `ORCHESTRATE:DECISION`
   markers and, once CI is green and the agent is done, posts
   `ORCHESTRATE:APPROVAL` — human merges

Full contract: `.claude/skills/bmad-team-review/SKILL.md` and
AGENTS.md "Pre-merge self-review".

## Troubleshooting

**Status command is slow.** It makes 2 `gh` API calls per invocation
(~1-2s each). This is expected — the trade-off for always-accurate
status without a manual file to maintain.

**`pbcopy: command not found`.** The dispatch flow falls back to
`xclip` (Linux X11) and `wl-copy` (Wayland) automatically. If none
exist, prompts are written to `/tmp/orchestrate-prompts/` and the
output points you at the right `cat` command.

**Stories not found.** Check `customize.toml` `[adapter.github_pr]
story_files_dir` path — it's relative to your current working directory,
not the skill directory.

**`--monitor` shows nothing.** Verify `gh auth status` is logged in
and that PRs have `<!-- ORCHESTRATE:CLAIM ... -->` markers. Without
CLAIM markers, the monitor has nothing to track.

**RESPONSE markers ignored.** The sub-agent only honors RESPONSE
comments from authors listed in `[security].trusted_authors`. Either
add your GitHub login there or ensure `gh api user --jq .login`
returns the right name (`gh auth status` to check).

**Re-dispatching a story.** Story IDs already in `in-progress` can be
re-dispatched; the orchestrator just regenerates the prompt without
flipping status. Useful when an agent loses its context.

## Logs

`~/.claude/logs/orchestrate-YYYYMMDD.log` — one file per day, rolled
by date. Contains every command + dispatch + monitor event, including
the full args and resolved story IDs.

## License

Part of vm-control-plane project (planned: extract to a standalone
skill repo so it can be pinned per-project; tracked separately).
