"""SQLite database with FTS5 for session storage and search."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

# Data stored in user's home directory
DATA_DIR = Path.home() / ".agent-session-viewer"
DB_PATH = DATA_DIR / "sessions.db"


@dataclass
class Session:
    id: str
    project: str
    machine: str
    first_message: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]
    message_count: int
    created_at: str


@dataclass
class Message:
    id: int
    session_id: str
    msg_id: str
    role: str
    content: str
    timestamp: str


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with get_db() as conn:
        conn.executescript("""
            -- Sessions table
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                machine TEXT DEFAULT 'local',
                first_message TEXT,
                started_at TEXT,
                ended_at TEXT,
                message_count INTEGER DEFAULT 0,
                file_size INTEGER,
                file_hash TEXT,
                agent TEXT DEFAULT 'claude',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Messages table
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                msg_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            -- Full-text search virtual table
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content='messages',
                content_rowid='id'
            );

            -- Triggers to keep FTS in sync
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END;

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
            CREATE INDEX IF NOT EXISTS idx_sessions_machine ON sessions(machine);
        """)


def session_exists(session_id: str) -> bool:
    """Check if a session exists."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None


def get_session_file_info(session_id: str) -> Optional[tuple[int, str]]:
    """Get stored file size and hash for a session.

    Returns:
        Tuple of (file_size, file_hash) or None if not found
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT file_size, file_hash FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row and row["file_size"] is not None:
            return (row["file_size"], row["file_hash"])
        return None


def upsert_session(
    session_id: str,
    project: str,
    machine: str = "local",
    first_message: Optional[str] = None,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    message_count: int = 0,
    file_size: Optional[int] = None,
    file_hash: Optional[str] = None,
    agent: str = "claude",
):
    """Insert or update a session."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO sessions (id, project, machine, first_message, started_at, ended_at, message_count, file_size, file_hash, agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project = excluded.project,
                machine = excluded.machine,
                first_message = excluded.first_message,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                message_count = excluded.message_count,
                file_size = excluded.file_size,
                file_hash = excluded.file_hash,
                agent = excluded.agent
        """, (session_id, project, machine, first_message, started_at, ended_at, message_count, file_size, file_hash, agent))


def delete_session_messages(session_id: str):
    """Delete all messages for a session (before re-importing)."""
    with get_db() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))


def get_message_count(session_id: str) -> int:
    """Get the number of messages for a session."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row["cnt"] if row else 0


def insert_message(
    session_id: str,
    msg_id: str,
    role: str,
    content: str,
    timestamp: str,
):
    """Insert a message."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO messages (session_id, msg_id, role, content, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, msg_id, role, content, timestamp))


def insert_messages_batch(messages: list[tuple]):
    """Insert multiple messages in a batch."""
    with get_db() as conn:
        conn.executemany("""
            INSERT INTO messages (session_id, msg_id, role, content, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, messages)


def get_sessions(
    project: Optional[str] = None,
    machine: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Get sessions with optional filters."""
    with get_db() as conn:
        query = "SELECT * FROM sessions WHERE COALESCE(message_count, 0) > 0"
        params = []

        if project:
            query += " AND project = ?"
            params.append(project)
        if machine:
            query += " AND machine = ?"
            params.append(machine)

        query += " ORDER BY COALESCE(ended_at, started_at, created_at) DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_session(session_id: str) -> Optional[dict]:
    """Get a single session."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def get_session_messages(session_id: str) -> list[dict]:
    """Get all messages for a session."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp",
            (session_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def search_messages(query: str, limit: int = 100, project: str | None = None) -> list[dict]:
    """Full-text search across messages, optionally filtered by project."""
    with get_db() as conn:
        if project:
            rows = conn.execute("""
                SELECT m.*, s.project, s.machine,
                       snippet(messages_fts, 0, '<mark>', '</mark>', '...', 32) as snippet
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                JOIN sessions s ON m.session_id = s.id
                WHERE messages_fts MATCH ? AND s.project = ?
                ORDER BY rank
                LIMIT ?
            """, (query, project, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT m.*, s.project, s.machine,
                       snippet(messages_fts, 0, '<mark>', '</mark>', '...', 32) as snippet
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.id
                JOIN sessions s ON m.session_id = s.id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit)).fetchall()
        return [dict(row) for row in rows]


def get_projects() -> list[str]:
    """Get list of unique projects."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT project FROM sessions ORDER BY project"
        ).fetchall()
        return [row["project"] for row in rows]


def get_machines() -> list[str]:
    """Get list of unique machines."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT machine FROM sessions ORDER BY machine"
        ).fetchall()
        return [row["machine"] for row in rows]


def get_stats() -> dict:
    """Get database statistics."""
    with get_db() as conn:
        sessions = conn.execute("SELECT COUNT(*) as count FROM sessions").fetchone()["count"]
        messages = conn.execute("SELECT COUNT(*) as count FROM messages").fetchone()["count"]
        projects = conn.execute("SELECT COUNT(DISTINCT project) as count FROM sessions").fetchone()["count"]
        machines = conn.execute("SELECT COUNT(DISTINCT machine) as count FROM sessions").fetchone()["count"]

        return {
            "sessions": sessions,
            "messages": messages,
            "projects": projects,
            "machines": machines,
        }
