"""
GitHub PR comment channel for orchestrate communication.

All orchestrator ↔ dev-agent messaging flows through PR comments using
structured markers:

  <!-- ORCHESTRATE:CLAIM agent=claudeA story=4.2 -->
  <!-- ORCHESTRATE:DECISION agent=claudeA story=4.2 timestamp=... -->
  <!-- ORCHESTRATE:RESPONSE agent=claudeA story=4.2 -->
  <!-- ORCHESTRATE:COMPLETION agent=claudeA story=4.2 -->

JSON payload follows the marker in a ```json code block.
"""

import json
import re
import subprocess
from datetime import datetime
from typing import Dict, List, Optional


MARKER_PATTERN = re.compile(
    r'<!--\s*ORCHESTRATE:(?P<type>\w+)\s+(?P<attrs>[^>]*?)\s*-->\s*\n',
    re.MULTILINE
)
ATTR_PATTERN = re.compile(r'(\w+)=([^\s]+)')
JSON_BLOCK_PATTERN = re.compile(r'```json\s*\n(.*?)\n```', re.DOTALL)


def _story_attr(story) -> str:
    """Render the story slot of a marker.

    Single story → ``story=<id>``. Bundle (list) → ``stories=[...]`` as
    compact JSON (no spaces) so ATTR_PATTERN's ``[^\\s]+`` value match
    captures the full array.
    """
    if isinstance(story, (list, tuple)):
        return f"stories={json.dumps(list(story), separators=(',', ':'))}"
    return f"story={story}"


class GitHubChannel:
    """Read/write orchestrate messages via GitHub PR comments."""

    def __init__(self, config: Dict):
        self.config = config
        self._trusted_authors = None  # lazy

    # ---------- Trust ----------

    @property
    def trusted_authors(self) -> set:
        """GitHub logins whose ORCHESTRATE:RESPONSE markers are honored.

        Resolved once per process via the shared `resolve_trusted_authors`
        helper so the channel and dispatched sub-agents agree. Empty set
        means no auto-trust (every RESPONSE is ignored — fail-closed).
        """
        if self._trusted_authors is None:
            from orchestrator import resolve_trusted_authors
            self._trusted_authors = set(resolve_trusted_authors(self.config))
        return self._trusted_authors

    def is_trusted(self, login: str) -> bool:
        return bool(login) and login in self.trusted_authors

    # ---------- PR discovery ----------

    def list_active_prs(self) -> List[Dict]:
        """List open PRs with headRefName and title.

        Honors `[monitor] author_filter` so two devs running --monitor in
        parallel each only see their own PRs. Empty/unset filter = no scoping.
        """
        cmd = ['gh', 'pr', 'list', '--state', 'open', '--json',
               'number,title,headRefName,isDraft,author']
        author = self.config.get('monitor', {}).get('author_filter', '').strip()
        if author:
            cmd += ['--author', author]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return []
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            return []

    def get_pr_comments(self, pr_number: int) -> List[Dict]:
        """Fetch all comments on a PR (issue comments, not review comments)."""
        try:
            result = subprocess.run(
                ['gh', 'pr', 'view', str(pr_number), '--json', 'comments'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return []
            data = json.loads(result.stdout)
            return data.get('comments', [])
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            return []

    # ---------- Marker parsing ----------

    def parse_marker(self, body: str) -> Optional[Dict]:
        """Extract ORCHESTRATE marker and JSON payload from a comment body.

        Returns dict with: type, attrs (agent, story, timestamp...), payload

        Supports both single story and bundle stories:
        - story=X.Y → single story
        - stories=["X.Y","Z.W"] → bundle (JSON array)
        """
        marker_match = MARKER_PATTERN.search(body)
        if not marker_match:
            return None

        msg_type = marker_match.group('type')
        attrs_str = marker_match.group('attrs')
        attrs = dict(ATTR_PATTERN.findall(attrs_str))

        # Extract JSON payload if present
        payload = {}
        json_match = JSON_BLOCK_PATTERN.search(body)
        if json_match:
            try:
                payload = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Handle story= (single), stories= (bundle), and issue= (direct-fix).
        # Issue claims ride the same `story` slot as `issue-<N>` so downstream
        # tracking (PR state, CI events) works unchanged. The one consumer that
        # cannot take an issue ref — adapter.update_story_status — is guarded
        # in pr_monitor.process_pr.
        story_id = attrs.get('story')
        stories_raw = attrs.get('stories')
        issue_raw = attrs.get('issue')

        if stories_raw:
            # Parse JSON array: stories=["6-1-template-query-api","6-2-template-lifecycle-and-gc"]
            try:
                stories = json.loads(stories_raw)
                if isinstance(stories, list) and stories:
                    story_id = stories  # Return list for bundle
            except json.JSONDecodeError:
                pass  # Fall back to story_id from story= if present
        elif issue_raw and not story_id:
            n = issue_raw.lstrip('#')
            if n.isdigit():
                story_id = f"issue-{n}"

        return {
            'type': msg_type,
            'attrs': attrs,
            'payload': payload,
            'agent': attrs.get('agent'),
            'story': story_id,  # str (single story / "issue-N") or list (bundle)
            'timestamp': attrs.get('timestamp', ''),
        }

    def extract_messages(self, comments: List[Dict], msg_type: str = None) -> List[Dict]:
        """Parse markers from a list of PR comments.

        Returns parsed messages, optionally filtered by type.
        Includes comment id and createdAt for ordering.
        """
        messages = []
        for comment in comments:
            body = comment.get('body', '')
            parsed = self.parse_marker(body)
            if not parsed:
                continue
            if msg_type and parsed['type'] != msg_type:
                continue

            parsed['comment_id'] = comment.get('id', '')
            parsed['comment_url'] = comment.get('url', '')
            parsed['created_at'] = comment.get('createdAt', '')
            parsed['author_login'] = (comment.get('author') or {}).get('login', '')
            messages.append(parsed)

        return messages

    # ---------- Write operations ----------

    # ---------- Label operations ----------

    def pr_has_label(self, pr_number: int, label: str) -> bool:
        """Check if a PR has a specific label."""
        try:
            result = subprocess.run(
                ['gh', 'pr', 'view', str(pr_number), '--json', 'labels'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return False
            data = json.loads(result.stdout)
            labels = [l.get('name', '') for l in data.get('labels', [])]
            return label in labels
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            return False

    def remove_pr_label(self, pr_number: int, label: str) -> bool:
        """Remove a label from a PR."""
        try:
            result = subprocess.run(
                ['gh', 'pr', 'edit', str(pr_number), '--remove-label', label],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def add_pr_label(self, pr_number: int, label: str) -> bool:
        """Add a label to a PR. Creates the label if it doesn't exist."""
        try:
            # Try adding the label (works if it exists)
            result = subprocess.run(
                ['gh', 'pr', 'edit', str(pr_number), '--add-label', label],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True

            # If label doesn't exist, create it then retry
            if 'not found' in result.stderr.lower() or 'could not add' in result.stderr.lower():
                # Create the label first
                subprocess.run(
                    ['gh', 'label', 'create', label,
                     '--description', f'Orchestrate skill: {label}',
                     '--color', '0E8A16'],
                    capture_output=True, text=True, timeout=10
                )
                # Retry add
                result = subprocess.run(
                    ['gh', 'pr', 'edit', str(pr_number), '--add-label', label],
                    capture_output=True, text=True, timeout=10
                )
                return result.returncode == 0
            return False
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ---------- Comment operations ----------

    def post_comment(self, pr_number: int, body: str) -> bool:
        """Post a comment on a PR via gh CLI."""
        try:
            result = subprocess.run(
                ['gh', 'pr', 'comment', str(pr_number), '--body', body],
                capture_output=True, text=True, timeout=15
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def write_claim(self, pr_number: int, agent: str, story: str) -> bool:
        """Write a CLAIM marker (orchestrate associates this PR with an agent/story).

        ``story`` of the form ``issue-<N>`` (or ``#<N>``) is rewritten to use
        the ``issue=`` attribute so the parser routes it through the
        direct-fix path.
        """
        ref = story.lstrip('#')
        if ref.startswith('issue-') and ref[6:].isdigit():
            n = ref[6:]
            body = (
                f"<!-- ORCHESTRATE:CLAIM agent={agent} issue={n} -->\n"
                f"PR claimed by orchestrate skill: agent `{agent}` "
                f"working on issue `#{n}`."
            )
        else:
            body = (
                f"<!-- ORCHESTRATE:CLAIM agent={agent} story={story} -->\n"
                f"PR claimed by orchestrate skill: agent `{agent}` "
                f"working on story `{story}`."
            )
        return self.post_comment(pr_number, body)

    def write_decision(self, pr_number: int, agent: str, story,
                       question: str, options: List[str],
                       context: str = "") -> bool:
        """Write a DECISION marker (dev agent asks for orchestrator input).

        ``story`` accepts a single ID (str) or a bundle (list/tuple of IDs).
        """
        timestamp = datetime.utcnow().isoformat() + 'Z'
        payload = {
            'question': question,
            'options': options,
            'context': context,
        }
        body = (
            f"<!-- ORCHESTRATE:DECISION agent={agent} {_story_attr(story)} timestamp={timestamp} -->\n\n"
            f"**Question**: {question}\n\n"
            f"**Options**: {', '.join(options)}\n\n"
            f"**Context**: {context}\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```"
        )
        return self.post_comment(pr_number, body)

    def write_response(self, pr_number: int, agent: str, story,
                       decision: str, reason: str,
                       auto_decided: bool = True,
                       rule_id: str = "") -> bool:
        """Write a RESPONSE marker (orchestrator answers a decision).

        ``story`` accepts a single ID (str) or a bundle (list/tuple of IDs).
        """
        timestamp = datetime.utcnow().isoformat() + 'Z'
        payload = {
            'decision': decision,
            'reason': reason,
            'auto_decided': auto_decided,
            'rule_id': rule_id,
        }
        body = (
            f"<!-- ORCHESTRATE:RESPONSE agent={agent} {_story_attr(story)} timestamp={timestamp} -->\n\n"
            f"**Decision**: {decision}\n\n"
            f"**Reason**: {reason}\n\n"
            f"_{'Auto-decided' if auto_decided else 'Manual'} via rule `{rule_id}`_\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```"
        )
        return self.post_comment(pr_number, body)

    def write_approval(self, pr_number: int, agent: str, story: str,
                       status: str = 'approved', message: str = '') -> bool:
        """Write an APPROVAL marker (orchestrator approves merge)."""
        timestamp = datetime.utcnow().isoformat() + 'Z'
        payload = {'status': status, 'message': message}
        body = (
            f"<!-- ORCHESTRATE:APPROVAL agent={agent} story={story} timestamp={timestamp} status={status} -->\n\n"
            f"## ✅ 最终审核通过\n\n"
            f"{message}\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```"
        )
        return self.post_comment(pr_number, body)

    def get_approval(self, pr_number: int) -> Optional[Dict]:
        """Get latest APPROVAL marker on a PR, if any (from trusted authors only)."""
        comments = self.get_pr_comments(pr_number)
        approvals = self.extract_messages(comments, msg_type='APPROVAL')
        trusted = [a for a in approvals if self.is_trusted(a.get('author_login', ''))]
        if not trusted:
            return None
        return max(trusted, key=lambda c: c['created_at'])

    def write_completion(self, pr_number: int, agent: str, story,
                         verdict: str, coverage: Dict,
                         deferred_count: int = 0) -> bool:
        """Write a COMPLETION marker (dev agent reports finished work).

        ``story`` accepts a single ID (str) or a bundle (list/tuple of IDs).
        """
        timestamp = datetime.utcnow().isoformat() + 'Z'
        payload = {
            'verdict': verdict,
            'coverage': coverage,
            'deferred_count': deferred_count,
        }
        cov_str = ' / '.join(f"{k}: {v}%" for k, v in coverage.items())
        body = (
            f"<!-- ORCHESTRATE:COMPLETION agent={agent} {_story_attr(story)} timestamp={timestamp} -->\n\n"
            f"**Verdict**: {verdict}\n"
            f"**Coverage**: {cov_str}\n"
            f"**Deferred items**: {deferred_count}\n\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```"
        )
        return self.post_comment(pr_number, body)

    # ---------- High-level queries ----------

    def discover_active_agents(self) -> List[Dict]:
        """Find all PRs with CLAIM markers, return agent → PR mapping.

        Returns list of {pr_number, pr_url, agent, story, title}.
        """
        active = []
        prs = self.list_active_prs()

        for pr in prs:
            comments = self.get_pr_comments(pr['number'])
            claims = self.extract_messages(comments, msg_type='CLAIM')
            if not claims:
                continue
            # Use first CLAIM (earliest)
            claim = claims[0]
            active.append({
                'pr_number': pr['number'],
                'pr_url': f"https://github.com/Hermerz/vm-control-plane/pull/{pr['number']}",
                'agent_id': claim['agent'],
                'story_id': claim['story'],
                'pr_title': pr['title'],
                'is_draft': pr.get('isDraft', False),
            })

        return active

    def get_pending_decisions(self, pr_number: int) -> List[Dict]:
        """Get DECISION messages without matching RESPONSE on a PR."""
        comments = self.get_pr_comments(pr_number)
        decisions = self.extract_messages(comments, msg_type='DECISION')
        # Only RESPONSE markers from trusted authors count as "answered" —
        # otherwise an attacker who can comment on the PR could post a fake
        # RESPONSE and trick the orchestrator into skipping a real decision.
        responses = [
            r for r in self.extract_messages(comments, msg_type='RESPONSE')
            if self.is_trusted(r.get('author_login', ''))
        ]

        # Match decisions to responses by (agent, story, timestamp ordering)
        response_keys = set()
        for resp in responses:
            story = resp['story']
            # Convert list to tuple for hashability (bundle case)
            if isinstance(story, list):
                story = tuple(story)
            response_keys.add((resp['agent'], story))

        pending = []
        for dec in decisions:
            dec_story = dec['story']
            if isinstance(dec_story, list):
                dec_story = tuple(dec_story)
            # Check if any response came AFTER this decision
            # RESPONSE can be from any agent (typically orchestrator), so only match on story + timestamp
            has_response = False
            for resp in responses:
                resp_story = resp['story']
                if isinstance(resp_story, list):
                    resp_story = tuple(resp_story)
                if (resp_story == dec_story and
                    resp['created_at'] > dec['created_at']):
                    has_response = True
                    break
            if not has_response:
                dec['pr_number'] = pr_number
                pending.append(dec)

        return pending

    def get_completion(self, pr_number: int) -> Optional[Dict]:
        """Get latest COMPLETION message on a PR, if any."""
        comments = self.get_pr_comments(pr_number)
        completions = self.extract_messages(comments, msg_type='COMPLETION')
        if not completions:
            return None
        # Return latest
        return max(completions, key=lambda c: c['created_at'])
