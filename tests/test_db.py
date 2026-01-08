"""Tests for database module."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_session_viewer import db


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database."""
    test_db_path = tmp_path / "test.db"
    test_data_dir = tmp_path

    with patch.object(db, "DB_PATH", test_db_path), \
         patch.object(db, "DATA_DIR", test_data_dir):
        db.init_db()
        yield test_db_path


class TestGetSessions:
    """Tests for get_sessions filtering behavior."""

    def test_filters_zero_message_count(self, test_db, tmp_path):
        """Sessions with message_count=0 should be filtered out."""
        with patch.object(db, "DB_PATH", test_db), \
             patch.object(db, "DATA_DIR", tmp_path):
            # Insert sessions with different message counts
            db.upsert_session("sess-with-msgs", "project1", message_count=5)
            db.upsert_session("sess-zero", "project1", message_count=0)
            db.upsert_session("sess-more-msgs", "project1", message_count=10)

            sessions = db.get_sessions()

            session_ids = [s["id"] for s in sessions]
            assert "sess-with-msgs" in session_ids
            assert "sess-more-msgs" in session_ids
            assert "sess-zero" not in session_ids

    def test_filters_null_message_count(self, test_db, tmp_path):
        """Sessions with NULL message_count should be filtered out."""
        with patch.object(db, "DB_PATH", test_db), \
             patch.object(db, "DATA_DIR", tmp_path):
            # Insert session with explicit message_count
            db.upsert_session("sess-with-msgs", "project1", message_count=5)

            # Insert session with NULL message_count directly via SQL
            with db.get_db() as conn:
                conn.execute(
                    "INSERT INTO sessions (id, project, message_count) VALUES (?, ?, NULL)",
                    ("sess-null", "project1")
                )

            sessions = db.get_sessions()

            session_ids = [s["id"] for s in sessions]
            assert "sess-with-msgs" in session_ids
            assert "sess-null" not in session_ids

    def test_returns_sessions_with_positive_message_count(self, test_db, tmp_path):
        """Sessions with positive message_count should be returned."""
        with patch.object(db, "DB_PATH", test_db), \
             patch.object(db, "DATA_DIR", tmp_path):
            db.upsert_session("sess-1", "project1", message_count=1)
            db.upsert_session("sess-100", "project1", message_count=100)

            sessions = db.get_sessions()

            assert len(sessions) == 2
            session_ids = [s["id"] for s in sessions]
            assert "sess-1" in session_ids
            assert "sess-100" in session_ids

    def test_filters_by_project(self, test_db, tmp_path):
        """Should filter sessions by project when specified."""
        with patch.object(db, "DB_PATH", test_db), \
             patch.object(db, "DATA_DIR", tmp_path):
            db.upsert_session("sess-a", "project-a", message_count=5)
            db.upsert_session("sess-b", "project-b", message_count=5)

            sessions = db.get_sessions(project="project-a")

            assert len(sessions) == 1
            assert sessions[0]["id"] == "sess-a"

    def test_respects_limit(self, test_db, tmp_path):
        """Should respect the limit parameter."""
        with patch.object(db, "DB_PATH", test_db), \
             patch.object(db, "DATA_DIR", tmp_path):
            for i in range(10):
                db.upsert_session(f"sess-{i}", "project1", message_count=5)

            sessions = db.get_sessions(limit=3)

            assert len(sessions) == 3

    def test_empty_database(self, test_db, tmp_path):
        """Should return empty list for empty database."""
        with patch.object(db, "DB_PATH", test_db), \
             patch.object(db, "DATA_DIR", tmp_path):
            sessions = db.get_sessions()
            assert sessions == []
