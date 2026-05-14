#!/usr/bin/env python3
"""
Integration test for orchestrate skill.
Tests adapter loading, config override, and dispatch prompt generation.
"""

import sys
import tomllib
import json
import tempfile
from pathlib import Path

# Add lib to path
skill_root = Path(__file__).parent
sys.path.insert(0, str(skill_root / "lib"))

from adapters.github_pr_adapter import GitHubPRAdapter
from orchestrator import Orchestrator
import adapters.github_pr_adapter as github_pr_adapter


def test_config_loading():
    """Test configuration loading and override."""
    print("1. Testing config loading...")

    config_path = skill_root / "customize.toml"
    with open(config_path, 'rb') as f:
        config = tomllib.load(f)

    base_max = config['orchestrator']['max_concurrent_agents']
    print(f"   Base max_concurrent_agents: {base_max}")

    # Check for project override
    project_override = Path.cwd() / "_bmad/team/orchestrate.toml"
    if project_override.exists():
        with open(project_override, 'rb') as f:
            override = tomllib.load(f)
            for key, value in override.items():
                if isinstance(value, dict) and key in config:
                    config[key].update(value)
                else:
                    config[key] = value

        override_max = config['orchestrator']['max_concurrent_agents']
        print(f"   After override: {override_max}")
        assert override_max != base_max, "Override should change value"
        print("   ✓ Config override works")
    else:
        print("   ⚠️  No project override found (optional)")

    return config


def test_adapter(config):
    """Test adapter initialization and story loading."""
    print("\n2. Testing adapter...")

    adapter = GitHubPRAdapter(config['adapter']['github_pr'])

    ready_stories = adapter.list_ready_stories()
    print(f"   Found {len(ready_stories)} ready stories")

    if ready_stories:
        story_id = ready_stories[0]
        details = adapter.get_story_details(story_id)
        print(f"   Sample: {story_id} - {details['title']}")
        print("   ✓ Adapter works")
    else:
        print("   ⚠️  No ready stories found")

    return adapter, ready_stories


def test_orchestrator(config, adapter, ready_stories):
    """Test orchestrator dispatch prompt generation."""
    print("\n3. Testing orchestrator...")

    orch = Orchestrator(config, adapter)

    if ready_stories:
        test_story = ready_stories[0]
        prompts = orch.dispatch_stories([test_story])

        assert len(prompts) == 1, "Should generate 1 prompt"
        agent_id = list(prompts.keys())[0]
        prompt = prompts[agent_id]

        print(f"   Generated prompt for {agent_id}")
        print(f"   Prompt length: {len(prompt)} chars")

        # Verify prompt contains key elements
        assert test_story in prompt, "Prompt should contain story ID"
        assert "AGENTS.md" in prompt, "Prompt should reference AGENTS.md"
        assert "decision" in prompt.lower(), "Prompt should mention decision protocol"

        print("   ✓ Orchestrator works")
    else:
        print("   ⚠️  Skipped (no ready stories)")


def test_closed_pr_blocks_ready_fallback(config):
    """Closed implementation PRs should not make a story look ready again."""
    print("\n4. Testing closed PR status derivation...")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        story_dir = root / "stories"
        story_dir.mkdir()
        (story_dir / "3-1-power-control.md").write_text(
            "Status: ready-for-dev\n\n# Story 3.1: Power Control\n"
        )

        adapter = GitHubPRAdapter({"story_files_dir": "stories"})
        adapter.repo_path = root

        original_run = github_pr_adapter.subprocess.run

        def fake_run(cmd, **kwargs):
            class Result:
                def __init__(self, stdout):
                    self.returncode = 0
                    self.stdout = stdout

            if cmd[:4] == ["gh", "pr", "list", "--state"]:
                state = cmd[4]
                prs = []
                if state == "closed":
                    prs = [{
                        "number": 42,
                        "title": "feat(story-3.1): power-control",
                        "headRefName": "alice-claude-1/story-3.1-power-control",
                        "state": "CLOSED",
                        "isDraft": False,
                        "author": {"login": "alice"},
                        "body": "",
                    }]
                return Result(json.dumps(prs))
            if cmd[:4] == ["gh", "pr", "view", "42"]:
                return Result("")
            raise AssertionError(f"unexpected subprocess command: {cmd}")

        github_pr_adapter.subprocess.run = fake_run
        try:
            statuses = adapter._get_all_story_statuses()
        finally:
            github_pr_adapter.subprocess.run = original_run

        assert statuses["3-1-power-control"] == "closed"
        assert "3-1-power-control" not in adapter.list_ready_stories()
        print("   ✓ Closed PRs override ready-for-dev fallback")


def test_merged_bundle_claim_maps_all_stories(config):
    """Merged bundle PRs should mark every claimed story done."""
    print("\n5. Testing merged bundle PR story mapping...")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        story_dir = root / "stories"
        story_dir.mkdir()
        for sid in ("5-1-vnc-console", "5-2-one-time-credential"):
            (story_dir / f"{sid}.md").write_text(
                f"Status: ready-for-dev\n\n# Story {sid}\n"
            )

        adapter = GitHubPRAdapter({"story_files_dir": "stories"})
        adapter.repo_path = root

        original_run = github_pr_adapter.subprocess.run

        def fake_run(cmd, **kwargs):
            class Result:
                def __init__(self, stdout):
                    self.returncode = 0
                    self.stdout = stdout

            if cmd[:4] == ["gh", "pr", "list", "--state"]:
                state = cmd[4]
                prs = []
                if state == "merged":
                    prs = [{
                        "number": 77,
                        "title": "feat(bundle): console and credential",
                        "headRefName": "alice-claude-1/bundle-5.1-5.2",
                        "state": "MERGED",
                        "isDraft": False,
                        "author": {"login": "alice"},
                        "body": "",
                    }]
                return Result(json.dumps(prs))
            if cmd[:4] == ["gh", "pr", "view", "77"]:
                return Result(
                    '<!-- ORCHESTRATE:CLAIM agent=alice-claude-1 '
                    'stories=["5-1-vnc-console","5-2-one-time-credential"] -->\n'
                )
            raise AssertionError(f"unexpected subprocess command: {cmd}")

        github_pr_adapter.subprocess.run = fake_run
        try:
            statuses = adapter._get_all_story_statuses()
        finally:
            github_pr_adapter.subprocess.run = original_run

        assert statuses["5-1-vnc-console"] == "done"
        assert statuses["5-2-one-time-credential"] == "done"
        assert adapter.list_ready_stories() == []
        print("   ✓ Merged bundle CLAIM maps every story")


def main():
    """Run all tests."""
    print("=" * 70)
    print("Orchestrate Skill Integration Test")
    print("=" * 70)

    try:
        config = test_config_loading()
        adapter, ready_stories = test_adapter(config)
        test_orchestrator(config, adapter, ready_stories)
        test_closed_pr_blocks_ready_fallback(config)
        test_merged_bundle_claim_maps_all_stories(config)

        print("\n" + "=" * 70)
        print("✅ All tests passed!")
        print("=" * 70)

    except Exception as e:
        print("\n" + "=" * 70)
        print(f"❌ Test failed: {e}")
        print("=" * 70)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
