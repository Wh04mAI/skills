"""
PR status monitor for orchestrate skill.

Monitors active PRs and syncs story status:
- PR draft → in-progress
- PR ready (not draft) → review
- PR merged → done

Discovers active PRs via ORCHESTRATE:CLAIM markers in PR comments.
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Callable
from datetime import datetime

from github_channel import GitHubChannel


class PRMonitor:
    """Monitor PR status and sync story status."""

    def __init__(self, config: Dict, adapter):
        self.config = config
        self.adapter = adapter
        self.channel = GitHubChannel(config)
        self.poll_interval = config['orchestrator'].get('pr_poll_interval_seconds', 120)
        self.required_checks = config.get('github', {}).get('required_checks', [])

        # In-memory transition cache for the lifetime of this monitor process.
        # On restart, the first poll re-emits current state as if everything just
        # transitioned — that's the desired resume behavior, not a regression.
        self.pr_states: Dict[int, Dict] = {}

    def discover_active_prs(self) -> List[Dict]:
        """Discover active agent PRs via ORCHESTRATE:CLAIM markers in PR comments.

        Returns list of {agent_id, story_id, pr_number, pr_url}.
        """
        return self.channel.discover_active_agents()

    def get_pr_status(self, pr_number: int) -> Optional[Dict]:
        """Query gh CLI for PR status.

        Returns:
            {state, is_draft, mergeable, checks: [...]} or None on error
        """
        try:
            result = subprocess.run(
                ['gh', 'pr', 'view', str(pr_number), '--json',
                 'state,isDraft,mergeable,statusCheckRollup'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return None

            data = json.loads(result.stdout)

            checks = []
            for check in data.get('statusCheckRollup', []):
                checks.append({
                    'name': check.get('name'),
                    'status': check.get('status'),
                    'conclusion': check.get('conclusion')
                })

            return {
                'state': data.get('state'),
                'is_draft': data.get('isDraft', False),
                'mergeable': data.get('mergeable'),
                'checks': checks
            }

        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            return None

    def all_required_checks_passed(self, pr_status: Dict) -> bool:
        """Check if all required CI checks passed."""
        if not pr_status or not pr_status.get('checks'):
            return False

        check_results = {}
        for check in pr_status['checks']:
            name = check.get('name')
            conclusion = check.get('conclusion')
            # Keep latest result if multiple runs
            check_results[name] = conclusion

        for required in self.required_checks:
            if check_results.get(required) != 'SUCCESS':
                return False

        return True

    def has_failed_check(self, pr_status: Dict) -> bool:
        """Check if any CI check failed."""
        if not pr_status or not pr_status.get('checks'):
            return False

        for check in pr_status['checks']:
            if check.get('conclusion') == 'FAILURE':
                return True

        return False

    def determine_story_status(self, pr_status: Dict) -> str:
        """Map PR status to story status."""
        state = pr_status.get('state', '').upper()

        if state == 'MERGED':
            return 'done'
        elif state == 'CLOSED':
            return 'closed'  # Cancelled
        elif state == 'OPEN':
            if pr_status.get('is_draft'):
                return 'in-progress'
            else:
                return 'review'

        return 'unknown'

    def process_pr(self, pr_info: Dict) -> Dict:
        """Process a single PR and update story status if needed.

        Supports both single story and bundle (story_id can be str or list).

        Returns event dict describing what happened.
        """
        pr_number = pr_info['pr_number']
        story_id = pr_info['story_id']
        agent_id = pr_info['agent_id']

        # Normalize story_id to list for uniform processing
        story_ids = story_id if isinstance(story_id, list) else [story_id]

        pr_status = self.get_pr_status(pr_number)

        if not pr_status:
            return {
                'type': 'error',
                'pr_number': pr_number,
                'story_id': story_id,
                'message': f"Failed to get PR status"
            }

        new_story_status = self.determine_story_status(pr_status)
        last_state = self.pr_states.get(pr_number, {})

        events = []

        # Detect status change - emit event for each story.
        # Status is derived from GitHub PR state, no YAML file to update.
        if last_state.get('story_status') != new_story_status:
            for sid in story_ids:
                events.append({
                    'type': 'status_change',
                    'pr_number': pr_number,
                    'story_id': sid,
                    'agent_id': agent_id,
                    'old_status': last_state.get('story_status', 'unknown'),
                    'new_status': new_story_status,
                })

        # Detect CI failure (new failure since last check)
        if self.has_failed_check(pr_status) and not last_state.get('ci_failed'):
            events.append({
                'type': 'ci_failed',
                'pr_number': pr_number,
                'story_id': story_id,
                'agent_id': agent_id,
            })

        last_state['story_status'] = new_story_status
        last_state['ci_failed'] = self.has_failed_check(pr_status)
        self.pr_states[pr_number] = last_state

        return events if events else None

    def check_all_prs(self) -> List[Dict]:
        """Check all active PRs and return list of events."""
        active_prs = self.discover_active_prs()
        all_events = []

        for pr_info in active_prs:
            events = self.process_pr(pr_info)
            if events:
                if isinstance(events, list):
                    all_events.extend(events)
                else:
                    all_events.append(events)

        return all_events

    def format_event(self, event: Dict) -> str:
        """Format event for human display."""
        event_type = event.get('type')
        pr = event.get('pr_number')
        story = event.get('story_id')
        # Render issue refs as `issue #N` so the log distinguishes a
        # direct-fix PR from a story PR at a glance.
        if isinstance(story, str) and story.startswith('issue-'):
            story = f"issue #{story[6:]}"

        if event_type == 'status_change':
            old = event['old_status']
            new = event['new_status']
            emoji = {
                'in-progress': '🔨',
                'review': '👀',
                'done': '✅',
                'closed': '❌'
            }.get(new, '📝')
            return f"{emoji} PR #{pr} ({story}): {old} → {new}"
        elif event_type == 'ci_failed':
            return f"🔥 PR #{pr} ({story}): CI failed - dev agent should fix"
        elif event_type == 'error':
            return f"❌ PR #{pr} ({story}): {event.get('message')}"
        else:
            return f"PR #{pr} ({story}): {event_type}"

    def monitor_loop(self, callback: Optional[Callable] = None):
        """Run monitoring loop.

        Args:
            callback: Optional function called with events list.
        """
        print(f"\n📡 Monitoring PRs (poll interval: {self.poll_interval}s)")
        print(f"   Required checks: {', '.join(self.required_checks)}")
        print("   Press Ctrl+C to stop\n")

        try:
            while True:
                events = self.check_all_prs()

                if events:
                    timestamp = datetime.now().strftime('%H:%M:%S')
                    print(f"[{timestamp}]")
                    for event in events:
                        print(f"  {self.format_event(event)}")
                    print()

                    if callback:
                        callback(events)

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\n\n⏹  PR monitoring stopped")


def main():
    """Standalone PR monitor entry point."""
    import sys
    import tomllib

    skill_root = Path(__file__).parent.parent
    config_path = skill_root / "customize.toml"

    with open(config_path, 'rb') as f:
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

    sys.path.insert(0, str(skill_root / "lib"))
    from adapters.github_pr_adapter import GitHubPRAdapter
    from adapters.file_scanner import FileScannerAdapter

    adapter_type = config['adapter']['type']
    if adapter_type == 'github_pr':
        adapter = GitHubPRAdapter(config['adapter']['github_pr'])
    elif adapter_type == 'file_scanner':
        adapter = FileScannerAdapter(config['adapter']['file_scanner'])

    monitor = PRMonitor(config, adapter)
    monitor.monitor_loop()


if __name__ == '__main__':
    main()
