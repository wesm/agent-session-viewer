"""Tests for sync module."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_session_viewer import sync


class TestFindSourceFile:
    """Tests for find_source_file path validation."""

    def test_valid_session_id(self, tmp_path):
        """Valid session IDs should find existing files."""
        # Set up mock project structure
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        session_file = project_dir / "abc123.jsonl"
        session_file.write_text("{}")

        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            result = sync.find_source_file("abc123")
            assert result == session_file

    def test_session_id_with_hyphens_underscores(self, tmp_path):
        """Session IDs with hyphens and underscores should work."""
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        session_file = project_dir / "session-123_test.jsonl"
        session_file.write_text("{}")

        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            result = sync.find_source_file("session-123_test")
            assert result == session_file

    def test_path_traversal_dotdot_blocked(self, tmp_path):
        """Path traversal with ../ should be blocked."""
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()

        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            result = sync.find_source_file("../etc/passwd")
            assert result is None

    def test_path_traversal_absolute_blocked(self, tmp_path):
        """Absolute paths should be blocked."""
        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            result = sync.find_source_file("/etc/passwd")
            assert result is None

    def test_path_traversal_slash_blocked(self, tmp_path):
        """Paths with slashes should be blocked."""
        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            result = sync.find_source_file("foo/bar")
            assert result is None

    def test_empty_session_id(self, tmp_path):
        """Empty session ID should return None."""
        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            result = sync.find_source_file("")
            assert result is None

    def test_none_session_id(self, tmp_path):
        """None session ID should return None."""
        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            result = sync.find_source_file(None)
            assert result is None

    def test_special_characters_blocked(self, tmp_path):
        """Session IDs with special characters should be blocked."""
        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            # Various injection attempts
            assert sync.find_source_file("test;ls") is None
            assert sync.find_source_file("test`ls`") is None
            assert sync.find_source_file("test$(ls)") is None
            assert sync.find_source_file("test\x00null") is None

    def test_nonexistent_session(self, tmp_path):
        """Non-existent session should return None."""
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()

        with patch.object(sync, "CLAUDE_PROJECTS_DIR", tmp_path):
            result = sync.find_source_file("nonexistent")
            assert result is None

    def test_nonexistent_projects_dir(self, tmp_path):
        """Non-existent projects directory should return None."""
        nonexistent = tmp_path / "nonexistent"

        with patch.object(sync, "CLAUDE_PROJECTS_DIR", nonexistent):
            result = sync.find_source_file("abc123")
            assert result is None
