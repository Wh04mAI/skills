"""
Decision queue monitor for orchestrate skill (GitHub PR comments).

Polls all active PRs for ORCHESTRATE:DECISION markers, auto-decides
based on rules, or escalates to user. Responses are written back as
ORCHESTRATE:RESPONSE comments on the same PR.
"""

import re
import time
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from github_channel import GitHubChannel


class DecisionMonitor:
    """Monitor and process agent decisions from GitHub PR comments."""

    def __init__(self, config: Dict, decision_rules_path: Path):
        self.config = config
        self.poll_interval = config['orchestrator'].get('poll_interval_seconds', 60)
        self.channel = GitHubChannel(config)

        # Load decision rules
        with open(decision_rules_path, 'r') as f:
            rules_config = yaml.safe_load(f)
            self.rules = rules_config.get('rules', [])
            self.escalate_patterns = rules_config.get('escalate', [])

    def get_pending_decisions(self) -> List[Dict]:
        """Get all unanswered decisions across active PRs."""
        active = self.channel.discover_active_agents()
        all_pending = []

        for pr_info in active:
            pending = self.channel.get_pending_decisions(pr_info['pr_number'])
            for dec in pending:
                # Enrich with payload data
                payload = dec.get('payload', {})
                all_pending.append({
                    'agent': dec['agent'],
                    'story': dec['story'],
                    'pr_number': pr_info['pr_number'],
                    'question': payload.get('question', ''),
                    'options': payload.get('options', []),
                    'context': payload.get('context', ''),
                    'created_at': dec['created_at'],
                    'comment_url': dec.get('comment_url', ''),
                })

        return all_pending

    def match_rule(self, decision: Dict) -> Optional[Dict]:
        """Match decision against auto-decision rules."""
        question = decision.get('question', '').lower()
        context = decision.get('context', '').lower()
        combined = f"{question} {context}"

        # Check escalate patterns first
        for escalate_rule in self.escalate_patterns:
            pattern = escalate_rule.get('pattern', '').lower()
            if any(p in combined for p in pattern.split('|')):
                return None  # Escalate

        # Check auto-decision rules
        for rule in self.rules:
            pattern = rule.get('pattern', '').lower()
            if any(p in combined for p in pattern.split('|')):
                return rule

        return None

    def auto_decide(self, decision: Dict) -> Optional[Dict]:
        """Attempt to auto-decide based on rules."""
        rule = self.match_rule(decision)
        if not rule:
            return None

        action = rule.get('action')
        reason = rule.get('reason', 'Auto-decided by pattern match')
        options = decision.get('options', [])

        # Map action to option
        chosen = None
        if action in options:
            chosen = action
        elif action == 'defer' and any('defer' in o.lower() for o in options):
            chosen = next(o for o in options if 'defer' in o.lower())
        elif action == 'apply_patch' and any(
            'apply' in o.lower() or 'fix' in o.lower() for o in options
        ):
            chosen = next(o for o in options if 'apply' in o.lower() or 'fix' in o.lower())

        if not chosen:
            return None

        return {
            'option': chosen,
            'reason': reason,
            'rule_id': rule.get('id', 'unknown'),
        }

    def process_decisions(self) -> List[Dict]:
        """Process pending decisions. Returns list of escalations."""
        pending = self.get_pending_decisions()
        if not pending:
            return []

        escalations = []

        for decision in pending:
            response = self.auto_decide(decision)

            if response:
                # Auto-decided: write RESPONSE to PR
                success = self.channel.write_response(
                    pr_number=decision['pr_number'],
                    agent=decision['agent'],
                    story=decision['story'],
                    decision=response['option'],
                    reason=response['reason'],
                    auto_decided=True,
                    rule_id=response['rule_id'],
                )

                if success:
                    print(f"🤖 Auto-decided for {decision['agent']} on PR #{decision['pr_number']}")
                    print(f"   Q: {decision['question'][:80]}")
                    print(f"   → {response['option']} ({response['reason']})")
                else:
                    print(f"⚠️  Failed to post RESPONSE on PR #{decision['pr_number']}")
                    escalations.append(decision)
            else:
                escalations.append(decision)

        return escalations

    def monitor_loop(self, callback=None):
        """Run monitoring loop, polling GitHub for decisions."""
        print(f"\n🔍 Monitoring GitHub PR comments for decisions")
        print(f"   Poll interval: {self.poll_interval}s")
        print("   Press Ctrl+C to stop\n")

        try:
            while True:
                escalations = self.process_decisions()

                if escalations:
                    print(f"\n⚠️  {len(escalations)} decision(s) need your input:\n")
                    for i, dec in enumerate(escalations, 1):
                        print(f"  {i}. PR #{dec['pr_number']} (agent {dec['agent']})")
                        print(f"     Q: {dec['question']}")
                        print(f"     Options: {', '.join(dec['options'])}")
                        if dec.get('comment_url'):
                            print(f"     Link: {dec['comment_url']}")
                        print()

                    if callback:
                        callback(escalations)
                    else:
                        print("📝 Reply on GitHub with ORCHESTRATE:RESPONSE comment, or wait...")

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\n\n⏹  Monitoring stopped")
