"""
GitHub PR adapter - derives story status from GitHub PR state.

Status derivation order:
1. Matching PR exists → PR state determines status (merged=done,
   open+draft=in-progress, open+!draft=review)
2. No matching PR → Status: field in story file used as-is

PR matching priority:
1. Branch name (headRefName) matches `<agent>/story-<N.M>-<slug>` (AGENTS.md §2 convention)
2. ORCHESTRATE:CLAIM marker in PR body or comments (for bundle PRs)

Title-based matching was removed: docs/issue PRs naturally cite story IDs
in their titles (e.g. "docs(epic-9): detail Story 9.1-9.7 ACs") and got
falsely matched as implementation PRs.
"""

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set
from .base import StoryAdapter

# Match branch names like "claudeA/story-4-1-resize-spec" or "claudeB/story-3.3-reinstall"
_STORY_BRANCH_RE = re.compile(
    r'[/-]story[-.](\d+[-.]\d+[-a-z0-9._-]+)',
    re.IGNORECASE,
)


class GitHubPRAdapter(StoryAdapter):
    """Adapter that derives story status from GitHub PR state + file fallback."""

    def __init__(self, config: Dict):
        super().__init__(config)
        self.story_files_dir = config.get(
            'story_files_dir',
            '_bmad-output/implementation-artifacts',
        )
        self.repo_path = Path.cwd()
        self._pr_data = None          # cached PR query results
        self._story_files_cache = None  # cached file scan

    # ── Public API (StoryAdapter interface) ─────────────────────────────

    def list_ready_stories(self) -> List[str]:
        """Return story IDs with derived status 'ready-for-dev'."""
        statuses = self._get_all_story_statuses()
        return [sid for sid, st in statuses.items() if st == 'ready-for-dev']

    def get_epic_statistics(self) -> Dict[str, Dict]:
        """Get statistics for each epic."""
        statuses = self._get_all_story_statuses()
        epic_stats: Dict[str, Dict] = {}

        for story_id, status in statuses.items():
            epic_num = story_id.split('-')[0]
            name = f"Epic {epic_num}"

            if name not in epic_stats:
                epic_stats[name] = {
                    'total': 0, 'done': 0, 'in_progress': 0,
                    'review': 0, 'ready': 0, 'backlog': 0,
                }
            epic_stats[name]['total'] += 1
            if status == 'done':
                epic_stats[name]['done'] += 1
            elif status in ('in-progress', 'in_progress'):
                epic_stats[name]['in_progress'] += 1
            elif status == 'review':
                epic_stats[name]['review'] += 1
            elif status == 'ready-for-dev':
                epic_stats[name]['ready'] += 1
            elif status == 'backlog':
                epic_stats[name]['backlog'] += 1
            elif status == 'cancelled':
                epic_stats[name]['done'] += 1  # Count cancelled as done for progress

        return epic_stats

    def get_story_details(self, story_id: str) -> Dict:
        """Load story file and return details with derived status."""
        story_file = self._find_story_file(story_id)
        if not story_file:
            raise ValueError(f"Story file not found for {story_id}")

        content = story_file.read_text()

        title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else story_id

        hints = ''
        hints_match = re.search(
            r'## Story-Specific Hints\n\n(.+?)(?=\n##|\Z)',
            content, re.DOTALL,
        )
        if hints_match:
            hints = hints_match.group(1).strip()

        return {
            'title': title,
            'file_path': str(story_file),
            'hints': hints,
            'status': self._derive_story_status(story_id),
        }

    def update_story_status(self, story_id: str, status: str) -> None:
        """No-op: status is derived from GitHub PR state, not stored."""
        pass

    def get_project_context(self) -> Dict:
        """Return project-level context for dispatch prompts."""
        try:
            result = subprocess.run(
                ['git', 'config', '--get', 'init.defaultBranch'],
                capture_output=True, text=True, timeout=5,
            )
            base_branch = result.stdout.strip() if result.returncode == 0 else 'main'
            if not base_branch:
                result = subprocess.run(
                    ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                    capture_output=True, text=True, timeout=5,
                )
                base_branch = result.stdout.strip().split('/')[-1] if result.returncode == 0 else 'main'
        except Exception:
            base_branch = 'main'

        test_command = 'make test'
        makefile = self.repo_path / 'apps' / 'backend' / 'Makefile'
        if makefile.exists():
            test_command = 'cd apps/backend && make test'
        elif (self.repo_path / 'Makefile').exists():
            test_command = 'make test'

        agents_doc = ''
        if (self.repo_path / 'AGENTS.md').exists():
            agents_doc = 'AGENTS.md'

        return {
            'repo_path': str(self.repo_path),
            'base_branch': base_branch,
            'test_command': test_command,
            'agents_doc': agents_doc,
        }

    # ── Backward compat for resolve_story_ids ────────────────────────────

    def get_all_known_stories(self) -> List[str]:
        """Return all story IDs found in story files directory."""
        return list(self._scan_story_files().keys())

    # ── Internal: file scanning ──────────────────────────────────────────

    def _scan_story_files(self) -> Dict[str, Path]:
        """Scan story files directory and return {story_id: Path} mapping."""
        if self._story_files_cache is not None:
            return self._story_files_cache

        story_dir = self.repo_path / self.story_files_dir
        result: Dict[str, Path] = {}
        if story_dir.exists():
            for f in sorted(story_dir.iterdir()):
                if f.suffix != '.md':
                    continue
                stem = f.stem
                if re.match(r'^\d+-\d+[-a-z0-9]+$', stem, re.IGNORECASE):
                    result[stem] = f
        self._story_files_cache = result
        return result

    def _find_story_file(self, story_id: str) -> Optional[Path]:
        return self._scan_story_files().get(story_id)

    def _read_file_status(self, story_id: str) -> Optional[str]:
        """Read Status: from story file first line."""
        file_path = self._find_story_file(story_id)
        if not file_path:
            return None
        content = file_path.read_text()
        m = re.search(r'^Status:\s*(.+)$', content, re.MULTILINE)
        return m.group(1).strip() if m else None

    # ── Internal: GitHub PR querying ─────────────────────────────────────

    def _query_all_prs(self) -> List[Dict]:
        """Query GitHub for all PR states that affect story readiness."""
        if self._pr_data is not None:
            return self._pr_data

        all_prs: List[Dict] = []

        for state in ('open', 'merged', 'closed'):
            try:
                result = subprocess.run(
                    ['gh', 'pr', 'list', '--state', state, '--limit', '100',
                     '--json', 'number,title,headRefName,state,isDraft,author,body'],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0 and result.stdout.strip():
                    all_prs.extend(json.loads(result.stdout))
            except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
                pass

        # Fetch comments for PRs whose body lacks CLAIM marker. Bundle PRs use
        # branches like `<agent>/bundle-5.1-5.2`, so comments are the only
        # reliable source for their individual story IDs even after merge/close.
        # Uses ThreadPoolExecutor for concurrent gh calls.
        comment_targets = []
        for pr in all_prs:
            body = pr.get('body', '') or ''
            if 'ORCHESTRATE:CLAIM' in body:
                continue
            if pr.get('_comment_bodies'):
                continue
            comment_targets.append(pr)

        def fetch_comments(pr: Dict) -> None:
            try:
                num = pr['number']
                r = subprocess.run(
                    ['gh', 'pr', 'view', str(num), '--json', 'comments',
                     '--jq', '.comments[] | .body'],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0 and r.stdout.strip():
                    all_bodies = '\n'.join(
                        line for line in r.stdout.strip().split('\n') if line
                    )
                    pr['_comment_bodies'] = all_bodies
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(fetch_comments, comment_targets))

        self._pr_data = all_prs
        return all_prs

    # ── Internal: CLAIM marker parsing ───────────────────────────────────

    _CLAIM_RE = re.compile(
        r'<!--\s*ORCHESTRATE:CLAIM\s+(.*?)\s*-->',
        re.DOTALL,
    )

    def _parse_claim_markers(self, text: str):
        """Extract story IDs from ORCHESTRATE:CLAIM markers in text."""
        m = self._CLAIM_RE.search(text)
        if not m:
            return []
        attrs = m.group(1)
        stories_m = re.search(r'stories=\[(.*?)\]', attrs)
        if stories_m:
            raw = stories_m.group(1)
            return re.findall(r'"([^"]+)"', raw)
        single_m = re.search(r'story=(\S+)', attrs)
        if single_m:
            return [single_m.group(1).strip()]
        return []

    def _normalize_story_ref(self, raw: str) -> str:
        """Normalize a branch/title story reference to canonical story ID prefix.

        Converts '3.2-delete' or '3-2-delete' to '3-2' for prefix matching.
        Also handles 'story-3.3-reinstall' → '3-3'.
        """
        cleaned = raw.replace('.', '-')
        m = re.match(r'(\d+-\d+)', cleaned)
        return m.group(1) if m else cleaned

    def _match_pr_to_story(self, pr: Dict, known_ids: Set[str]) -> Optional[str]:
        """Match a PR to a known story ID via branch name convention.

        Only branch names matching `<agent>/story-<N.M>-<slug>` count as
        implementation PRs. Title and body references are too noisy
        (docs/issue PRs cite story IDs without implementing them).
        Bundle PRs without a story branch are caught later via CLAIM markers.
        """
        branch = pr.get('headRefName', '') or ''
        m = _STORY_BRANCH_RE.search(branch)
        if not m:
            return None
        prefix = self._normalize_story_ref(m.group(1))
        for sid in known_ids:
            if sid.startswith(prefix):
                return sid
        return None

    def _build_story_to_pr_map(self) -> Dict[str, Dict]:
            """Build {story_id: {number, state, is_draft}} mapping.
    
            Uses branch name matching + CLAIM marker parsing for bundle PRs.
            """
            known_ids = set(self._scan_story_files().keys())
            prs = self._query_all_prs()
    
            def _sid_entry(pr: Dict) -> Dict:
                return {
                    'number': pr['number'],
                    'state': pr.get('state', 'OPEN').upper(),
                    'is_draft': pr.get('isDraft', False),
                }
    
            story_to_pr: Dict[str, Dict] = {}
            for pr in prs:
                # Strategy A: branch name matching
                matched = self._match_pr_to_story(pr, known_ids)
                if matched and matched not in story_to_pr:
                    story_to_pr[matched] = _sid_entry(pr)
    
                # Strategy B: CLAIM markers in PR body + comments
                claim_text = (pr.get('body', '') or '')
                comments = pr.get('_comment_bodies', '')
                if comments:
                    claim_text += '\n' + comments
    
                claimed = self._parse_claim_markers(claim_text)
                for sid in claimed:
                    if sid in known_ids and sid not in story_to_pr:
                        story_to_pr[sid] = _sid_entry(pr)
    
            # Strategy C: merged PRs override open PR claims.
            # An open bundle PR may claim a story that a merged individual PR
            # already implemented. Merged status is the authoritative source.
            for pr in prs:
                if pr.get('state') != 'MERGED':
                    continue
                matched = self._match_pr_to_story(pr, known_ids)
                if matched and matched in story_to_pr:
                    story_to_pr[matched] = _sid_entry(pr)
                claim_text = (pr.get('body', '') or '')
                comments = pr.get('_comment_bodies', '')
                if comments:
                    claim_text += '\n' + comments
                claimed = self._parse_claim_markers(claim_text)
                for sid in claimed:
                    if sid in known_ids:
                        story_to_pr[sid] = _sid_entry(pr)
    
            return story_to_pr

    def _derive_story_status(self, story_id: str) -> str:
        """Derive status from PR state, or fall back to file Status: field.

        CLOSED-not-merged PRs are historical noise (story restarted under a
        new branch, scope cancelled, etc). They must not override an
        authoritative file Status — especially `cancelled`, which encodes an
        ADR-level decision the closed PR pre-dates.
        """
        pr_map = self._build_story_to_pr_map()
        info = pr_map.get(story_id)

        if info:
            s = info.get('state', 'OPEN')
            if s == 'MERGED':
                return 'done'
            if s == 'CLOSED':
                file_status = self._read_file_status(story_id)
                if file_status in ('cancelled', 'done'):
                    return file_status
                return 'closed'
            # OPEN
            return 'in-progress' if info.get('is_draft') else 'review'

        # No PR → fall back to file Status: field
        return self._read_file_status(story_id) or 'unknown'

    def _get_all_story_statuses(self) -> Dict[str, str]:
        """Derive status for every known story in one pass (single gh query)."""
        story_ids = list(self._scan_story_files().keys())
        pr_map = self._build_story_to_pr_map()

        statuses: Dict[str, str] = {}
        for sid in story_ids:
            info = pr_map.get(sid)
            if info:
                s = info.get('state', 'OPEN')
                if s == 'MERGED':
                    statuses[sid] = 'done'
                elif s == 'CLOSED':
                    file_status = self._read_file_status(sid)
                    if file_status in ('cancelled', 'done'):
                        statuses[sid] = file_status
                    else:
                        statuses[sid] = 'closed'
                else:
                    statuses[sid] = 'in-progress' if info['is_draft'] else 'review'
            else:
                statuses[sid] = self._read_file_status(sid) or 'unknown'
        return statuses

    # ── Backward compat for legacy callers ───────────────────────────────

    def _load_status_file(self) -> Dict:
        """Return a sprint-status.yaml compatible structure for legacy code."""
        statuses = self._get_all_story_statuses()
        return {
            'development_status': {sid: st for sid, st in statuses.items()},
        }
