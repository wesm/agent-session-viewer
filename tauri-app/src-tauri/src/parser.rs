//! Parse Claude Code and Codex JSONL session files.

use crate::db::{Message, Session};
use chrono::{DateTime, Utc};
use serde_json::Value;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

/// Parsed session result.
pub struct ParsedSession {
    pub metadata: Session,
    pub messages: Vec<Message>,
}

/// Parse a timestamp string to ISO format.
fn parse_timestamp(ts: &str) -> Option<DateTime<Utc>> {
    // Handle various ISO formats
    DateTime::parse_from_rfc3339(ts)
        .map(|dt| dt.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            // Try without timezone
            chrono::NaiveDateTime::parse_from_str(ts, "%Y-%m-%dT%H:%M:%S%.f")
                .ok()
                .map(|dt| dt.and_utc())
        })
}

/// Create a message ID from timestamp.
fn make_msg_id(ts: &str, index: usize) -> String {
    if ts.is_empty() {
        format!("msg-{}", index)
    } else {
        format!("msg-{}", ts.replace([':', '.'], "-"))
    }
}

/// Extract text content from Claude message content (string or array of blocks).
fn extract_text_content(content: &Value, include_tools: bool) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(blocks) => {
            let mut texts = Vec::new();
            for block in blocks {
                if let Value::Object(obj) = block {
                    let block_type = obj.get("type").and_then(|v| v.as_str()).unwrap_or("");
                    match block_type {
                        "text" => {
                            if let Some(text) = obj.get("text").and_then(|v| v.as_str()) {
                                texts.push(text.to_string());
                            }
                        }
                        "thinking" => {
                            if let Some(thinking) = obj.get("thinking").and_then(|v| v.as_str()) {
                                texts.push(format!("[Thinking]\n{}", thinking));
                            }
                        }
                        "tool_use" if include_tools => {
                            texts.push(format_tool_use(obj));
                        }
                        _ => {}
                    }
                }
            }
            texts.join("\n")
        }
        _ => String::new(),
    }
}

/// Format a tool_use block for display.
fn format_tool_use(block: &serde_json::Map<String, Value>) -> String {
    let tool_name = block
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let input = block.get("input").cloned().unwrap_or(Value::Object(Default::default()));

    match tool_name {
        "Read" => {
            let path = input.get("file_path").and_then(|v| v.as_str()).unwrap_or("unknown");
            format!("[Read: {}]", path)
        }
        "Edit" => {
            let path = input.get("file_path").and_then(|v| v.as_str()).unwrap_or("unknown");
            format!("[Edit: {}]", path)
        }
        "Write" => {
            let path = input.get("file_path").and_then(|v| v.as_str()).unwrap_or("unknown");
            format!("[Write: {}]", path)
        }
        "Bash" => {
            let cmd = input.get("command").and_then(|v| v.as_str()).unwrap_or("");
            let desc = input.get("description").and_then(|v| v.as_str());
            if let Some(d) = desc {
                format!("[Bash: {}]\n$ {}", d, cmd)
            } else {
                format!("[Bash]\n$ {}", cmd)
            }
        }
        "Glob" => {
            let pattern = input.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
            let path = input.get("path").and_then(|v| v.as_str()).unwrap_or(".");
            format!("[Glob: {} in {}]", pattern, path)
        }
        "Grep" => {
            let pattern = input.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
            format!("[Grep: {}]", pattern)
        }
        "Task" => {
            let desc = input.get("description").and_then(|v| v.as_str()).unwrap_or("");
            let agent = input.get("subagent_type").and_then(|v| v.as_str()).unwrap_or("");
            format!("[Task: {} ({})]", desc, agent)
        }
        "TodoWrite" => {
            let todos = input.get("todos").and_then(|v| v.as_array());
            let mut lines = vec!["[Todo List]".to_string()];
            if let Some(todos) = todos {
                for todo in todos {
                    let status = todo.get("status").and_then(|v| v.as_str()).unwrap_or("pending");
                    let content = todo.get("content").and_then(|v| v.as_str()).unwrap_or("");
                    let icon = match status {
                        "completed" => "✓",
                        "in_progress" => "→",
                        _ => "○",
                    };
                    lines.push(format!("  {} {}", icon, content));
                }
            }
            lines.join("\n")
        }
        _ => format!("[Tool: {}]", tool_name),
    }
}

/// Parse a Claude Code session file.
pub fn parse_claude_session(path: &Path, project: &str, machine: &str) -> Option<ParsedSession> {
    let session_id = path.file_stem()?.to_str()?.to_string();

    // Skip agent files
    if session_id.starts_with("agent-") {
        return None;
    }

    let file = File::open(path).ok()?;
    let reader = BufReader::new(file);

    let mut messages = Vec::new();
    let mut first_message: Option<String> = None;
    let mut started_at: Option<DateTime<Utc>> = None;
    let mut ended_at: Option<DateTime<Utc>> = None;

    for line in reader.lines() {
        let line = match line {
            Ok(l) if !l.trim().is_empty() => l,
            _ => continue,
        };

        let entry: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        // Extract timestamp
        let ts_str = entry
            .get("timestamp")
            .or_else(|| entry.get("snapshot").and_then(|s| s.get("timestamp")))
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if let Some(ts) = parse_timestamp(ts_str) {
            if started_at.is_none() {
                started_at = Some(ts);
            }
            ended_at = Some(ts);
        }

        let entry_type = entry.get("type").and_then(|v| v.as_str()).unwrap_or("");

        match entry_type {
            "user" => {
                let msg_data = entry.get("message").unwrap_or(&Value::Null);
                let content_val = msg_data.get("content").unwrap_or(&Value::Null);
                let content = extract_text_content(content_val, true);

                if !content.trim().is_empty() {
                    if first_message.is_none() {
                        let truncated: String = content.chars().take(300).collect();
                        let mut summary = truncated.replace('\n', " ");
                        if content.len() > 300 {
                            summary.push_str("...");
                        }
                        first_message = Some(summary);
                    }

                    messages.push(Message {
                        msg_id: make_msg_id(ts_str, messages.len()),
                        session_id: session_id.clone(),
                        role: "user".to_string(),
                        content,
                        timestamp: ts_str.to_string(),
                    });
                }
            }
            "assistant" => {
                let msg_data = entry.get("message").unwrap_or(&Value::Null);
                let content_val = msg_data.get("content").unwrap_or(&Value::Null);
                let content = extract_text_content(content_val, true);

                if !content.trim().is_empty() {
                    messages.push(Message {
                        msg_id: make_msg_id(ts_str, messages.len()),
                        session_id: session_id.clone(),
                        role: "assistant".to_string(),
                        content,
                        timestamp: ts_str.to_string(),
                    });
                }
            }
            _ => {}
        }
    }

    let metadata = Session {
        session_id,
        project: project.to_string(),
        machine: machine.to_string(),
        first_message,
        started_at: started_at.map(|dt| dt.to_rfc3339()),
        ended_at: ended_at.map(|dt| dt.to_rfc3339()),
        message_count: messages.len() as i32,
        file_size: None,
        file_hash: None,
        agent: "claude".to_string(),
    };

    Some(ParsedSession { metadata, messages })
}

/// Extract project name from Codex cwd path.
fn extract_codex_project(cwd: &str) -> String {
    if cwd.is_empty() {
        return "unknown".to_string();
    }
    Path::new(cwd)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("unknown")
        .to_string()
}

/// Parse a Codex session file.
pub fn parse_codex_session(path: &Path, machine: &str, include_exec: bool) -> Option<ParsedSession> {
    let file = File::open(path).ok()?;
    let reader = BufReader::new(file);

    let mut messages = Vec::new();
    let mut first_message: Option<String> = None;
    let mut started_at: Option<DateTime<Utc>> = None;
    let mut ended_at: Option<DateTime<Utc>> = None;
    let mut session_id: Option<String> = None;
    let mut project = "unknown".to_string();

    for line in reader.lines() {
        let line = match line {
            Ok(l) if !l.trim().is_empty() => l,
            _ => continue,
        };

        let entry: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        let entry_type = entry.get("type").and_then(|v| v.as_str()).unwrap_or("");
        let payload = entry.get("payload").unwrap_or(&Value::Null);
        let ts_str = entry.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");

        if let Some(ts) = parse_timestamp(ts_str) {
            if started_at.is_none() {
                started_at = Some(ts);
            }
            ended_at = Some(ts);
        }

        match entry_type {
            "session_meta" => {
                session_id = payload.get("id").and_then(|v| v.as_str()).map(String::from);
                let cwd = payload.get("cwd").and_then(|v| v.as_str()).unwrap_or("");
                project = extract_codex_project(cwd);

                // Check originator - skip codex_exec unless explicitly included
                let originator = payload.get("originator").and_then(|v| v.as_str()).unwrap_or("");
                if !include_exec && originator == "codex_exec" {
                    return None;
                }
            }
            "response_item" => {
                let role = payload.get("role").and_then(|v| v.as_str()).unwrap_or("");
                if role != "user" && role != "assistant" {
                    continue;
                }

                let content_blocks = payload.get("content").and_then(|v| v.as_array());
                let mut texts = Vec::new();

                if let Some(blocks) = content_blocks {
                    for block in blocks {
                        if let Some(obj) = block.as_object() {
                            let block_type = obj.get("type").and_then(|v| v.as_str()).unwrap_or("");
                            if matches!(block_type, "input_text" | "output_text" | "text") {
                                if let Some(text) = obj.get("text").and_then(|v| v.as_str()) {
                                    if !text.is_empty() {
                                        texts.push(text.to_string());
                                    }
                                }
                            }
                        }
                    }
                }

                let content = texts.join("\n");
                if content.trim().is_empty() {
                    continue;
                }

                // Skip system/instruction messages
                if role == "user"
                    && (content.starts_with("# AGENTS.md")
                        || content.starts_with("<environment_context>")
                        || content.starts_with("<INSTRUCTIONS>"))
                {
                    continue;
                }

                // Capture first user message
                if role == "user" && first_message.is_none() {
                    let truncated: String = content.chars().take(300).collect();
                    let mut summary = truncated.replace('\n', " ");
                    if content.len() > 300 {
                        summary.push_str("...");
                    }
                    first_message = Some(summary);
                }

                messages.push(Message {
                    msg_id: make_msg_id(ts_str, messages.len()),
                    session_id: String::new(), // Will be set below
                    role: role.to_string(),
                    content,
                    timestamp: ts_str.to_string(),
                });
            }
            _ => {}
        }
    }

    // Fallback session_id from filename
    let final_session_id = session_id
        .unwrap_or_else(|| path.file_stem().unwrap_or_default().to_string_lossy().to_string());

    // Prefix with "codex:" to avoid collision
    let prefixed_id = format!("codex:{}", final_session_id);

    // Update message session_ids
    for msg in &mut messages {
        msg.session_id = prefixed_id.clone();
    }

    let metadata = Session {
        session_id: prefixed_id,
        project,
        machine: machine.to_string(),
        first_message,
        started_at: started_at.map(|dt| dt.to_rfc3339()),
        ended_at: ended_at.map(|dt| dt.to_rfc3339()),
        message_count: messages.len() as i32,
        file_size: None,
        file_hash: None,
        agent: "codex".to_string(),
    };

    Some(ParsedSession { metadata, messages })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn test_parse_claude_session_basic() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("test-session.jsonl");

        let content = r#"{"type":"user","timestamp":"2026-01-08T10:00:00Z","message":{"content":"Hello"}}
{"type":"assistant","timestamp":"2026-01-08T10:01:00Z","message":{"content":[{"type":"text","text":"Hi there!"}]}}"#;

        fs::write(&session_file, content).unwrap();

        let result = parse_claude_session(&session_file, "test-project", "local");
        assert!(result.is_some());

        let parsed = result.unwrap();
        assert_eq!(parsed.metadata.session_id, "test-session");
        assert_eq!(parsed.metadata.project, "test-project");
        assert_eq!(parsed.metadata.agent, "claude");
        assert_eq!(parsed.messages.len(), 2);
        assert_eq!(parsed.messages[0].role, "user");
        assert_eq!(parsed.messages[0].content, "Hello");
        assert_eq!(parsed.messages[1].role, "assistant");
        assert_eq!(parsed.messages[1].content, "Hi there!");
    }

    #[test]
    fn test_parse_claude_session_skips_agent_files() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("agent-12345.jsonl");

        let content = r#"{"type":"user","timestamp":"2026-01-08T10:00:00Z","message":{"content":"Hello"}}"#;
        fs::write(&session_file, content).unwrap();

        let result = parse_claude_session(&session_file, "test-project", "local");
        assert!(result.is_none());
    }

    #[test]
    fn test_parse_claude_session_with_tool_use() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("test-session.jsonl");

        let content = r#"{"type":"assistant","timestamp":"2026-01-08T10:00:00Z","message":{"content":[{"type":"text","text":"Let me read that file."},{"type":"tool_use","name":"Read","input":{"file_path":"/path/to/file.txt"}}]}}"#;
        fs::write(&session_file, content).unwrap();

        let result = parse_claude_session(&session_file, "test-project", "local");
        assert!(result.is_some());

        let parsed = result.unwrap();
        assert_eq!(parsed.messages.len(), 1);
        assert!(parsed.messages[0].content.contains("Let me read that file"));
        assert!(parsed.messages[0].content.contains("[Read: /path/to/file.txt]"));
    }

    #[test]
    fn test_parse_codex_session_basic() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("rollout-2026-01-08-abc123.jsonl");

        let content = r#"{"type":"session_meta","timestamp":"2026-01-08T10:00:00Z","payload":{"id":"abc123","cwd":"/home/user/myproject","originator":"codex_cli_rs"}}
{"type":"response_item","timestamp":"2026-01-08T10:01:00Z","payload":{"role":"user","content":[{"type":"input_text","text":"Hello Codex"}]}}
{"type":"response_item","timestamp":"2026-01-08T10:02:00Z","payload":{"role":"assistant","content":[{"type":"output_text","text":"Hello! How can I help?"}]}}"#;

        fs::write(&session_file, content).unwrap();

        let result = parse_codex_session(&session_file, "local", false);
        assert!(result.is_some());

        let parsed = result.unwrap();
        assert_eq!(parsed.metadata.session_id, "codex:abc123");
        assert_eq!(parsed.metadata.project, "myproject");
        assert_eq!(parsed.metadata.agent, "codex");
        assert_eq!(parsed.messages.len(), 2);
    }

    #[test]
    fn test_parse_codex_session_skips_codex_exec_by_default() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("test.jsonl");

        let content = r#"{"type":"session_meta","payload":{"id":"test-id","cwd":"/test","originator":"codex_exec"}}"#;
        fs::write(&session_file, content).unwrap();

        let result = parse_codex_session(&session_file, "local", false);
        assert!(result.is_none());
    }

    #[test]
    fn test_parse_codex_session_includes_codex_exec_when_flag_set() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("test.jsonl");

        let content = r#"{"type":"session_meta","payload":{"id":"test-id","cwd":"/test","originator":"codex_exec"}}"#;
        fs::write(&session_file, content).unwrap();

        let result = parse_codex_session(&session_file, "local", true);
        assert!(result.is_some());
        assert_eq!(result.unwrap().metadata.session_id, "codex:test-id");
    }

    #[test]
    fn test_parse_codex_session_includes_interactive_sessions() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("test.jsonl");

        let content = r#"{"type":"session_meta","payload":{"id":"test-id","cwd":"/test","originator":"codex_cli_rs"}}"#;
        fs::write(&session_file, content).unwrap();

        let result = parse_codex_session(&session_file, "local", false);
        assert!(result.is_some());
        assert_eq!(result.unwrap().metadata.session_id, "codex:test-id");
    }

    #[test]
    fn test_parse_codex_session_missing_originator_included() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("test.jsonl");

        let content = r#"{"type":"session_meta","payload":{"id":"test-id","cwd":"/test"}}"#;
        fs::write(&session_file, content).unwrap();

        let result = parse_codex_session(&session_file, "local", false);
        assert!(result.is_some());
        assert_eq!(result.unwrap().metadata.session_id, "codex:test-id");
    }

    #[test]
    fn test_parse_codex_session_skips_system_messages() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("test.jsonl");

        // Build content programmatically to avoid raw string escaping issues
        let line1 = r#"{"type":"session_meta","payload":{"id":"test-id","cwd":"/test"}}"#;
        let line2 = format!(
            r#"{{"type":"response_item","payload":{{"role":"user","content":[{{"type":"input_text","text":"{} AGENTS.md"}}]}}}}"#,
            "#"
        );
        let line3 = r#"{"type":"response_item","payload":{"role":"user","content":[{"type":"input_text","text":"<environment_context>stuff</environment_context>"}]}}"#;
        let line4 = r#"{"type":"response_item","payload":{"role":"user","content":[{"type":"input_text","text":"Hello actual message"}]}}"#;
        let content = format!("{}\n{}\n{}\n{}", line1, line2, line3, line4);

        fs::write(&session_file, content).unwrap();

        let result = parse_codex_session(&session_file, "local", false);
        assert!(result.is_some());

        let parsed = result.unwrap();
        // Only the actual user message should be included
        assert_eq!(parsed.messages.len(), 1);
        assert_eq!(parsed.messages[0].content, "Hello actual message");
    }

    #[test]
    fn test_extract_codex_project() {
        assert_eq!(extract_codex_project("/home/user/projects/myapp"), "myapp");
        assert_eq!(extract_codex_project("/Users/dev/code/webapp"), "webapp");
        assert_eq!(extract_codex_project(""), "unknown");
    }

    #[test]
    fn test_make_msg_id() {
        assert_eq!(make_msg_id("2026-01-08T10:00:00.123Z", 0), "msg-2026-01-08T10-00-00-123Z");
        assert_eq!(make_msg_id("", 5), "msg-5");
    }

    #[test]
    fn test_first_message_truncation() {
        let tmp = tempdir().unwrap();
        let session_file = tmp.path().join("test.jsonl");

        let long_message = "a".repeat(500);
        let content = format!(
            r#"{{"type":"user","timestamp":"2026-01-08T10:00:00Z","message":{{"content":"{}"}}}}"#,
            long_message
        );
        fs::write(&session_file, content).unwrap();

        let result = parse_claude_session(&session_file, "test", "local");
        assert!(result.is_some());

        let parsed = result.unwrap();
        assert!(parsed.metadata.first_message.is_some());
        let first = parsed.metadata.first_message.unwrap();
        assert!(first.len() <= 303); // 300 chars + "..."
        assert!(first.ends_with("..."));
    }
}
