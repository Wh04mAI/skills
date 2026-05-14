"""
Next-step advisor for orchestrate skill.

Analyzes current state and recommends what to do next:
- Check decision queue for pending escalations
- Check PR status for stuck/ready PRs
- Check ready-for-dev stories
- Detect potential conflicts and suggest bundles
- Suggest concrete next actions
"""

import re
from pathlib import Path
from typing import Dict, List, Set, Tuple
from decision_monitor import DecisionMonitor
from pr_monitor import PRMonitor


# Common words to ignore when extracting keywords
STOP_WORDS = {
    'and', 'or', 'the', 'a', 'an', 'of', 'for', 'to', 'in', 'on',
    'with', 'api', 'and', 'gc',  # 'api', 'gc' too generic
}


def extract_keywords(story_id: str) -> Set[str]:
    """Extract semantic keywords from story ID.

    Examples:
        '6-1-template-query-api' → {'template', 'query'}
        '6-2-template-lifecycle-and-gc' → {'template', 'lifecycle'}
    """
    # Strip leading number-number prefix (e.g., "6-1-" or "7-3-")
    parts = story_id.split('-')
    # Remove first 2 segments if they look like epic-story numbers
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        parts = parts[2:]

    keywords = set()
    for part in parts:
        word = part.lower().strip()
        if word and word not in STOP_WORDS and len(word) > 2:
            keywords.add(word)

    return keywords


def get_epic(story_id: str) -> str:
    """Extract epic number from story ID. e.g., '6-1-...' → '6'."""
    parts = story_id.split('-')
    return parts[0] if parts else ''


def detect_bundles(ready_stories: List[str]) -> List[List[str]]:
    """Detect groups of stories that should likely be bundled.

    Heuristic: same epic + at least 1 shared keyword.

    Returns list of story groups (each group has 2+ stories).
    """
    # Group by epic
    by_epic = {}
    for story_id in ready_stories:
        epic = get_epic(story_id)
        by_epic.setdefault(epic, []).append(story_id)

    bundles = []

    # Within each epic, find groups with shared keywords
    for epic, stories in by_epic.items():
        if len(stories) < 2:
            continue

        # Build keyword map
        kw_map = {s: extract_keywords(s) for s in stories}

        # Find clusters: stories sharing at least 1 keyword
        # Simple approach: for each pair, check overlap
        used = set()
        for i, s1 in enumerate(stories):
            if s1 in used:
                continue
            cluster = [s1]
            for s2 in stories[i+1:]:
                if s2 in used:
                    continue
                if kw_map[s1] & kw_map[s2]:  # Set intersection
                    cluster.append(s2)
                    used.add(s2)
            if len(cluster) >= 2:
                used.add(s1)
                bundles.append(cluster)

    return bundles


class NextAdvisor:
    """Analyze state and recommend next steps."""

    def __init__(self, config: Dict, adapter, decision_rules_path: Path):
        self.config = config
        self.adapter = adapter
        self.decision_monitor = DecisionMonitor(config, decision_rules_path)
        self.pr_monitor = PRMonitor(config, adapter)

    def analyze(self) -> Dict:
        """Analyze current state and return findings.

        Returns:
            {
                'pending_escalations': [...],
                'active_prs': [...],
                'ready_stories': [...],
                'review_prs': [...],
                'failed_prs': [...]
            }
        """
        findings = {
            'pending_escalations': [],
            'active_prs': [],
            'ready_stories': [],
            'review_prs': [],
            'failed_prs': [],
            'merged_prs': []
        }

        # Check pending decisions across all active PRs
        # Decisions that can't be auto-decided become escalations
        escalations = []
        pending = self.decision_monitor.get_pending_decisions()

        for dec in pending:
            rule = self.decision_monitor.match_rule(dec)
            if rule is None:
                escalations.append(dec)

        findings['pending_escalations'] = escalations

        # Check ready-for-dev stories
        try:
            ready = self.adapter.list_ready_stories()
            findings['ready_stories'] = ready
        except Exception:
            pass

        # Check active PRs
        active_prs = self.pr_monitor.discover_active_prs()
        findings['active_prs'] = active_prs

        # Check status of each PR
        for pr_info in active_prs:
            pr_status = self.pr_monitor.get_pr_status(pr_info['pr_number'])
            if not pr_status:
                continue

            pr_state = pr_status.get('state', '').upper()
            is_draft = pr_status.get('is_draft', False)

            if pr_state == 'MERGED':
                findings['merged_prs'].append({
                    **pr_info,
                    'status': pr_status
                })
            elif pr_state == 'OPEN':
                if self.pr_monitor.has_failed_check(pr_status):
                    findings['failed_prs'].append({
                        **pr_info,
                        'status': pr_status
                    })
                elif not is_draft and self.pr_monitor.all_required_checks_passed(pr_status):
                    findings['review_prs'].append({
                        **pr_info,
                        'status': pr_status
                    })

        return findings

    def recommend(self) -> List[Dict]:
        """Generate prioritized recommendations.

        Returns list of {priority, action, command, reason}.
        """
        findings = self.analyze()
        recommendations = []

        # Priority 1: Pending escalations (blocking)
        if findings['pending_escalations']:
            for esc in findings['pending_escalations']:
                recommendations.append({
                    'priority': 1,
                    'icon': '⚠️',
                    'action': f"Resolve decision for agent {esc['agent']}",
                    'detail': f"Story {esc['story']}: {esc['question']}",
                    'options': esc.get('options', []),
                    'how_to': f"Reply on PR with <!-- ORCHESTRATE:RESPONSE agent={esc.get('agent','')} story={esc.get('story','')} --> comment"
                })

        # Priority 2: Failed CI PRs (blocking)
        if findings['failed_prs']:
            for pr in findings['failed_prs']:
                recommendations.append({
                    'priority': 2,
                    'icon': '🔥',
                    'action': f"Check CI failure on PR #{pr['pr_number']}",
                    'detail': f"Story {pr['story_id']} (agent {pr['agent_id']}) - dev needs to fix",
                    'how_to': f"gh pr checks {pr['pr_number']} --watch"
                })

        # Priority 3: PRs ready for review (action needed)
        if findings['review_prs']:
            for pr in findings['review_prs']:
                recommendations.append({
                    'priority': 3,
                    'icon': '👀',
                    'action': f"Review and merge PR #{pr['pr_number']}",
                    'detail': f"Story {pr['story_id']}: CI green, ready for human review",
                    'how_to': f"gh pr view {pr['pr_number']} && gh pr merge {pr['pr_number']} --rebase"
                })

        # Priority 4: Recently merged PRs (cleanup)
        if findings['merged_prs']:
            for pr in findings['merged_prs']:
                recommendations.append({
                    'priority': 4,
                    'icon': '🎉',
                    'action': f"PR #{pr['pr_number']} merged - clean up",
                    'detail': f"Story {pr['story_id']} done, branch may need cleanup",
                    'how_to': f"git push origin --delete <branch>  # if not auto-deleted"
                })

        # Priority 5: Available work (no active blockers)
        if findings['ready_stories'] and len(findings['active_prs']) < self.config['orchestrator'].get('max_concurrent_agents', 5):
            available_slots = self.config['orchestrator'].get('max_concurrent_agents', 5) - len(findings['active_prs'])

            # Detect potential bundles among ready stories
            bundles = detect_bundles(findings['ready_stories'])
            bundled_set = set()
            for bundle in bundles:
                bundled_set.update(bundle)

            # Independent stories (no detected conflict)
            independent = [s for s in findings['ready_stories'] if s not in bundled_set]

            # Suggest bundles first (warn about potential conflicts)
            for bundle in bundles:
                # Collect all pairwise shared keywords to show what links them
                kw_per_story = [extract_keywords(s) for s in bundle]
                all_shared = set()
                for i in range(len(kw_per_story)):
                    for j in range(i+1, len(kw_per_story)):
                        all_shared |= (kw_per_story[i] & kw_per_story[j])

                if all_shared:
                    kw_str = ', '.join(sorted(all_shared))
                    reason = f"shared keywords: {kw_str}"
                else:
                    reason = f"same Epic, related concepts"

                recommendations.append({
                    'priority': 5,
                    'icon': '🔗',
                    'action': f"⚠️ {len(bundle)} stories may conflict ({reason})",
                    'detail': f"Stories: {', '.join(bundle)}",
                    'how_to': (
                        f"Option A (safe, serial): give all to one agent\n"
                        f"      /orchestrate --bundle {' '.join(bundle)}\n"
                        f"   Option B (risky, parallel): accept potential conflicts\n"
                        f"      /orchestrate {' '.join(bundle)}\n"
                        f"   Option C (review story files first to confirm)"
                    )
                })

            # Suggest independent stories for parallel
            if independent:
                stories_to_dispatch = independent[:available_slots]
                recommendations.append({
                    'priority': 6,
                    'icon': '🚀',
                    'action': f"Dispatch independent stories ({available_slots} slots, no conflicts detected)",
                    'detail': f"Ready: {', '.join(stories_to_dispatch[:3])}{'...' if len(stories_to_dispatch) > 3 else ''}",
                    'how_to': f"/orchestrate {' '.join(stories_to_dispatch[:2])}"
                })

        # If nothing else, suggest monitoring
        if not recommendations and findings['active_prs']:
            recommendations.append({
                'priority': 6,
                'icon': '📡',
                'action': "Wait for agents to finish",
                'detail': f"{len(findings['active_prs'])} agent(s) working - check back later",
                'how_to': "/orchestrate --monitor"
            })

        # Nothing to do
        if not recommendations:
            recommendations.append({
                'priority': 99,
                'icon': '✨',
                'action': "All caught up",
                'detail': "No pending work, no active agents",
                'how_to': "Wait for new stories or refine sprint plan"
            })

        return sorted(recommendations, key=lambda x: x['priority'])

    def show(self):
        """Print formatted recommendations."""
        findings = self.analyze()

        print("\n" + "=" * 70)
        print("📊 Current State")
        print("=" * 70)
        print(f"  Ready-for-dev stories: {len(findings['ready_stories'])}")
        print(f"  Active PRs: {len(findings['active_prs'])}")
        print(f"  Pending escalations: {len(findings['pending_escalations'])}")
        print(f"  PRs ready for review: {len(findings['review_prs'])}")
        print(f"  PRs with failed CI: {len(findings['failed_prs'])}")
        print(f"  Recently merged: {len(findings['merged_prs'])}")

        recommendations = self.recommend()

        print("\n" + "=" * 70)
        print("🎯 Next Steps (prioritized)")
        print("=" * 70)

        for i, rec in enumerate(recommendations, 1):
            print(f"\n{rec['icon']}  {i}. {rec['action']}")
            print(f"   {rec['detail']}")
            print(f"   How: {rec['how_to']}")

            if 'options' in rec and rec['options']:
                print(f"   Options: {', '.join(rec['options'])}")

        print()
