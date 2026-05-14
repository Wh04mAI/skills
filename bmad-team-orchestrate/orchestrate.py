#!/usr/bin/env python3
"""
Orchestrate skill entry point — compact UX.

Output budget:
- status:   <= 8 lines (epic summary + active agents + next-step hint)
- dispatch: 1 line per story + 2 lines hint, first prompt auto-copied to clipboard
- monitor:  streaming events, no periodic full-state dumps

All commands and flags:
    /orchestrate                       status + next-step hint (default)
    /orchestrate <ids...>              dispatch stories (first prompt → clipboard)
    /orchestrate --all                 dispatch all ready stories
    /orchestrate --bundle <ids...>     bundle stories to one agent (serial)
    /orchestrate --monitor             monitor decisions + PRs + reviews
    /orchestrate --list                list every known story (debug)
    /orchestrate --dry-run <ids...>    preview dispatch without writing state
    /orchestrate --agent-id NAME …     override agent id (--bundle / single story)
    /orchestrate --help                full help
    /orchestrate --debug ...           verbose logging to console
"""

# --- venv bootstrap ---------------------------------------------------------
# Re-exec under sibling .venv/bin/python so PyYAML and other deps resolve
# without polluting the system Python. Auto-creates the venv on first run
# in each worktree (uv required).
import os as _os
import sys as _sys
from pathlib import Path as _Path

_SKILL_DIR = _Path(__file__).resolve().parent
_VENV_DIR = _SKILL_DIR / ".venv"
_VENV_PY = _VENV_DIR / "bin" / "python"
if _sys.executable != str(_VENV_PY):
    if not _VENV_PY.exists():
        import subprocess as _sp
        print(f"[orchestrate] bootstrapping venv at {_VENV_DIR}", file=_sys.stderr)
        _sp.run(["uv", "venv", str(_VENV_DIR)], check=True)
        _sp.run(
            ["uv", "pip", "install", "--python", str(_VENV_PY),
             "-r", str(_SKILL_DIR / "requirements.txt")],
            check=True,
        )
    _os.execv(str(_VENV_PY), [str(_VENV_PY), *_sys.argv])
# --- end bootstrap ----------------------------------------------------------

import logging
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path

skill_root = Path(__file__).parent
sys.path.insert(0, str(skill_root / "lib"))

from adapters.github_pr_adapter import GitHubPRAdapter
from adapters.file_scanner import FileScannerAdapter
from orchestrator import Orchestrator
from unified_monitor import UnifiedMonitor


# ------------------------------------------------------------------ logging --

def setup_logging(debug=False):
    log_dir = Path.home() / '.claude' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f'orchestrate-{datetime.now():%Y%m%d}.log'

    handlers = [logging.FileHandler(log_file)]
    if debug:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=handlers,
    )
    logger = logging.getLogger('orchestrate')
    logger.info(f"session start (log: {log_file})")
    return logger


# ------------------------------------------------------------ config + deps --

def load_config():
    """Merge customize.toml with _bmad/custom/orchestrate.toml override."""
    with open(skill_root / "customize.toml", 'rb') as f:
        config = tomllib.load(f)

    project_override = Path.cwd() / "_bmad/custom/orchestrate.toml"
    if project_override.exists():
        with open(project_override, 'rb') as f:
            override = tomllib.load(f)
        for key, value in override.items():
            if isinstance(value, dict) and key in config:
                config[key].update(value)
            else:
                config[key] = value
    return config


def initialize_adapter(config):
    adapter_type = config['adapter']['type']
    if adapter_type == 'github_pr':
        return GitHubPRAdapter(config['adapter']['github_pr'])
    if adapter_type == 'file_scanner':
        return FileScannerAdapter(config['adapter']['file_scanner'])
    print(f"❌ Unknown adapter: {adapter_type} (use github_pr or file_scanner)")
    sys.exit(1)


def resolve_story_ids(raw_ids, adapter):
    """Resolve shorthand like '6.1' / '6-1' to full story IDs.

    Matches first against ready-for-dev, then falls back to all known
    stories (covers re-dispatch). Errors out fast on miss with fuzzy
    suggestions instead of dumping the full story list.
    """
    from difflib import get_close_matches

    try:
        if hasattr(adapter, 'get_all_known_stories'):
            all_known = adapter.get_all_known_stories()
        else:
            all_known = adapter.list_ready_stories()
    except Exception:
        all_known = adapter.list_ready_stories()

    resolved = []
    for raw in raw_ids:
        if raw in all_known:
            resolved.append(raw)
            continue
        normalized = raw.replace('.', '-')
        matches = [s for s in all_known if s.startswith(normalized + '-') or s == normalized]
        if len(matches) == 1:
            resolved.append(matches[0])
        elif len(matches) > 1:
            print(f"❌ {raw} ambiguous: {', '.join(matches)}")
            sys.exit(1)
        else:
            fuzzy = get_close_matches(normalized, all_known, n=3, cutoff=0.6)
            print(f"❌ {raw} not found" + (f" (did you mean: {', '.join(fuzzy)}?)" if fuzzy else ""))
            sys.exit(1)
    return resolved


# ----------------------------------------------------------------- output ----

def _epic_lines(adapter):
    """One line per epic — 'Epic 6  4/7 done · 2 ready · 1 in-progress'.

    Hides fully-done epics to keep output tight; if every epic is done,
    surfaces a single 'all epics complete' line so we don't print nothing.
    """
    try:
        stats = adapter.get_epic_statistics()
    except AttributeError:
        return []
    if not stats:
        return []

    def _epic_num(name):
        try:
            return int(name.split()[1])
        except (IndexError, ValueError):
            return 0

    lines = []
    hidden_done = 0
    for name in sorted(stats.keys(), key=_epic_num):
        s = stats[name]
        if s['total'] > 0 and s['done'] == s['total']:
            hidden_done += 1
            continue
        parts = [f"{s['done']}/{s['total']} done"]
        if s.get('in_progress'):
            parts.append(f"{s['in_progress']} in-progress")
        if s.get('review'):
            parts.append(f"{s['review']} in-review")
        if s.get('ready'):
            parts.append(f"{s['ready']} ready")
        lines.append(f"{name:<8} {' · '.join(parts)}")

    if not lines and hidden_done:
        lines.append(f"✨ All {hidden_done} epics complete")
    return lines


def _local_next_hint(adapter, max_concurrent):
    """Local-only next-step hint — no GitHub round-trip.

    `cmd_status` runs on every keystroke, so it must complete in well
    under a second. Anything that touches `gh` (active-PR discovery,
    pending escalations, CI checks) belongs in `--monitor`, not here.
    """
    try:
        ready = adapter.list_ready_stories()
    except Exception:
        return ""
    if not ready:
        return "✨ All ready stories dispatched — use --monitor to track progress"

    from next_advisor import detect_bundles
    bundles = detect_bundles(ready)
    if bundles:
        bundle = bundles[0]
        return (
            f"🔗 Next: {len(bundle)} stories share keywords (likely conflict)\n"
            f"   → /orchestrate --bundle {' '.join(bundle)}"
        )

    take = min(2, max_concurrent)
    return f"🚀 Next: /orchestrate {' '.join(ready[:take])}"


def _copy_to_clipboard(text):
    """Pipe text into pbcopy / xclip / wl-copy. Returns tool name or None."""
    for cmd, args in (
        ('pbcopy', ['pbcopy']),
        ('xclip', ['xclip', '-selection', 'clipboard']),
        ('wl-copy', ['wl-copy']),
    ):
        if shutil.which(cmd):
            try:
                subprocess.run(args, input=text, text=True, check=True, timeout=5)
                return cmd
            except (subprocess.SubprocessError, OSError):
                return None
    return None


def _clipboard_recopy_command(path: Path) -> str:
    """Shell snippet to refill clipboard from a prompt file.

    Bash `pbcopy < huge.txt` truncates silently on macOS; `python3 -c …`
    piping the decoded string stdin does not.
    """
    p_res = path.resolve()
    py_src = (
        'import pathlib, subprocess; subprocess.run(["pbcopy"], '
        f'input=pathlib.Path({str(p_res)!r}).read_text(), text=True, check=True)'
    )
    if shutil.which('pbcopy'):
        return 'python3 -c ' + shlex.quote(py_src)
    quoted = shlex.quote(str(p_res))
    if shutil.which('xclip'):
        return f'xclip -selection clipboard < {quoted}'
    if shutil.which('wl-copy'):
        return f'wl-copy < {quoted}'
    return f'cat {quoted}'


def strip_agent_id_flag(argv):
    """Remove ``--agent-id <value>`` tokens before positional story parsing."""
    cleaned = []
    override = None
    i = 0
    while i < len(argv):
        if argv[i] == '--agent-id':
            if i + 1 >= len(argv):
                raise ValueError('--agent-id requires a value (e.g. claudeD)')
            override = argv[i + 1]
            i += 2
            continue
        cleaned.append(argv[i])
        i += 1
    return cleaned, override


# ---------------------------------------------------------------- commands ---

def cmd_status(config, adapter):
    """Compact local status — epic summary + next-step hint, no GitHub.

    Reads local story files + one-time GitHub PR query (~2 `gh` calls).
    PR state, pending decisions, and CI status surface via `--monitor`.
    """
    lines = _epic_lines(adapter)

    max_concurrent = config['orchestrator'].get('max_concurrent_agents', 5)
    hint = _local_next_hint(adapter, max_concurrent)
    if hint:
        lines.append(hint)

    if not lines:
        lines.append("✨ Nothing to show — no story files found")

    print()
    for line in lines:
        print(line)
    print()


def cmd_list(adapter):
    stories = adapter.list_ready_stories()
    print(f"\n📋 Ready stories ({len(stories)}):")
    for sid in stories:
        try:
            print(f"  • {sid}: {adapter.get_story_details(sid)['title']}")
        except Exception:
            print(f"  • {sid}")
    print()


def cmd_monitor(config, adapter):
    rules_path = skill_root / "decision-rules.yaml"
    UnifiedMonitor(config, adapter, rules_path).run()


def cmd_help():
    print("""
/orchestrate                      Status + next-step hint (default, ≤ 8 lines)
/orchestrate <ids...>             Dispatch stories (first prompt → clipboard)
/orchestrate --all                Dispatch all ready (up to max_concurrent)
/orchestrate --bundle <ids...>    Bundle to one agent (serial execution)
/orchestrate --monitor            Monitor decisions + PRs + reviews
/orchestrate --list               List every ready story
/orchestrate --dry-run <ids...>   Preview dispatch without writing state
/orchestrate --agent-id NAME …  Override agent id (--bundle or single story id)
/orchestrate --debug ...          Verbose logging to console

Story IDs: shorthand '6.1' / '6-1' or full '6-1-template-query-api'.
Config:    customize.toml (base) + _bmad/custom/orchestrate.toml (override).
Prompts:   first prompt copied to clipboard, all saved to /tmp/orchestrate-prompts/.
Logs:      ~/.claude/logs/orchestrate-YYYYMMDD.log
""")


def cmd_dispatch(
    config, adapter, orchestrator, story_ids, *,
    bundle, dry_run, agent_id_override=None,
):
    """Generate prompts, write to /tmp, copy first to clipboard."""
    if not story_ids:
        print("❌ No stories to dispatch")
        return
    if bundle and len(story_ids) < 2:
        print("❌ --bundle requires at least 2 stories")
        return

    if agent_id_override is not None:
        if bundle:
            pass  # bundle always allocates exactly one logical agent slot
        elif len(story_ids) != 1:
            print("❌ --agent-id only with --bundle or exactly one story id (not `--all`/multi)")
            return

    if dry_run:
        mode = "bundle/serial" if bundle else "parallel"
        print(f"\n🔍 DRY-RUN [{mode}] — would dispatch {len(story_ids)} stories:")
        for sid in story_ids:
            print(f"  • {sid}")
        if agent_id_override:
            print(f"  (agent override {agent_id_override})")
        print()
        return

    if bundle:
        prompts = orchestrator.dispatch_bundle(
            story_ids, agent_id_override=agent_id_override,
        )
    else:
        prompts = orchestrator.dispatch_stories(
            story_ids, agent_id_override=agent_id_override,
        )

    # /tmp keeps paths short and pasteable; on macOS this resolves through
    # /private/tmp which `pbcopy` reads happily. Fall back to the platform
    # tempdir (e.g. Windows %TEMP%) if /tmp isn't writable.
    prompt_dir = Path('/tmp') / 'orchestrate-prompts'
    try:
        prompt_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        prompt_dir = Path(tempfile.gettempdir()) / 'orchestrate-prompts'
        prompt_dir.mkdir(parents=True, exist_ok=True)

    items = []  # [(agent_id, story_id, filepath, prompt)]
    for agent_id, prompt in prompts.items():
        sid = orchestrator.active_agents[agent_id]['story_id']
        filepath = prompt_dir / f'{agent_id}-{sid}.txt'
        filepath.write_text(prompt)
        items.append((agent_id, sid, filepath, prompt))

    # First prompt → clipboard; recopy hints avoid macOS `pbcopy < file` truncation.
    first_agent, first_sid, first_path, first_prompt = items[0]
    clip_tool = _copy_to_clipboard(first_prompt)

    print()
    if clip_tool:
        print(f"✓ {first_agent} → {first_sid}  →  clipboard ({clip_tool})")
    else:
        print(f"✓ {first_agent} → {first_sid}  →  {first_path}")

    for agent_id, sid, filepath, _ in items[1:]:
        print(f"✓ {agent_id} → {sid}  →  {filepath}")

    print()
    if clip_tool:
        print(f"💡 Paste {first_agent} into a new agent session.")
    else:
        print(f"💡 Copy {first_agent}: cat {first_path}")
    if len(items) > 1:
        next_path = items[1][2]
        print(f'   Next (recopy): {_clipboard_recopy_command(next_path)}')
    elif clip_tool:
        print(f'   Recopy full prompt: {_clipboard_recopy_command(first_path)}')
    print()


# -------------------------------------------------------------------- main --

def parse_args(argv):
    """Hand-rolled parser — argparse fights with positional story-id lists.

    Returns dict with: command, story_ids, bundle, all, dry_run, debug.
    """
    args = {
        'command': 'status',
        'story_ids': [],
        'bundle': False,
        'all': False,
        'dry_run': False,
        'debug': False,
    }

    flags = set()
    positional = []
    for a in argv:
        if a.startswith('--'):
            flags.add(a)
        elif a.startswith('-') and len(a) > 1:
            flags.add(a)
        else:
            positional.append(a)

    if '--debug' in flags:
        args['debug'] = True
        flags.discard('--debug')

    if '--help' in flags or '-h' in flags:
        args['command'] = 'help'
        return args
    if '--list' in flags:
        args['command'] = 'list'
        return args
    if '--monitor' in flags:
        args['command'] = 'monitor'
        return args

    if positional or '--all' in flags or '--bundle' in flags:
        args['command'] = 'dispatch'
        args['story_ids'] = positional
        args['bundle'] = '--bundle' in flags
        args['all'] = '--all' in flags
        args['dry_run'] = '--dry-run' in flags

    return args


def main():
    try:
        argv2, agent_id_override = strip_agent_id_flag(sys.argv[1:])
    except ValueError as e:
        print(f"\n❌ {e}")
        sys.exit(2)

    args = parse_args(argv2)
    logger = setup_logging(args['debug'])

    try:
        config = load_config()
        adapter = initialize_adapter(config)
        orchestrator = Orchestrator(config, adapter)

        cmd = args['command']
        logger.info(
            f"command={cmd} story_ids={args['story_ids']} "
            f"bundle={args['bundle']} all={args['all']} "
            f"agent_id_override={agent_id_override!r}"
        )

        if cmd == 'help':
            cmd_help()
        elif cmd == 'list':
            if agent_id_override is not None:
                print("\n❌ --agent-id only applies when dispatching stories")
                sys.exit(2)
            cmd_list(adapter)
        elif cmd == 'monitor':
            if agent_id_override is not None:
                print("\n❌ --agent-id only applies when dispatching stories")
                sys.exit(2)
            cmd_monitor(config, adapter)
        elif cmd == 'dispatch':
            if args['all']:
                story_ids = adapter.list_ready_stories()
                story_ids = story_ids[:config['orchestrator']['max_concurrent_agents']]
            else:
                story_ids = resolve_story_ids(args['story_ids'], adapter)
            cmd_dispatch(
                config, adapter, orchestrator, story_ids,
                bundle=args['bundle'], dry_run=args['dry_run'],
                agent_id_override=agent_id_override,
            )
        else:
            if agent_id_override is not None:
                print("\n❌ --agent-id is only valid with dispatch (--bundle / story ids)")
                sys.exit(2)
            cmd_status(config, adapter)

    except Exception as e:
        logger.exception("error in main")
        print(f"\n❌ {e}")
        if args['debug']:
            raise
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  interrupted")
        sys.exit(130)
