"""
Core orchestrator engine.

Generates dispatch prompts for sub-agents and tracks the in-process
list of active agents. Decision/PR/review monitoring lives in the
dedicated *_monitor modules and reads state from GitHub PR comments.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set
from adapters.base import StoryAdapter


def resolve_trusted_authors(config: Dict) -> List[str]:
    """GitHub logins whose ORCHESTRATE:RESPONSE markers are honored.

    Resolution order: `[security].trusted_authors` from config, else
    `gh api user --jq .login`. Used by both the orchestrator-side
    monitor (to filter incoming RESPONSE markers) and dispatched
    sub-agents (baked into their poll filter), so both ends agree.
    """
    configured = config.get('security', {}).get('trusted_authors')
    if configured:
        return list(configured)
    try:
        result = subprocess.run(
            ['gh', 'api', 'user', '--jq', '.login'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            login = result.stdout.strip()
            if login:
                return [login]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def _trusted_authors_for_dispatch(config: Dict) -> List[str]:
    """Same as resolve_trusted_authors, but raises if empty so we don't
    silently dispatch a prompt whose bash filter would reject every
    RESPONSE comment and leave the agent stuck."""
    authors = resolve_trusted_authors(config)
    if not authors:
        raise RuntimeError(
            "Cannot resolve trusted RESPONSE authors. Set "
            "[security].trusted_authors in customize.toml or run "
            "`gh auth login` so `gh api user --jq .login` succeeds."
        )
    return authors


_CLAIM_AGENT_RE = re.compile(
    r'<!--\s*ORCHESTRATE:CLAIM\s[^>]*\bagent=([^\s/>]+)',
    re.IGNORECASE,
)


_HANDLE_SANITIZE_RE = re.compile(r'[^a-z0-9]+')


def _sanitize_handle(raw: str) -> str:
    """Lowercase + alnum-only + 12-char cap. Empty → ''. """
    if not raw:
        return ''
    cleaned = _HANDLE_SANITIZE_RE.sub('', raw.strip().lower())
    return cleaned[:12]


def _detect_human_handle() -> str:
    """Resolve the local operator's identity for agent-ID prefixing.

    Cascade: gh login → git user.email local-part → git user.name → 'anon'.
    Result is always sanitized (lowercase alnum, ≤12 chars).
    """
    try:
        r = subprocess.run(
            ['gh', 'api', 'user', '--jq', '.login'],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            handle = _sanitize_handle(r.stdout)
            if handle:
                return handle
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    try:
        r = subprocess.run(
            ['git', 'config', 'user.email'],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and '@' in r.stdout:
            handle = _sanitize_handle(r.stdout.split('@', 1)[0])
            if handle:
                return handle
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    try:
        r = subprocess.run(
            ['git', 'config', 'user.name'],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            handle = _sanitize_handle(r.stdout)
            if handle:
                return handle
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return 'anon'


def _resolve_agent_prefix(dispatch_cfg: Dict) -> str:
    """Compute the agent-ID prefix for this machine.

    Precedence:
    1. Explicit `agent_id_prefix` (legacy) — returned as-is.
    2. `<human>-<tool>-` where human = gh login (sanitized) and
       tool = `agent_tool` (default 'claude').
    """
    explicit = dispatch_cfg.get('agent_id_prefix')
    if explicit:
        return explicit
    tool = dispatch_cfg.get('agent_tool', 'claude')
    human = _detect_human_handle()
    return f"{human}-{tool}-"


def _allocate_agent_ids(
    *,
    prefix: str,
    sequence: List[str],
    claimed: Set[str],
    needed: int,
) -> List[str]:
    """Pick successive agent IDs (`prefix + suffix`) not in `claimed`."""
    if needed <= 0:
        return []
    used: Set[str] = set(claimed)
    picks: List[str] = []
    for _ in range(needed):
        found = False
        for suffix in sequence:
            aid = prefix + suffix
            if aid not in used:
                picks.append(aid)
                used.add(aid)
                found = True
                break
        if not found:
            raise RuntimeError(
                'No free dispatch agent IDs: every slot in '
                f'{[prefix + s for s in sequence]} is already claimed '
                'on an open GitHub PR. Close PRs / remove duplicates, omit '
                'parallel dispatches needing more IDs, pass --dry-run without '
                'gh, or use --agent-id for a bundle/single-story run.'
            )
    return picks


def gather_claimed_agent_ids_from_github(
    *,
    author_filter: str = "",
    timeout_s: float = 12,
    limit_prs: int = 40,
) -> Set[str]:
    """Parse agent= values from <!-- ORCHESTRATE:CLAIM ... --> in open PR comments.

    On `gh` errors or timeouts, returns an empty set (fail-open for dispatch).

    Honors the same `[monitor].author_filter` as the unified monitor —
    when set (e.g. ``@me``), only scans PRs authored by that GitHub actor.
    """
    claimed: Set[str] = set()
    cmd = [
        'gh', 'pr', 'list', '--state', 'open', '--limit', str(limit_prs),
        '--json', 'number',
    ]
    ath = author_filter.strip()
    if ath:
        cmd.extend(['--author', ath])
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
        if out.returncode != 0:
            return claimed
        for row in json.loads(out.stdout or '[]'):
            num = row.get('number')
            if num is None:
                continue
            vc = subprocess.run(
                ['gh', 'pr', 'view', str(num), '--json', 'comments,body'],
                capture_output=True, text=True, timeout=timeout_s,
            )
            if vc.returncode != 0:
                continue
            blob = vc.stdout or ''
            payload = {}
            try:
                payload = json.loads(blob)
            except json.JSONDecodeError:
                continue
            for text in ([payload.get('body') or ''] +
                         [(c.get('body') or '') for c in (payload.get('comments') or [])]):
                claimed.update(_CLAIM_AGENT_RE.findall(text))
        return claimed
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return claimed


def _trusted_authors_jq(authors: List[str]) -> str:
    """Render authors as the inside of a jq IN(...) call. Each author
    must be a GitHub login (alphanumerics + dash/underscore), so simple
    quoting is safe."""
    return ", ".join(f'"{a}"' for a in authors)


def short_story_id(story_id: str) -> str:
    """Extract the leading "<epic>.<num>" form from a full story ID.

    Examples:
        "7-2-cordon-uncordon-api" -> "7.2"
        "10-3-foo"                -> "10.3"

    Raises:
        ValueError: if story_id doesn't start with two numeric segments.
    """
    parts = story_id.split('-')
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(
            f"Invalid story_id format: {story_id!r} (expected "
            f"'<epic>-<num>-<slug>' with numeric epic and num)"
        )
    return f"{parts[0]}.{parts[1]}"


def _bundle_id(story_ids: List[str]) -> str:
    """Canonical bundle ID used both as template variable and tracking key."""
    return "bundle-" + "-".join(short_story_id(s) for s in story_ids)


class Orchestrator:
    """Multi-agent orchestrator for parallel story implementation."""

    def __init__(self, config: Dict, adapter: StoryAdapter):
        """
        Initialize orchestrator.

        Args:
            config: Configuration from customize.toml
            adapter: Story tracking adapter
        """
        self.config = config
        self.adapter = adapter
        self.active_agents = {}  # {agent_id: {story_id, pr_number, status}}

    def generate_dispatch_prompt(self, story_id: str, agent_id: str) -> str:
        """
        Generate dispatch prompt for a story.

        Args:
            story_id: Story identifier
            agent_id: Agent identifier (e.g., 'claudeA')

        Returns:
            Complete dispatch prompt
        """
        story_details = self.adapter.get_story_details(story_id)
        project_context = self.adapter.get_project_context()

        # Load template
        template_name = self.config.get('dispatch', {}).get('template', 'dispatch-minimal.md')
        template_path = Path(__file__).parent.parent / 'templates' / template_name

        if not template_path.exists():
            raise FileNotFoundError(f"Dispatch template not found: {template_path}")

        template = template_path.read_text()

        # Substitute variables. `prompt_file_path` mirrors the path
        # `cmd_dispatch` writes the prompt to (see orchestrate.py); the
        # agent uses it in Step 1.5 to copy the prompt into its own
        # worktree for re-reading after context truncation.
        variables = {
            'agent_id': agent_id,
            'story_id': story_id,
            'story_title': story_details['title'],
            'story_file': story_details['file_path'],
            'story_hints': story_details.get('hints', ''),
            'repo_path': project_context['repo_path'],
            'base_branch': project_context['base_branch'],
            'test_command': project_context['test_command'],
            'agents_doc': project_context.get('agents_doc', ''),
            'trusted_authors_jq': _trusted_authors_jq(_trusted_authors_for_dispatch(self.config)),
            'trusted_authors_display': ", ".join(_trusted_authors_for_dispatch(self.config)),
            'prompt_file_path': f'/tmp/orchestrate-prompts/{agent_id}-{story_id}.txt',
            'custom_instructions': ''
        }

        for key, value in variables.items():
            template = template.replace('{' + key + '}', str(value))

        # Unescape literal braces in templates (used in JSON examples)
        # Templates use {{ }} to mean literal { } so they don't conflict with
        # {variable} substitution syntax above.
        template = template.replace('{{', '{').replace('}}', '}')

        return template

    def generate_bundle_prompt(self, story_ids: List[str], agent_id: str) -> str:
        """Generate a single prompt covering multiple stories serially."""
        project_context = self.adapter.get_project_context()

        # Build bundle list with details
        bundle_list_lines = []
        for i, sid in enumerate(story_ids, 1):
            details = self.adapter.get_story_details(sid)
            bundle_list_lines.append(
                f"  {i}. **{sid}**: {details['title']}\n"
                f"     File: {details['file_path']}"
            )
        bundle_list = '\n'.join(bundle_list_lines)

        bundle_id = _bundle_id(story_ids)

        # Load bundle template
        template_path = Path(__file__).parent.parent / 'templates' / 'dispatch-bundle.md'
        template = template_path.read_text()

        variables = {
            'agent_id': agent_id,
            'story_count': str(len(story_ids)),
            'bundle_list': bundle_list,
            'bundle_id': bundle_id,
            'bundle_ids_json': '[' + ','.join(f'"{s}"' for s in story_ids) + ']',
            'repo_path': project_context['repo_path'],
            'trusted_authors_jq': _trusted_authors_jq(_trusted_authors_for_dispatch(self.config)),
            'trusted_authors_display': ", ".join(_trusted_authors_for_dispatch(self.config)),
            # See dispatch-bundle.md Step 1.5 — agent copies this file
            # into its worktree so it can re-read the prompt after
            # context truncation.
            'prompt_file_path': f'/tmp/orchestrate-prompts/{agent_id}-{bundle_id}.txt',
        }

        for key, value in variables.items():
            template = template.replace('{' + key + '}', str(value))

        # Unescape JSON literals
        template = template.replace('{{', '{').replace('}}', '}')

        return template

    def dispatch_bundle(
        self,
        story_ids: List[str],
        *,
        agent_id_override: Optional[str] = None,
    ) -> Dict[str, str]:
        """Dispatch a bundle of stories to a single agent.

        Returns dict with single agent_id -> prompt mapping.
        """
        dispatch_cfg = self.config.get('dispatch', {})
        agent_prefix = _resolve_agent_prefix(dispatch_cfg)
        agent_sequence = dispatch_cfg.get(
            'agent_id_sequence', ['1', '2', '3', '4', '5', '6']
        )

        author_f = self.config.get('monitor', {}).get('author_filter', '') or ''
        claimed = gather_claimed_agent_ids_from_github(author_filter=author_f)

        if agent_id_override is not None:
            agent_id = agent_id_override.strip()
            if not agent_id:
                raise ValueError('--agent-id value is empty')
        else:
            agent_id = _allocate_agent_ids(
                prefix=agent_prefix,
                sequence=agent_sequence,
                claimed=claimed,
                needed=1,
            )[0]

        prompt = self.generate_bundle_prompt(story_ids, agent_id)

        self.active_agents[agent_id] = {
            'story_id': _bundle_id(story_ids),
            'bundle_stories': story_ids,
            'pr_number': None,
            'status': 'spawned',
        }

        return {agent_id: prompt}

    def dispatch_stories(
        self,
        story_ids: List[str],
        *,
        agent_id_override: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Generate dispatch prompts for stories.

        Note: Actual spawning is done by Claude Code via Agent tool.
        This method returns prompts for the user to spawn.

        Args:
            story_ids: List of story IDs to dispatch

        Returns:
            Dictionary mapping agent_id -> dispatch_prompt
        """
        dispatch_cfg = self.config.get('dispatch', {})
        agent_sequence = dispatch_cfg.get(
            'agent_id_sequence', ['1', '2', '3', '4', '5', '6']
        )
        agent_prefix = _resolve_agent_prefix(dispatch_cfg)

        max_i = min(len(story_ids), len(agent_sequence))
        if max_i < len(story_ids):
            print(
                f"Warning: only dispatching first {max_i} stories "
                f"(sequence length caps parallel agents)."
            )

        trimmed = story_ids[:max_i]
        author_f = self.config.get('monitor', {}).get('author_filter', '') or ''
        claimed = gather_claimed_agent_ids_from_github(author_filter=author_f)

        if agent_id_override is not None:
            if len(trimmed) != 1:
                raise ValueError(
                    '--agent-id is only supported for a bundle or a '
                    'single-story dispatch'
                )
            agent_ids = [agent_id_override.strip()]
            if not agent_ids[0]:
                raise ValueError('--agent-id value is empty')
        else:
            agent_ids = _allocate_agent_ids(
                prefix=agent_prefix,
                sequence=agent_sequence,
                claimed=claimed,
                needed=len(trimmed),
            )

        prompts = {}
        for agent_id, story_id in zip(agent_ids, trimmed):
            prompt = self.generate_dispatch_prompt(story_id, agent_id)
            prompts[agent_id] = prompt

            self.active_agents[agent_id] = {
                'story_id': story_id,
                'pr_number': None,
                'status': 'spawned'
            }

        return prompts

    def get_status_summary(self) -> str:
        """Generate human-readable status summary."""
        if not self.active_agents:
            return "No active agents."

        lines = ["Active agents:"]
        for agent_id, info in self.active_agents.items():
            story_id = info['story_id']
            status = info['status']
            pr_number = info.get('pr_number', 'N/A')
            lines.append(f"  {agent_id}: Story {story_id} | PR #{pr_number} | {status}")

        return '\n'.join(lines)
