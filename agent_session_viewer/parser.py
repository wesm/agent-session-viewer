"""Parse Claude Code JSONL session files."""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Generator
from dataclasses import dataclass


@dataclass
class SessionMetadata:
    session_id: str
    project: str
    machine: str
    first_message: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]
    message_count: int
    agent: str = "claude"  # "claude" or "codex"


@dataclass
class ParsedMessage:
    msg_id: str
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse an ISO timestamp string to datetime."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def format_tool_use(block: dict) -> str:
    """Format a tool_use block for display."""
    tool_name = block.get("name", "unknown")
    tool_input = block.get("input", {})

    # Format based on tool type
    if tool_name == "AskUserQuestion":
        questions = tool_input.get("questions", [])
        lines = [f"[Question: {tool_name}]"]
        for q in questions:
            lines.append(f"  {q.get('question', '')}")
            for opt in q.get("options", []):
                lines.append(f"    - {opt.get('label', '')}: {opt.get('description', '')}")
        return "\n".join(lines)

    elif tool_name == "TodoWrite":
        todos = tool_input.get("todos", [])
        lines = ["[Todo List]"]
        for todo in todos:
            status = todo.get("status", "pending")
            icon = {"completed": "✓", "in_progress": "→", "pending": "○"}.get(status, "○")
            lines.append(f"  {icon} {todo.get('content', '')}")
        return "\n".join(lines)

    elif tool_name == "EnterPlanMode":
        return "[Entering Plan Mode]"

    elif tool_name == "ExitPlanMode":
        return "[Exiting Plan Mode]"

    elif tool_name in ("Read", "Glob", "Grep"):
        # File operations - show what was accessed
        if tool_name == "Read":
            return f"[Read: {tool_input.get('file_path', 'unknown')}]"
        elif tool_name == "Glob":
            return f"[Glob: {tool_input.get('pattern', '')} in {tool_input.get('path', '.')}]"
        elif tool_name == "Grep":
            return f"[Grep: {tool_input.get('pattern', '')}]"

    elif tool_name == "Edit":
        return f"[Edit: {tool_input.get('file_path', 'unknown')}]"

    elif tool_name == "Write":
        return f"[Write: {tool_input.get('file_path', 'unknown')}]"

    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        desc = tool_input.get("description", "")
        if desc:
            return f"[Bash: {desc}]\n$ {cmd}"
        return f"[Bash]\n$ {cmd}"

    elif tool_name == "Task":
        desc = tool_input.get("description", "")
        agent_type = tool_input.get("subagent_type", "")
        return f"[Task: {desc} ({agent_type})]"

    # Default: show tool name
    return f"[Tool: {tool_name}]"


def extract_text_content(content, include_tools: bool = True) -> str:
    """Extract text from message content (string or list of blocks)."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    texts.append(block.get("text", ""))
                elif block_type == "thinking":
                    # Include thinking blocks (collapsed in UI later if needed)
                    thinking_text = block.get("thinking", "")
                    if thinking_text:
                        texts.append(f"[Thinking]\n{thinking_text}")
                elif block_type == "tool_use" and include_tools:
                    texts.append(format_tool_use(block))
        return "\n".join(texts)

    return ""


def make_msg_id(timestamp: str) -> str:
    """Create a message ID from timestamp."""
    return f"msg-{timestamp.replace(':', '-').replace('.', '-')}"


def parse_session(
    jsonl_path: Path,
    project: str,
    machine: str = "local"
) -> tuple[SessionMetadata, list[ParsedMessage]]:
    """
    Parse a JSONL session file and extract metadata + messages.

    Returns:
        Tuple of (SessionMetadata, list of ParsedMessages)
    """
    session_id = jsonl_path.stem
    messages = []
    first_message = None
    started_at = None
    ended_at = None
    message_count = 0

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                message_count += 1

                # Extract timestamp
                ts = None
                ts_str = None
                if "timestamp" in entry:
                    ts_str = entry["timestamp"]
                    ts = parse_timestamp(ts_str)
                elif "snapshot" in entry and "timestamp" in entry.get("snapshot", {}):
                    ts_str = entry["snapshot"]["timestamp"]
                    ts = parse_timestamp(ts_str)

                if ts:
                    if started_at is None:
                        started_at = ts
                    ended_at = ts

                # Process user messages
                if entry.get("type") == "user":
                    msg_data = entry.get("message", {})
                    content = extract_text_content(msg_data.get("content", ""))

                    if content.strip():
                        # Capture first user message for summary
                        if first_message is None:
                            first_message = content[:300].replace("\n", " ").strip()
                            if len(content) > 300:
                                first_message += "..."

                        messages.append(ParsedMessage(
                            msg_id=make_msg_id(ts_str) if ts_str else f"msg-{len(messages)}",
                            role="user",
                            content=content,
                            timestamp=ts_str or "",
                        ))

                # Process assistant messages
                elif entry.get("type") == "assistant":
                    msg_data = entry.get("message", {})
                    content = extract_text_content(msg_data.get("content", []))

                    if content.strip():
                        messages.append(ParsedMessage(
                            msg_id=make_msg_id(ts_str) if ts_str else f"msg-{len(messages)}",
                            role="assistant",
                            content=content,
                            timestamp=ts_str or "",
                        ))

    except Exception as e:
        print(f"Error parsing {jsonl_path}: {e}")

    metadata = SessionMetadata(
        session_id=session_id,
        project=project,
        machine=machine,
        first_message=first_message,
        started_at=started_at.isoformat() if started_at else None,
        ended_at=ended_at.isoformat() if ended_at else None,
        message_count=len(messages),
    )

    return metadata, messages


def iter_project_sessions(sessions_dir: Path) -> Generator[tuple[str, Path], None, None]:
    """
    Iterate over all sessions in a sessions directory.

    Yields:
        Tuples of (project_name, jsonl_path)
    """
    for project_dir in sorted(sessions_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        project_name = project_dir.name

        for session_file in project_dir.glob("*.jsonl"):
            yield project_name, session_file


def extract_codex_project(cwd: str) -> str:
    """Extract project name from Codex cwd path."""
    if not cwd:
        return "unknown"
    path = Path(cwd)
    # Use the last component of the path as project name
    return path.name or "unknown"


def parse_codex_session(
    jsonl_path: Path,
    machine: str = "local",
    include_exec: bool = False,
) -> tuple[Optional[SessionMetadata], list[ParsedMessage]]:
    """
    Parse a Codex JSONL session file and extract metadata + messages.

    Codex format:
    - session_meta: {timestamp, payload: {id, cwd, originator}}
    - response_item: {timestamp, payload: {role: "user"|"assistant", content: [{type, text}]}}

    Note: Session IDs are prefixed with "codex:" to avoid collisions with Claude session IDs.

    Args:
        jsonl_path: Path to the JSONL file
        machine: Machine identifier
        include_exec: If False, skip non-interactive sessions (originator=codex_exec)

    Returns:
        Tuple of (SessionMetadata or None if skipped, list of ParsedMessages)
    """
    messages = []
    first_message = None
    started_at = None
    ended_at = None
    session_id = None
    project = "unknown"
    originator = None

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type")
                payload = entry.get("payload", {})
                ts_str = entry.get("timestamp")
                ts = parse_timestamp(ts_str)

                if ts:
                    if started_at is None:
                        started_at = ts
                    ended_at = ts

                # Extract session metadata
                if entry_type == "session_meta":
                    session_id = payload.get("id")
                    cwd = payload.get("cwd", "")
                    project = extract_codex_project(cwd)
                    originator = payload.get("originator", "")

                    # Skip non-interactive sessions unless explicitly included
                    if not include_exec and originator == "codex_exec":
                        return None, []

                # Process messages
                elif entry_type == "response_item":
                    role = payload.get("role")
                    if role not in ("user", "assistant"):
                        continue

                    content_blocks = payload.get("content", [])
                    texts = []
                    for block in content_blocks:
                        if isinstance(block, dict):
                            block_type = block.get("type", "")
                            if block_type in ("input_text", "output_text", "text"):
                                text = block.get("text", "")
                                if text:
                                    texts.append(text)

                    content = "\n".join(texts)
                    if not content.strip():
                        continue

                    # Skip system/instruction messages
                    if role == "user" and (
                        content.startswith("# AGENTS.md") or
                        content.startswith("<environment_context>") or
                        content.startswith("<INSTRUCTIONS>")
                    ):
                        continue

                    # Capture first user message for summary
                    if role == "user" and first_message is None:
                        first_message = content[:300].replace("\n", " ").strip()
                        if len(content) > 300:
                            first_message += "..."

                    messages.append(ParsedMessage(
                        msg_id=make_msg_id(ts_str) if ts_str else f"msg-{len(messages)}",
                        role=role,
                        content=content,
                        timestamp=ts_str or "",
                    ))

    except Exception as e:
        print(f"Error parsing Codex session {jsonl_path}: {e}")

    # Fallback session_id from filename if not in metadata
    if not session_id:
        session_id = jsonl_path.stem

    # Prefix with "codex:" to avoid collision with Claude session IDs
    session_id = f"codex:{session_id}"

    metadata = SessionMetadata(
        session_id=session_id,
        project=project,
        machine=machine,
        first_message=first_message,
        started_at=started_at.isoformat() if started_at else None,
        ended_at=ended_at.isoformat() if ended_at else None,
        message_count=len(messages),
        agent="codex",
    )

    return metadata, messages
