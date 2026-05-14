"""
Abstract base class for story tracking adapters.

Adapters provide a uniform interface for different project structures:
- github_pr: Derives story status from GitHub PR state + file Status: fallback
- file_scanner: Scans filesystem for story files
- jira: Queries Jira API
- linear: Queries Linear API
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional


class StoryAdapter(ABC):
    """Abstract interface for story tracking systems."""

    def __init__(self, config: Dict):
        """
        Initialize adapter with configuration.

        Args:
            config: Adapter-specific configuration from customize.toml
        """
        self.config = config

    @abstractmethod
    def list_ready_stories(self) -> List[str]:
        """
        Return story IDs in 'ready-for-dev' status.

        Returns:
            List of story IDs (e.g., ['4.2', '4.3', '6.1'])
        """
        pass

    @abstractmethod
    def get_story_details(self, story_id: str) -> Dict:
        """
        Return detailed information about a story.

        Args:
            story_id: Story identifier (e.g., '4.2')

        Returns:
            Dictionary with keys:
                - title: Story title
                - file_path: Path to story file
                - hints: Optional story-specific hints
                - status: Current status
        """
        pass

    @abstractmethod
    def update_story_status(self, story_id: str, status: str) -> None:
        """
        Update story status.

        Args:
            story_id: Story identifier
            status: New status (e.g., 'in-progress', 'review', 'done')
        """
        pass

    @abstractmethod
    def get_project_context(self) -> Dict:
        """
        Return project-level context for dispatch prompts.

        Returns:
            Dictionary with keys:
                - repo_path: Absolute path to repository root
                - base_branch: Default branch for PRs (e.g., 'main', 'dev')
                - test_command: Command to run tests (e.g., 'make test')
                - agents_doc: Path to AGENTS.md (if exists)
        """
        pass

    def validate_story_id(self, story_id: str) -> bool:
        """
        Check if story ID is valid.

        Args:
            story_id: Story identifier to validate

        Returns:
            True if valid, False otherwise
        """
        try:
            details = self.get_story_details(story_id)
            return details is not None
        except Exception:
            return False
