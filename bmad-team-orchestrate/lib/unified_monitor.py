"""
Unified monitor combining decision queue and PR status monitoring.

Runs both monitors in a single loop to avoid spawning two processes.

Note: code review is no longer triggered by the monitor. Dev agents run
`/cross-model-review` themselves before posting COMPLETION (see
.claude/skills/cross-model-review/). The monitor only handles DECISION
escalation and PR status sync.
"""

import time
from pathlib import Path
from typing import Dict, Optional, Callable
from datetime import datetime

from decision_monitor import DecisionMonitor
from pr_monitor import PRMonitor


class UnifiedMonitor:
    """Combined decision queue + PR status monitor."""

    def __init__(self, config: Dict, adapter, decision_rules_path: Path):
        self.config = config
        self.decision_monitor = DecisionMonitor(config, decision_rules_path)
        self.pr_monitor = PRMonitor(config, adapter)
        self.required_checks = config.get('github', {}).get('required_checks', [])

        # Use shorter of the two intervals
        decision_interval = config['orchestrator'].get('poll_interval_seconds', 60)
        pr_interval = config['orchestrator'].get('pr_poll_interval_seconds', 120)
        self.poll_interval = min(decision_interval, pr_interval)

        # Track last PR check to respect pr_poll_interval
        self.last_pr_check = 0
        self.pr_check_interval = pr_interval

        # Map PR number → last announced COMPLETION timestamp. Keying on
        # the timestamp (not just the PR) means: if the orchestrator
        # withholds APPROVAL and the agent pushes fixups + posts a newer
        # COMPLETION, that fresh completion re-triggers approval_needed
        # instead of being suppressed by a stale cache entry.
        self._announced_approval: Dict[int, str] = {}

    def run(self, escalation_callback: Optional[Callable] = None,
            event_callback: Optional[Callable] = None):
        """Run unified monitoring loop.

        Args:
            escalation_callback: Called when decisions need user input
            event_callback: Called when PR events occur
        """
        from orchestrator import resolve_trusted_authors
        trusted = resolve_trusted_authors(self.config)
        trusted_str = ", ".join(trusted) if trusted else "(none)"

        author = self.config.get('monitor', {}).get('author_filter', '').strip()
        scope_str = f"author={author}" if author else "scope=all-authors"

        print(
            f"🎯 Monitor: poll {self.poll_interval}s · PR {self.pr_check_interval}s · "
            f"{scope_str} · trusted {trusted_str} · Ctrl+C to stop",
            flush=True,
        )

        try:
            while True:
                escalations = self.decision_monitor.process_decisions()
                for decision in escalations:
                    self._emit_escalation(decision)
                if escalations and escalation_callback:
                    escalation_callback(escalations)

                now = time.time()
                if now - self.last_pr_check >= self.pr_check_interval:
                    events = self.pr_monitor.check_all_prs()
                    for event in events:
                        self._emit(self.pr_monitor.format_event(event))
                    if events and event_callback:
                        event_callback(events)

                    approval_events = self._check_approval_needed()
                    for event in approval_events:
                        self._emit(self._format_approval_event(event))
                    if approval_events and event_callback:
                        event_callback(approval_events)

                    self.last_pr_check = now

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\n⏹  stopped", flush=True)

    def _emit(self, msg: str):
        """One-line event with HH:MM:SS prefix. Multi-line messages are
        emitted verbatim under the timestamp (used by review_manual)."""
        ts = datetime.now().strftime('%H:%M:%S')
        first, *rest = msg.split('\n')
        print(f"[{ts}] {first}", flush=True)
        for line in rest:
            print(line, flush=True)

    def _check_approval_needed(self) -> list:
        """Emit one approval_needed event per PR that has reached
        COMPLETION + green CI but no APPROVAL yet. Without this signal
        the dev agent's polling loop (waiting for ORCHESTRATE:APPROVAL)
        would block indefinitely.

        CI-green is judged against `[github].required_checks` if set;
        otherwise every non-skipped check must be SUCCESS.
        """
        events = []
        active_prs = self.pr_monitor.discover_active_prs()

        for pr_info in active_prs:
            pr_number = pr_info['pr_number']
            if self.pr_monitor.channel.get_approval(pr_number):
                continue
            completion = self.pr_monitor.channel.get_completion(pr_number)
            if not completion:
                continue
            comp_ts = completion.get('timestamp', '')
            if self._announced_approval.get(pr_number) == comp_ts:
                continue
            # Don't unblock approval while an escalation is still open —
            # an unanswered DECISION means the orchestrator hasn't ruled
            # on a residual issue, so APPROVAL would jump the queue.
            if self.pr_monitor.channel.get_pending_decisions(pr_number):
                continue
            pr_status = self.pr_monitor.get_pr_status(pr_number)
            if not pr_status or pr_status.get('is_draft'):
                continue
            if not self._ci_green(pr_status):
                continue

            self._announced_approval[pr_number] = comp_ts
            events.append({
                'type': 'approval_needed',
                'pr_number': pr_number,
                'story_id': pr_info['story_id'],
                'agent_id': pr_info['agent_id'],
            })
        return events

    def _ci_green(self, pr_status: Dict) -> bool:
        """If required_checks configured, every required check must be
        SUCCESS. Otherwise fall back to 'every non-skipped check is
        SUCCESS' (preserves prior behavior for unconfigured projects)."""
        checks = pr_status.get('checks') or []
        if self.required_checks:
            results = {c.get('name'): c.get('conclusion') for c in checks}
            return all(results.get(name) == 'SUCCESS'
                       for name in self.required_checks)
        non_skipped = [c.get('conclusion') for c in checks
                       if c.get('conclusion') != 'SKIPPED']
        return bool(non_skipped) and all(c == 'SUCCESS' for c in non_skipped)

    def _format_approval_event(self, event: Dict) -> str:
        story = event.get('story_id')
        if isinstance(story, list):
            story = ", ".join(story)
        return (f"🟢 PR #{event['pr_number']} ({story}, {event['agent_id']}): "
                f"completion + CI green — post ORCHESTRATE:APPROVAL")

    def _emit_escalation(self, decision: Dict):
        """Single-line escalation: agent, story, question, options."""
        ts = datetime.now().strftime('%H:%M:%S')
        opts = ", ".join(decision.get('options', []))
        opt_str = f" [{opts}]" if opts else ""
        q = (decision.get('question') or '').replace('\n', ' ').strip()
        if len(q) > 80:
            q = q[:77] + "..."
        print(
            f"[{ts}] ⚠️  {decision['agent']}({decision['story']}) "
            f"asks: {q}{opt_str} — reply on PR",
            flush=True,
        )

