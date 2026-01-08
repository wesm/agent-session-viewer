//! Tauri commands - the API layer between frontend and backend.

use crate::db::{Database, Message, SearchResult, Session};
use crate::sync::{self, SyncStats};
use std::sync::Arc;
use tauri::State;

/// Application state containing the database.
pub struct AppState {
    pub db: Arc<Database>,
}

/// Get all sessions.
#[tauri::command]
pub fn get_sessions(
    state: State<AppState>,
    project: Option<String>,
    limit: Option<i32>,
) -> Result<Vec<Session>, String> {
    let limit = limit.unwrap_or(500);
    state
        .db
        .get_sessions(project.as_deref(), limit)
        .map_err(|e| e.to_string())
}

/// Get messages for a session.
#[tauri::command]
pub fn get_messages(state: State<AppState>, session_id: String) -> Result<Vec<Message>, String> {
    state
        .db
        .get_messages(&session_id)
        .map_err(|e| e.to_string())
}

/// Search messages.
#[tauri::command]
pub fn search(
    state: State<AppState>,
    query: String,
    limit: Option<i32>,
) -> Result<Vec<SearchResult>, String> {
    let limit = limit.unwrap_or(100);
    state.db.search(&query, limit).map_err(|e| e.to_string())
}

/// Get list of projects.
#[tauri::command]
pub fn get_projects(state: State<AppState>) -> Result<Vec<String>, String> {
    state.db.get_projects().map_err(|e| e.to_string())
}

/// Trigger a sync operation.
#[tauri::command]
pub fn trigger_sync(state: State<AppState>) -> Result<SyncStats, String> {
    Ok(sync::sync_all(&state.db, "local"))
}

/// Check if a session's source file has been modified.
#[tauri::command]
pub fn check_session_update(
    state: State<AppState>,
    session_id: String,
) -> Result<bool, String> {
    // Find source file
    let source_path = match sync::find_source_file(&session_id) {
        Some(p) => p,
        None => return Ok(false),
    };

    // Get current file size
    let source_size = std::fs::metadata(&source_path)
        .map(|m| m.len() as i64)
        .map_err(|e| e.to_string())?;

    // Check against stored info
    if let Ok(Some((stored_size, stored_hash))) = state.db.get_session_file_info(&session_id) {
        if stored_size != source_size {
            return Ok(true); // Size changed
        }
        // Check hash
        if let Some(source_hash) = sync::compute_file_hash(&source_path) {
            if source_hash != stored_hash {
                return Ok(true); // Hash changed
            }
        }
    } else {
        // No stored info means it's new
        return Ok(true);
    }

    Ok(false)
}

/// Sync a single session and return updated data.
#[tauri::command]
pub fn sync_session(
    state: State<AppState>,
    session_id: String,
) -> Result<Option<Session>, String> {
    // Find source file
    let source_path = match sync::find_source_file(&session_id) {
        Some(p) => p,
        None => return Ok(None),
    };

    // Determine if it's Claude or Codex
    if session_id.starts_with("codex:") {
        sync::sync_codex_session(&state.db, &source_path, "local", true);
    } else {
        // Get project name from path
        let project_name = source_path
            .parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            .unwrap_or("unknown");
        sync::sync_claude_session(&state.db, &source_path, project_name, "local", true);
    }

    // Return updated session
    let sessions = state
        .db
        .get_sessions(None, 1000)
        .map_err(|e| e.to_string())?;

    Ok(sessions.into_iter().find(|s| s.session_id == session_id))
}
