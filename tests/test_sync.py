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


class TestFindCodexSourceFile:
    """Tests for Codex source file lookup with UUID extraction."""

    def _create_codex_structure(self, tmp_path, filename):
        """Helper to create Codex directory structure with a session file."""
        day_dir = tmp_path / "2026" / "01" / "08"
        day_dir.mkdir(parents=True)
        session_file = day_dir / filename
        session_file.write_text("{}")
        return session_file

    def test_valid_uuid_match(self, tmp_path):
        """Valid UUID should find the correct file."""
        session_file = self._create_codex_structure(
            tmp_path,
            "rollout-2026-01-08T06-48-54-019b9da7-1f41-7af2-80d9-6e293902fea8.jsonl"
        )

        with patch.object(sync, "CODEX_SESSIONS_DIR", tmp_path):
            result = sync._find_codex_source_file("019b9da7-1f41-7af2-80d9-6e293902fea8")
            assert result == session_file

    def test_uuid_with_extra_timestamp_dashes(self, tmp_path):
        """UUID extraction should work even with extra dashes in timestamp (e.g., millis)."""
        # Simulate a timestamp with milliseconds: 2026-01-08T06-48-54-123
        session_file = self._create_codex_structure(
            tmp_path,
            "rollout-2026-01-08T06-48-54-123-019b9da7-1f41-7af2-80d9-6e293902fea8.jsonl"
        )

        with patch.object(sync, "CODEX_SESSIONS_DIR", tmp_path):
            result = sync._find_codex_source_file("019b9da7-1f41-7af2-80d9-6e293902fea8")
            assert result == session_file

    def test_uuid_with_timezone_in_timestamp(self, tmp_path):
        """UUID extraction should work with timezone offset in timestamp."""
        # Simulate timezone: 2026-01-08T06-48-54-0600
        session_file = self._create_codex_structure(
            tmp_path,
            "rollout-2026-01-08T06-48-54-0600-019b9da7-1f41-7af2-80d9-6e293902fea8.jsonl"
        )

        with patch.object(sync, "CODEX_SESSIONS_DIR", tmp_path):
            result = sync._find_codex_source_file("019b9da7-1f41-7af2-80d9-6e293902fea8")
            assert result == session_file

    def test_partial_uuid_no_match(self, tmp_path):
        """Partial UUID should not match."""
        self._create_codex_structure(
            tmp_path,
            "rollout-2026-01-08T06-48-54-019b9da7-1f41-7af2-80d9-6e293902fea8.jsonl"
        )

        with patch.object(sync, "CODEX_SESSIONS_DIR", tmp_path):
            # Missing first segment
            result = sync._find_codex_source_file("1f41-7af2-80d9-6e293902fea8")
            assert result is None

    def test_similar_uuid_no_collision(self, tmp_path):
        """Similar but different UUIDs should not collide."""
        # Create two files with similar UUIDs
        self._create_codex_structure(
            tmp_path,
            "rollout-2026-01-08T06-48-54-019b9da7-1f41-7af2-80d9-6e293902fea8.jsonl"
        )
        day_dir = tmp_path / "2026" / "01" / "08"
        other_file = day_dir / "rollout-2026-01-08T07-00-00-019b9da7-1f41-7af2-80d9-000000000000.jsonl"
        other_file.write_text("{}")

        with patch.object(sync, "CODEX_SESSIONS_DIR", tmp_path):
            # Should find exact match only
            result = sync._find_codex_source_file("019b9da7-1f41-7af2-80d9-6e293902fea8")
            assert result is not None
            assert "6e293902fea8" in result.name

    def test_codex_prefix_routing(self, tmp_path):
        """find_source_file should route codex: prefixed IDs correctly."""
        session_file = self._create_codex_structure(
            tmp_path,
            "rollout-2026-01-08T06-48-54-019b9da7-1f41-7af2-80d9-6e293902fea8.jsonl"
        )

        with patch.object(sync, "CODEX_SESSIONS_DIR", tmp_path):
            result = sync.find_source_file("codex:019b9da7-1f41-7af2-80d9-6e293902fea8")
            assert result == session_file

    def test_nonexistent_codex_dir(self, tmp_path):
        """Non-existent Codex directory should return None."""
        nonexistent = tmp_path / "nonexistent"

        with patch.object(sync, "CODEX_SESSIONS_DIR", nonexistent):
            result = sync._find_codex_source_file("019b9da7-1f41-7af2-80d9-6e293902fea8")
            assert result is None


class TestCodexExecFiltering:
    """Tests for filtering non-interactive Codex sessions."""

    def test_skip_codex_exec_by_default(self, tmp_path):
        """Sessions with originator=codex_exec should be skipped by default."""
        from agent_session_viewer.parser import parse_codex_session

        session_file = tmp_path / "test.jsonl"
        session_file.write_text(
            '{"type":"session_meta","payload":{"id":"test-id","cwd":"/test","originator":"codex_exec"}}\n'
        )

        metadata, messages = parse_codex_session(session_file)
        assert metadata is None
        assert messages == []

    def test_include_codex_exec_when_flag_set(self, tmp_path):
        """Sessions with originator=codex_exec should be included when include_exec=True."""
        from agent_session_viewer.parser import parse_codex_session

        session_file = tmp_path / "test.jsonl"
        session_file.write_text(
            '{"type":"session_meta","payload":{"id":"test-id","cwd":"/test","originator":"codex_exec"}}\n'
        )

        metadata, messages = parse_codex_session(session_file, include_exec=True)
        assert metadata is not None
        assert metadata.session_id == "codex:test-id"

    def test_include_interactive_sessions(self, tmp_path):
        """Interactive sessions (codex_cli_rs) should be included."""
        from agent_session_viewer.parser import parse_codex_session

        session_file = tmp_path / "test.jsonl"
        session_file.write_text(
            '{"type":"session_meta","payload":{"id":"test-id","cwd":"/test","originator":"codex_cli_rs"}}\n'
        )

        metadata, messages = parse_codex_session(session_file)
        assert metadata is not None
        assert metadata.session_id == "codex:test-id"

    def test_missing_originator_included(self, tmp_path):
        """Sessions without originator field should be included."""
        from agent_session_viewer.parser import parse_codex_session

        session_file = tmp_path / "test.jsonl"
        session_file.write_text(
            '{"type":"session_meta","payload":{"id":"test-id","cwd":"/test"}}\n'
        )

        metadata, messages = parse_codex_session(session_file)
        assert metadata is not None
        assert metadata.session_id == "codex:test-id"


class TestGetProjectName:
    """Tests for get_project_name function."""

    def test_extracts_last_component_from_encoded_path(self):
        """Should extract just the project name from encoded full path."""
        # Claude Code encodes paths like: -Users-user-Projects-roborev
        dir_path = Path("-Users-user-Projects-roborev")
        result = sync.get_project_name(dir_path)
        assert result == "roborev"

    def test_extracts_from_path_with_dashes(self):
        """Should preserve dashes in project names."""
        dir_path = Path("-Users-user-Projects-my-app")
        result = sync.get_project_name(dir_path)
        assert result == "my-app"

    def test_extracts_from_path_with_code_directory(self):
        """Should extract project name when 'code' is in the path."""
        dir_path = Path("-Users-user-code-myproject")
        result = sync.get_project_name(dir_path)
        assert result == "myproject"

    def test_simple_directory_name(self):
        """Should handle simple directory names without leading dash."""
        dir_path = Path("simple-project")
        result = sync.get_project_name(dir_path)
        assert result == "simple-project"

    def test_extracts_from_downloads_path(self):
        """Should extract project name from Downloads directory."""
        dir_path = Path("-Users-user-Downloads-my-project")
        result = sync.get_project_name(dir_path)
        assert result == "my-project"

    def test_extracts_from_experiments_path(self):
        """Should extract project name from Experiments directory."""
        dir_path = Path("-Users-user-Experiments-test-app")
        result = sync.get_project_name(dir_path)
        assert result == "test-app"
