//! SQLite database with FTS5 full-text search.

use rusqlite::{params, Connection, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Mutex;

/// Session metadata stored in the database.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub session_id: String,
    pub project: String,
    pub machine: String,
    pub first_message: Option<String>,
    pub started_at: Option<String>,
    pub ended_at: Option<String>,
    pub message_count: i32,
    pub file_size: Option<i64>,
    pub file_hash: Option<String>,
    pub agent: String,
}

/// Message stored in the database.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub msg_id: String,
    pub session_id: String,
    pub role: String,
    pub content: String,
    pub timestamp: String,
}

/// Search result from FTS query.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub session_id: String,
    pub msg_id: String,
    pub role: String,
    pub content: String,
    pub timestamp: String,
    pub project: String,
    pub snippet: String,
}

/// Thread-safe database handle.
pub struct Database {
    conn: Mutex<Connection>,
}

impl Database {
    /// Open or create the database at the given path.
    pub fn open(path: &PathBuf) -> Result<Self> {
        let conn = Connection::open(path)?;
        let db = Self {
            conn: Mutex::new(conn),
        };
        db.init_schema()?;
        Ok(db)
    }

    /// Initialize the database schema.
    fn init_schema(&self) -> Result<()> {
        let conn = self.conn.lock().unwrap();

        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                machine TEXT DEFAULT 'local',
                first_message TEXT,
                started_at TEXT,
                ended_at TEXT,
                message_count INTEGER DEFAULT 0,
                file_size INTEGER,
                file_hash TEXT,
                agent TEXT DEFAULT 'claude'
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
            CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                msg_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                timestamp TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                msg_id,
                session_id,
                content='messages',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content, msg_id, session_id)
                VALUES (NEW.id, NEW.content, NEW.msg_id, NEW.session_id);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content, msg_id, session_id)
                VALUES ('delete', OLD.id, OLD.content, OLD.msg_id, OLD.session_id);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content, msg_id, session_id)
                VALUES ('delete', OLD.id, OLD.content, OLD.msg_id, OLD.session_id);
                INSERT INTO messages_fts(rowid, content, msg_id, session_id)
                VALUES (NEW.id, NEW.content, NEW.msg_id, NEW.session_id);
            END;
            "#,
        )?;

        Ok(())
    }

    /// Get all sessions, optionally filtered by project.
    pub fn get_sessions(&self, project: Option<&str>, limit: i32) -> Result<Vec<Session>> {
        let conn = self.conn.lock().unwrap();

        fn row_to_session(row: &rusqlite::Row) -> rusqlite::Result<Session> {
            Ok(Session {
                session_id: row.get(0)?,
                project: row.get(1)?,
                machine: row.get(2)?,
                first_message: row.get(3)?,
                started_at: row.get(4)?,
                ended_at: row.get(5)?,
                message_count: row.get(6)?,
                file_size: row.get(7)?,
                file_hash: row.get(8)?,
                agent: row.get::<_, Option<String>>(9)?.unwrap_or_else(|| "claude".to_string()),
            })
        }

        if let Some(p) = project {
            let mut stmt = conn.prepare(
                "SELECT session_id, project, machine, first_message, started_at, ended_at,
                        COALESCE(message_count, 0), file_size, file_hash, agent
                 FROM sessions
                 WHERE project = ?1 AND COALESCE(message_count, 0) > 0
                 ORDER BY started_at DESC
                 LIMIT ?2"
            )?;
            let result: Vec<_> = stmt.query_map(params![p, limit], row_to_session)?.collect();
            result.into_iter().collect()
        } else {
            let mut stmt = conn.prepare(
                "SELECT session_id, project, machine, first_message, started_at, ended_at,
                        COALESCE(message_count, 0), file_size, file_hash, agent
                 FROM sessions
                 WHERE COALESCE(message_count, 0) > 0
                 ORDER BY started_at DESC
                 LIMIT ?1"
            )?;
            let result: Vec<_> = stmt.query_map(params![limit], row_to_session)?.collect();
            result.into_iter().collect()
        }
    }

    /// Get messages for a session.
    pub fn get_messages(&self, session_id: &str) -> Result<Vec<Message>> {
        let conn = self.conn.lock().unwrap();

        let mut stmt = conn.prepare(
            "SELECT msg_id, session_id, role, content, timestamp
             FROM messages
             WHERE session_id = ?1
             ORDER BY timestamp ASC",
        )?;

        let rows = stmt.query_map(params![session_id], |row| {
            Ok(Message {
                msg_id: row.get(0)?,
                session_id: row.get(1)?,
                role: row.get(2)?,
                content: row.get(3)?,
                timestamp: row.get(4)?,
            })
        })?;

        rows.collect()
    }

    /// Search messages using FTS5.
    pub fn search(&self, query: &str, limit: i32) -> Result<Vec<SearchResult>> {
        let conn = self.conn.lock().unwrap();

        let mut stmt = conn.prepare(
            r#"
            SELECT m.session_id, m.msg_id, m.role, m.content, m.timestamp, s.project,
                   snippet(messages_fts, 0, '<mark>', '</mark>', '...', 32) as snippet
            FROM messages_fts
            JOIN messages m ON messages_fts.rowid = m.id
            JOIN sessions s ON m.session_id = s.session_id
            WHERE messages_fts MATCH ?1
            ORDER BY rank
            LIMIT ?2
            "#,
        )?;

        let rows = stmt.query_map(params![query, limit], |row| {
            Ok(SearchResult {
                session_id: row.get(0)?,
                msg_id: row.get(1)?,
                role: row.get(2)?,
                content: row.get(3)?,
                timestamp: row.get(4)?,
                project: row.get(5)?,
                snippet: row.get(6)?,
            })
        })?;

        rows.collect()
    }

    /// Insert or update a session.
    pub fn upsert_session(&self, session: &Session) -> Result<()> {
        let conn = self.conn.lock().unwrap();

        conn.execute(
            r#"
            INSERT INTO sessions (session_id, project, machine, first_message, started_at,
                                  ended_at, message_count, file_size, file_hash, agent)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
            ON CONFLICT(session_id) DO UPDATE SET
                project = excluded.project,
                machine = excluded.machine,
                first_message = excluded.first_message,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                message_count = excluded.message_count,
                file_size = excluded.file_size,
                file_hash = excluded.file_hash,
                agent = excluded.agent
            "#,
            params![
                session.session_id,
                session.project,
                session.machine,
                session.first_message,
                session.started_at,
                session.ended_at,
                session.message_count,
                session.file_size,
                session.file_hash,
                session.agent,
            ],
        )?;

        Ok(())
    }

    /// Delete messages for a session (before re-indexing).
    pub fn delete_session_messages(&self, session_id: &str) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("DELETE FROM messages WHERE session_id = ?1", params![session_id])?;
        Ok(())
    }

    /// Insert messages in batch.
    pub fn insert_messages(&self, messages: &[Message]) -> Result<()> {
        let conn = self.conn.lock().unwrap();

        let mut stmt = conn.prepare(
            "INSERT INTO messages (session_id, msg_id, role, content, timestamp)
             VALUES (?1, ?2, ?3, ?4, ?5)",
        )?;

        for msg in messages {
            stmt.execute(params![
                msg.session_id,
                msg.msg_id,
                msg.role,
                msg.content,
                msg.timestamp,
            ])?;
        }

        Ok(())
    }

    /// Get file info for incremental sync check.
    pub fn get_session_file_info(&self, session_id: &str) -> Result<Option<(i64, String)>> {
        let conn = self.conn.lock().unwrap();

        let mut stmt = conn.prepare(
            "SELECT file_size, file_hash FROM sessions WHERE session_id = ?1",
        )?;

        let result = stmt.query_row(params![session_id], |row| {
            let size: Option<i64> = row.get(0)?;
            let hash: Option<String> = row.get(1)?;
            Ok(size.zip(hash))
        });

        match result {
            Ok(Some((size, hash))) => Ok(Some((size, hash))),
            Ok(None) => Ok(None),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(e),
        }
    }

    /// Get list of unique projects.
    pub fn get_projects(&self) -> Result<Vec<String>> {
        let conn = self.conn.lock().unwrap();

        let mut stmt = conn.prepare(
            "SELECT DISTINCT project FROM sessions ORDER BY project",
        )?;

        let rows = stmt.query_map([], |row| row.get(0))?;
        rows.collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    struct TestDb {
        db: Database,
        _dir: TempDir,  // Keep tempdir alive
    }

    fn create_test_db() -> TestDb {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let db = Database::open(&db_path).unwrap();
        TestDb { db, _dir: dir }
    }

    fn sample_session(id: &str, project: &str, message_count: i32) -> Session {
        Session {
            session_id: id.to_string(),
            project: project.to_string(),
            machine: "local".to_string(),
            first_message: Some("Test message".to_string()),
            started_at: Some("2026-01-08T10:00:00Z".to_string()),
            ended_at: Some("2026-01-08T11:00:00Z".to_string()),
            message_count,
            file_size: Some(1000),
            file_hash: Some("abc123".to_string()),
            agent: "claude".to_string(),
        }
    }

    #[test]
    fn test_filters_zero_message_count() {
        let test_db = create_test_db();
        let db = &test_db.db;
        db.upsert_session(&sample_session("s1", "project1", 0)).unwrap();
        db.upsert_session(&sample_session("s2", "project1", 5)).unwrap();

        let sessions = db.get_sessions(None, 100).unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].session_id, "s2");
    }

    #[test]
    fn test_returns_sessions_with_positive_message_count() {
        let test_db = create_test_db();
        let db = &test_db.db;
        db.upsert_session(&sample_session("s1", "project1", 10)).unwrap();
        db.upsert_session(&sample_session("s2", "project1", 5)).unwrap();
        db.upsert_session(&sample_session("s3", "project1", 1)).unwrap();

        let sessions = db.get_sessions(None, 100).unwrap();
        assert_eq!(sessions.len(), 3);
    }

    #[test]
    fn test_filters_by_project() {
        let test_db = create_test_db();
        let db = &test_db.db;
        db.upsert_session(&sample_session("s1", "project1", 5)).unwrap();
        db.upsert_session(&sample_session("s2", "project2", 5)).unwrap();
        db.upsert_session(&sample_session("s3", "project1", 3)).unwrap();

        let sessions = db.get_sessions(Some("project1"), 100).unwrap();
        assert_eq!(sessions.len(), 2);
        assert!(sessions.iter().all(|s| s.project == "project1"));
    }

    #[test]
    fn test_respects_limit() {
        let test_db = create_test_db();
        let db = &test_db.db;
        for i in 0..10 {
            db.upsert_session(&sample_session(&format!("s{}", i), "project1", 5)).unwrap();
        }

        let sessions = db.get_sessions(None, 3).unwrap();
        assert_eq!(sessions.len(), 3);
    }

    #[test]
    fn test_empty_database() {
        let test_db = create_test_db();
        let db = &test_db.db;
        let sessions = db.get_sessions(None, 100).unwrap();
        assert!(sessions.is_empty());
    }

    #[test]
    fn test_insert_and_get_messages() {
        let test_db = create_test_db();
        let db = &test_db.db;
        db.upsert_session(&sample_session("s1", "project1", 2)).unwrap();

        let messages = vec![
            Message {
                msg_id: "m1".to_string(),
                session_id: "s1".to_string(),
                role: "user".to_string(),
                content: "Hello".to_string(),
                timestamp: "2026-01-08T10:00:00Z".to_string(),
            },
            Message {
                msg_id: "m2".to_string(),
                session_id: "s1".to_string(),
                role: "assistant".to_string(),
                content: "Hi there".to_string(),
                timestamp: "2026-01-08T10:01:00Z".to_string(),
            },
        ];
        db.insert_messages(&messages).unwrap();

        let retrieved = db.get_messages("s1").unwrap();
        assert_eq!(retrieved.len(), 2);
        assert_eq!(retrieved[0].content, "Hello");
        assert_eq!(retrieved[1].content, "Hi there");
    }

    #[test]
    fn test_full_text_search() {
        let test_db = create_test_db();
        let db = &test_db.db;
        db.upsert_session(&sample_session("s1", "project1", 2)).unwrap();

        let messages = vec![
            Message {
                msg_id: "m1".to_string(),
                session_id: "s1".to_string(),
                role: "user".to_string(),
                content: "How do I implement authentication?".to_string(),
                timestamp: "2026-01-08T10:00:00Z".to_string(),
            },
            Message {
                msg_id: "m2".to_string(),
                session_id: "s1".to_string(),
                role: "assistant".to_string(),
                content: "You can use JWT tokens for authentication".to_string(),
                timestamp: "2026-01-08T10:01:00Z".to_string(),
            },
        ];
        db.insert_messages(&messages).unwrap();

        let results = db.search("authentication", 10).unwrap();
        assert_eq!(results.len(), 2);

        let results = db.search("JWT tokens", 10).unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].role, "assistant");
    }

    #[test]
    fn test_delete_session_messages() {
        let test_db = create_test_db();
        let db = &test_db.db;
        db.upsert_session(&sample_session("s1", "project1", 1)).unwrap();

        let messages = vec![Message {
            msg_id: "m1".to_string(),
            session_id: "s1".to_string(),
            role: "user".to_string(),
            content: "Test".to_string(),
            timestamp: "2026-01-08T10:00:00Z".to_string(),
        }];
        db.insert_messages(&messages).unwrap();

        assert_eq!(db.get_messages("s1").unwrap().len(), 1);

        db.delete_session_messages("s1").unwrap();
        assert_eq!(db.get_messages("s1").unwrap().len(), 0);
    }

    #[test]
    fn test_get_session_file_info() {
        let test_db = create_test_db();
        let db = &test_db.db;
        db.upsert_session(&sample_session("s1", "project1", 5)).unwrap();

        let info = db.get_session_file_info("s1").unwrap();
        assert!(info.is_some());
        let (size, hash) = info.unwrap();
        assert_eq!(size, 1000);
        assert_eq!(hash, "abc123");

        let info = db.get_session_file_info("nonexistent").unwrap();
        assert!(info.is_none());
    }

    #[test]
    fn test_get_projects() {
        let test_db = create_test_db();
        let db = &test_db.db;
        db.upsert_session(&sample_session("s1", "alpha", 5)).unwrap();
        db.upsert_session(&sample_session("s2", "beta", 5)).unwrap();
        db.upsert_session(&sample_session("s3", "alpha", 3)).unwrap();

        let projects = db.get_projects().unwrap();
        assert_eq!(projects, vec!["alpha", "beta"]);
    }

    #[test]
    fn test_upsert_updates_existing() {
        let test_db = create_test_db();
        let db = &test_db.db;

        let mut session = sample_session("s1", "project1", 5);
        db.upsert_session(&session).unwrap();

        session.message_count = 10;
        session.first_message = Some("Updated message".to_string());
        db.upsert_session(&session).unwrap();

        let sessions = db.get_sessions(None, 100).unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].message_count, 10);
        assert_eq!(sessions[0].first_message, Some("Updated message".to_string()));
    }
}
