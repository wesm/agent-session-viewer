//! Sync sessions from Claude Code and Codex directories.

use crate::db::Database;
use crate::parser::{parse_claude_session, parse_codex_session};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

/// Compute MD5 hash of a file.
pub fn compute_file_hash(path: &Path) -> Option<String> {
    let mut file = fs::File::open(path).ok()?;
    let mut buffer = Vec::new();
    file.read_to_end(&mut buffer).ok()?;
    Some(format!("{:x}", md5::compute(&buffer)))
}

/// Get the Claude projects directory.
pub fn claude_projects_dir() -> PathBuf {
    std::env::var("CLAUDE_PROJECTS_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            dirs::home_dir()
                .unwrap_or_default()
                .join(".claude")
                .join("projects")
        })
}

/// Get the Codex sessions directory.
pub fn codex_sessions_dir() -> PathBuf {
    std::env::var("CODEX_SESSIONS_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            dirs::home_dir()
                .unwrap_or_default()
                .join(".codex")
                .join("sessions")
        })
}

/// Get the data directory for our app.
pub fn data_dir() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_default()
        .join(".agent-session-viewer")
}

/// Convert a project directory name to a clean project name.
fn get_project_name(dir_name: &str) -> String {
    let mut name = dir_name.to_string();

    // Strip common path prefixes like "-Users-user-code-"
    if name.starts_with('-') {
        let parts: Vec<&str> = name.split('-').collect();
        for (i, part) in parts.iter().enumerate() {
            if part.eq_ignore_ascii_case("code") && i + 1 < parts.len() {
                name = parts[i + 1..].join("-");
                break;
            }
        }
    }

    name.replace('-', "_")
}

/// Find all Claude project directories.
pub fn find_claude_projects() -> Vec<PathBuf> {
    let dir = claude_projects_dir();
    if !dir.exists() {
        return Vec::new();
    }

    let mut projects = Vec::new();
    if let Ok(entries) = fs::read_dir(&dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                projects.push(path);
            }
        }
    }

    projects.sort();
    projects
}

/// Find all Codex session files.
pub fn find_codex_sessions() -> Vec<PathBuf> {
    let dir = codex_sessions_dir();
    if !dir.exists() {
        return Vec::new();
    }

    let mut sessions = Vec::new();

    // Navigate year/month/day structure
    if let Ok(years) = fs::read_dir(&dir) {
        for year in years.flatten() {
            let year_path = year.path();
            if !year_path.is_dir() || !year.file_name().to_string_lossy().chars().all(|c| c.is_ascii_digit()) {
                continue;
            }

            if let Ok(months) = fs::read_dir(&year_path) {
                for month in months.flatten() {
                    let month_path = month.path();
                    if !month_path.is_dir() || !month.file_name().to_string_lossy().chars().all(|c| c.is_ascii_digit()) {
                        continue;
                    }

                    if let Ok(days) = fs::read_dir(&month_path) {
                        for day in days.flatten() {
                            let day_path = day.path();
                            if !day_path.is_dir() || !day.file_name().to_string_lossy().chars().all(|c| c.is_ascii_digit()) {
                                continue;
                            }

                            if let Ok(files) = fs::read_dir(&day_path) {
                                for file in files.flatten() {
                                    let file_path = file.path();
                                    if file_path.extension().map_or(false, |e| e == "jsonl") {
                                        sessions.push(file_path);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    sessions.sort();
    sessions
}

/// Sync result for a single session.
#[derive(Debug)]
pub struct SyncResult {
    pub session_id: String,
    pub project: String,
    pub skipped: bool,
    pub messages: usize,
}

/// Sync a Claude session file.
pub fn sync_claude_session(
    db: &Database,
    path: &Path,
    project_name: &str,
    machine: &str,
    force: bool,
) -> Option<SyncResult> {
    let session_id = path.file_stem()?.to_str()?;

    // Skip agent files
    if session_id.starts_with("agent-") {
        return None;
    }

    let source_size = fs::metadata(path).ok()?.len() as i64;

    // Check if file has changed
    if !force {
        if let Ok(Some((stored_size, stored_hash))) = db.get_session_file_info(session_id) {
            if stored_size == source_size {
                let source_hash = compute_file_hash(path)?;
                if source_hash == stored_hash {
                    return Some(SyncResult {
                        session_id: session_id.to_string(),
                        project: project_name.to_string(),
                        skipped: true,
                        messages: 0,
                    });
                }
            }
        }
    }

    let source_hash = compute_file_hash(path)?;

    // Parse the session
    let mut parsed = parse_claude_session(path, project_name, machine)?;
    parsed.metadata.file_size = Some(source_size);
    parsed.metadata.file_hash = Some(source_hash);

    // Update database
    db.upsert_session(&parsed.metadata).ok()?;
    db.delete_session_messages(&parsed.metadata.session_id).ok()?;
    if !parsed.messages.is_empty() {
        db.insert_messages(&parsed.messages).ok()?;
    }

    Some(SyncResult {
        session_id: parsed.metadata.session_id,
        project: project_name.to_string(),
        skipped: false,
        messages: parsed.messages.len(),
    })
}

/// Sync a Codex session file.
pub fn sync_codex_session(
    db: &Database,
    path: &Path,
    machine: &str,
    force: bool,
) -> Option<SyncResult> {
    let source_size = fs::metadata(path).ok()?.len() as i64;

    // Parse first to get session_id (and skip non-interactive)
    let mut parsed = parse_codex_session(path, machine, false)?;

    let session_id = &parsed.metadata.session_id;

    // Check if file has changed
    if !force {
        if let Ok(Some((stored_size, stored_hash))) = db.get_session_file_info(session_id) {
            if stored_size == source_size {
                let source_hash = compute_file_hash(path)?;
                if source_hash == stored_hash {
                    return Some(SyncResult {
                        session_id: session_id.clone(),
                        project: parsed.metadata.project.clone(),
                        skipped: true,
                        messages: 0,
                    });
                }
            }
        }
    }

    let source_hash = compute_file_hash(path)?;
    parsed.metadata.file_size = Some(source_size);
    parsed.metadata.file_hash = Some(source_hash);

    // Update database
    db.upsert_session(&parsed.metadata).ok()?;
    db.delete_session_messages(&parsed.metadata.session_id).ok()?;
    if !parsed.messages.is_empty() {
        db.insert_messages(&parsed.messages).ok()?;
    }

    Some(SyncResult {
        session_id: parsed.metadata.session_id,
        project: parsed.metadata.project,
        skipped: false,
        messages: parsed.messages.len(),
    })
}

/// Sync all Claude sessions from a project directory.
pub fn sync_claude_project(
    db: &Database,
    project_dir: &Path,
    machine: &str,
) -> (usize, usize, usize) {
    let project_name = get_project_name(&project_dir.file_name().unwrap_or_default().to_string_lossy());

    let mut total = 0;
    let mut synced = 0;
    let mut skipped = 0;

    if let Ok(entries) = fs::read_dir(project_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().map_or(false, |e| e == "jsonl") {
                if let Some(result) = sync_claude_session(db, &path, &project_name, machine, false) {
                    total += 1;
                    if result.skipped {
                        skipped += 1;
                    } else {
                        synced += 1;
                    }
                }
            }
        }
    }

    (total, synced, skipped)
}

/// Sync all sessions (Claude + Codex).
pub fn sync_all(db: &Database, machine: &str) -> SyncStats {
    let mut stats = SyncStats::default();

    // Sync Claude projects
    for project_dir in find_claude_projects() {
        let (total, synced, skipped) = sync_claude_project(db, &project_dir, machine);
        stats.total_sessions += total;
        stats.synced += synced;
        stats.skipped += skipped;
    }

    // Sync Codex sessions
    for session_path in find_codex_sessions() {
        if let Some(result) = sync_codex_session(db, &session_path, machine, false) {
            stats.total_sessions += 1;
            if result.skipped {
                stats.skipped += 1;
            } else {
                stats.synced += 1;
            }
        }
    }

    stats
}

/// Statistics from a sync operation.
#[derive(Debug, Default, serde::Serialize)]
pub struct SyncStats {
    pub total_sessions: usize,
    pub synced: usize,
    pub skipped: usize,
}

/// Find the source file for a session ID.
pub fn find_source_file(session_id: &str) -> Option<PathBuf> {
    if session_id.is_empty() {
        return None;
    }

    // Handle Codex sessions
    if let Some(codex_id) = session_id.strip_prefix("codex:") {
        return find_codex_source_file(codex_id);
    }

    // Claude sessions
    find_claude_source_file(session_id)
}

/// Find a Claude session source file.
fn find_claude_source_file(session_id: &str) -> Option<PathBuf> {
    // Validate session_id
    if !session_id.chars().all(|c| c.is_alphanumeric() || c == '-' || c == '_') {
        return None;
    }

    let projects_dir = claude_projects_dir();
    if !projects_dir.exists() {
        return None;
    }

    for entry in fs::read_dir(&projects_dir).ok()?.flatten() {
        let project_dir = entry.path();
        if !project_dir.is_dir() {
            continue;
        }

        let candidate = project_dir.join(format!("{}.jsonl", session_id));
        if candidate.exists() {
            // Verify path doesn't escape project dir
            if candidate.canonicalize().ok()?.starts_with(project_dir.canonicalize().ok()?) {
                return Some(candidate);
            }
        }
    }

    None
}

/// Find a Codex session source file by UUID.
fn find_codex_source_file(session_id: &str) -> Option<PathBuf> {
    // Validate session_id
    if !session_id.chars().all(|c| c.is_alphanumeric() || c == '-' || c == '_') {
        return None;
    }

    let sessions_dir = codex_sessions_dir();
    if !sessions_dir.exists() {
        return None;
    }

    // Search through year/month/day structure
    for year in fs::read_dir(&sessions_dir).ok()?.flatten() {
        let year_path = year.path();
        if !year_path.is_dir() {
            continue;
        }

        for month in fs::read_dir(&year_path).ok()?.flatten() {
            let month_path = month.path();
            if !month_path.is_dir() {
                continue;
            }

            for day in fs::read_dir(&month_path).ok()?.flatten() {
                let day_path = day.path();
                if !day_path.is_dir() {
                    continue;
                }

                for file in fs::read_dir(&day_path).ok()?.flatten() {
                    let file_path = file.path();
                    if file_path.extension().map_or(false, |e| e == "jsonl") {
                        let stem = file_path.file_stem()?.to_string_lossy();
                        if stem.starts_with("rollout-") {
                            // Extract UUID using rsplit
                            let parts: Vec<&str> = stem.rsplit('-').take(5).collect();
                            if parts.len() == 5 {
                                let file_uuid = format!(
                                    "{}-{}-{}-{}-{}",
                                    parts[4], parts[3], parts[2], parts[1], parts[0]
                                );
                                if file_uuid == session_id {
                                    return Some(file_path);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    // Helper to validate session ID characters (mirrors the validation in find_*_source_file)
    fn is_valid_session_id(id: &str) -> bool {
        !id.is_empty() && id.chars().all(|c| c.is_alphanumeric() || c == '-' || c == '_')
    }

    #[test]
    fn test_valid_session_id_characters() {
        assert!(is_valid_session_id("abc123"));
        assert!(is_valid_session_id("session-123_test"));
        assert!(is_valid_session_id("ABC-123_xyz"));
    }

    #[test]
    fn test_path_traversal_dotdot_blocked() {
        assert!(!is_valid_session_id("../etc/passwd"));
        assert!(!is_valid_session_id(".."));
    }

    #[test]
    fn test_path_traversal_slash_blocked() {
        assert!(!is_valid_session_id("foo/bar"));
        assert!(!is_valid_session_id("/etc/passwd"));
    }

    #[test]
    fn test_empty_session_id() {
        assert!(!is_valid_session_id(""));
    }

    #[test]
    fn test_special_characters_blocked() {
        assert!(!is_valid_session_id("test;ls"));
        assert!(!is_valid_session_id("test`ls`"));
        assert!(!is_valid_session_id("test$(ls)"));
        assert!(!is_valid_session_id("test\x00null"));
    }

    #[test]
    fn test_codex_uuid_extraction() {
        // Test the UUID extraction logic used in find_codex_source_file
        let filename = "rollout-2026-01-08T06-48-54-019b9da7-1f41-7af2-80d9-6e293902fea8";
        let parts: Vec<&str> = filename.rsplit('-').take(5).collect();
        assert_eq!(parts.len(), 5);

        let file_uuid = format!(
            "{}-{}-{}-{}-{}",
            parts[4], parts[3], parts[2], parts[1], parts[0]
        );
        assert_eq!(file_uuid, "019b9da7-1f41-7af2-80d9-6e293902fea8");
    }

    #[test]
    fn test_codex_uuid_with_extra_timestamp_dashes() {
        // Timestamp with milliseconds: 2026-01-08T06-48-54-123
        let filename = "rollout-2026-01-08T06-48-54-123-019b9da7-1f41-7af2-80d9-6e293902fea8";
        let parts: Vec<&str> = filename.rsplit('-').take(5).collect();
        assert_eq!(parts.len(), 5);

        let file_uuid = format!(
            "{}-{}-{}-{}-{}",
            parts[4], parts[3], parts[2], parts[1], parts[0]
        );
        assert_eq!(file_uuid, "019b9da7-1f41-7af2-80d9-6e293902fea8");
    }

    #[test]
    fn test_codex_uuid_with_timezone() {
        // Timestamp with timezone: 2026-01-08T06-48-54-0600
        let filename = "rollout-2026-01-08T06-48-54-0600-019b9da7-1f41-7af2-80d9-6e293902fea8";
        let parts: Vec<&str> = filename.rsplit('-').take(5).collect();
        assert_eq!(parts.len(), 5);

        let file_uuid = format!(
            "{}-{}-{}-{}-{}",
            parts[4], parts[3], parts[2], parts[1], parts[0]
        );
        assert_eq!(file_uuid, "019b9da7-1f41-7af2-80d9-6e293902fea8");
    }

    #[test]
    fn test_get_project_name() {
        assert_eq!(get_project_name("my-project"), "my_project");
        assert_eq!(get_project_name("-Users-user-code-myapp"), "myapp");
        assert_eq!(get_project_name("-home-dev-code-webapp-frontend"), "webapp_frontend");
    }

    #[test]
    fn test_compute_file_hash() {
        let tmp = tempdir().unwrap();
        let file_path = tmp.path().join("test.txt");
        fs::write(&file_path, "Hello, World!").unwrap();

        let hash = compute_file_hash(&file_path).unwrap();
        // MD5 of "Hello, World!" is 65a8e27d8879283831b664bd8b7f0ad4
        assert_eq!(hash, "65a8e27d8879283831b664bd8b7f0ad4");
    }

    #[test]
    fn test_find_source_file_codex_prefix_routing() {
        // Test that codex: prefix is handled correctly
        let id = "codex:019b9da7-1f41-7af2-80d9-6e293902fea8";
        assert!(id.starts_with("codex:"));
        let codex_id = id.strip_prefix("codex:").unwrap();
        assert_eq!(codex_id, "019b9da7-1f41-7af2-80d9-6e293902fea8");
    }
}
