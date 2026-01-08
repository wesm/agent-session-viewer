// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod db;
mod parser;
mod sync;

use commands::AppState;
use db::Database;
use std::sync::Arc;

fn main() {
    // Initialize data directory
    let data_dir = sync::data_dir();
    std::fs::create_dir_all(&data_dir).expect("Failed to create data directory");

    // Open database (use separate file from Python version to avoid schema conflicts)
    let db_path = data_dir.join("sessions-tauri.db");
    let db = Database::open(&db_path).expect("Failed to open database");

    // Initial sync
    println!("Running initial sync...");
    let stats = sync::sync_all(&db, "local");
    println!(
        "Synced {} sessions ({} new, {} unchanged)",
        stats.total_sessions, stats.synced, stats.skipped
    );

    let state = AppState { db: Arc::new(db) };

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            commands::get_sessions,
            commands::get_messages,
            commands::search,
            commands::get_projects,
            commands::trigger_sync,
            commands::check_session_update,
            commands::sync_session,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
