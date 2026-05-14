"""
File scanner adapter - scans filesystem for story files.

Default adapter with minimal assumptions. Scans directories for markdown files,
extracts story ID and status from file content using regex patterns.
"""

import re
from pathlib import Path
from typing import List, Dict, Optional
from .base import StoryAdapter


class FileScannerAdapter(StoryAdapter):
    """Adapter that scans filesystem for story files."""

    def __init__(self, config: Dict):
        super().__init__(config)
        self.story_patterns = config.get('story_patterns', ['stories/*.md'])
        self.status_marker_regex = config.get('status_marker_regex', r'^Status:\s*(.+)$')
        self.id_pattern_regex = config.get('id_pattern_regex', r'^#\s*Story\s+([0-9.-]+)')
        self.repo_path = Path.cwd()

    def list_ready_stories(self) -> List[str]:
        """Scan filesystem for stories with 'ready-for-dev' status."""
        ready_stories = []

        for pattern in self.story_patterns:
            for story_file in self.repo_path.glob(pattern):
                if not story_file.is_file():
                    continue

                story_id = self._extract_story_id(story_file)
                status = self._extract_status(story_file)

                if story_id and status and 'ready-for-dev' in status.lower():
                    ready_stories.append(story_id)

        return ready_stories

    def get_story_details(self, story_id: str) -> Dict:
        """Find story file and extract details."""
        for pattern in self.story_patterns:
            for story_file in self.repo_path.glob(pattern):
                if not story_file.is_file():
                    continue

                file_story_id = self._extract_story_id(story_file)
                if file_story_id == story_id:
                    return {
                        'title': self._extract_title(story_file),
                        'file_path': str(story_file),
                        'hints': '',
                        'status': self._extract_status(story_file) or 'unknown'
                    }

        raise ValueError(f"Story {story_id} not found")

    def update_story_status(self, story_id: str, status: str) -> None:
        """Update status marker in story file."""
        details = self.get_story_details(story_id)
        story_file = Path(details['file_path'])

        content = story_file.read_text()

        # Replace existing status line or add new one
        status_pattern = re.compile(self.status_marker_regex, re.MULTILINE)
        if status_pattern.search(content):
            content = status_pattern.sub(f'Status: {status}', content)
        else:
            # Add status after first heading
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('#'):
                    lines.insert(i + 1, f'\nStatus: {status}\n')
                    break
            content = '\n'.join(lines)

        story_file.write_text(content)

    def get_project_context(self) -> Dict:
        """Return project context from filesystem inspection."""
        # Detect base branch
        try:
            import subprocess
            result = subprocess.run(
                ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                capture_output=True,
                text=True,
                timeout=5
            )
            base_branch = result.stdout.strip().split('/')[-1] if result.returncode == 0 else 'main'
        except:
            base_branch = 'main'

        # Detect test command
        test_command = 'make test'
        if (self.repo_path / 'Makefile').exists():
            test_command = 'make test'
        elif (self.repo_path / 'package.json').exists():
            test_command = 'npm test'
        elif (self.repo_path / 'pytest.ini').exists():
            test_command = 'pytest'

        # Check for AGENTS.md
        agents_doc = ''
        if (self.repo_path / 'AGENTS.md').exists():
            agents_doc = 'AGENTS.md'

        return {
            'repo_path': str(self.repo_path),
            'base_branch': base_branch,
            'test_command': test_command,
            'agents_doc': agents_doc
        }

    def _extract_story_id(self, file_path: Path) -> Optional[str]:
        """Extract story ID from file content."""
        content = file_path.read_text()
        match = re.search(self.id_pattern_regex, content, re.MULTILINE)
        return match.group(1) if match else None

    def _extract_status(self, file_path: Path) -> Optional[str]:
        """Extract status from file content."""
        content = file_path.read_text()
        match = re.search(self.status_marker_regex, content, re.MULTILINE)
        return match.group(1).strip() if match else None

    def _extract_title(self, file_path: Path) -> str:
        """Extract title from first heading."""
        content = file_path.read_text()
        match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        return match.group(1).strip() if match else file_path.stem
