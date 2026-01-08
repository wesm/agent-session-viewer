"""Sync sessions from local Claude Code projects."""

import hashlib
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
import fnmatch

from . import db
from .parser import parse_session, parse_codex_session, iter_project_sessions


def compute_file_hash(path: Path) -> str:
    """Compute MD5 hash of a file."""
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

# Where Claude Code stores sessions
CLAUDE_PROJECTS_DIR = Path(os.environ.get(
    "CLAUDE_PROJECTS_DIR",
    Path.home() / ".claude" / "projects"
))

# Where Codex stores sessions
CODEX_SESSIONS_DIR = Path(os.environ.get(
    "CODEX_SESSIONS_DIR",
    Path.home() / ".codex" / "sessions"
))

# Where we store session files (in user's home directory)
DATA_DIR = Path.home() / ".agent-session-viewer"
SESSIONS_DIR = DATA_DIR / "sessions"

# Project patterns to match (case-insensitive)
PROJECT_PATTERNS = ["*"]


def get_project_name(dir_path: Path) -> str:
    """Convert a project directory path to a clean name."""
    name = dir_path.name
    # Strip common path prefixes like "-Users-user-code-"
    if name.startswith("-"):
        parts = name.split("-")
        # Find the meaningful part (usually after "code")
        for i, part in enumerate(parts):
            if part.lower() == "code" and i + 1 < len(parts):
                name = "-".join(parts[i + 1:])
                break
    return name.replace("-", "_")


def find_matching_projects() -> list[Path]:
    """Find all projects matching our patterns."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return []

    projects = []
    for item in CLAUDE_PROJECTS_DIR.iterdir():
        if not item.is_dir():
            continue

        name = item.name.lower()
        for pattern in PROJECT_PATTERNS:
            if fnmatch.fnmatch(name, pattern.lower()):
                projects.append(item)
                break

    return sorted(projects)


def find_source_file(session_id: str) -> Optional[Path]:
    """Find the source .jsonl file for a session ID.

    Handles both Claude (plain ID) and Codex (codex: prefixed) sessions.
    Validates session_id to prevent path traversal attacks.
    """
    if not session_id:
        return None

    # Handle Codex sessions (prefixed with "codex:")
    if session_id.startswith("codex:"):
        return _find_codex_source_file(session_id[6:])  # Strip "codex:" prefix

    # Claude sessions
    return _find_claude_source_file(session_id)


def _find_claude_source_file(session_id: str) -> Optional[Path]:
    """Find a Claude session source file."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return None

    # Validate session_id: only allow alphanumeric, hyphens, underscores
    if not session_id or not all(c.isalnum() or c in '-_' for c in session_id):
        return None

    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{session_id}.jsonl"
        try:
            candidate.resolve().relative_to(project_dir.resolve())
        except ValueError:
            continue
        if candidate.exists():
            return candidate
    return None


def _find_codex_source_file(session_id: str) -> Optional[Path]:
    """Find a Codex session source file by searching year/month/day directories."""
    if not CODEX_SESSIONS_DIR.exists():
        return None

    # Validate session_id: only allow alphanumeric, hyphens, underscores
    if not session_id or not all(c.isalnum() or c in '-_' for c in session_id):
        return None

    # Search through year/month/day structure
    for year_dir in CODEX_SESSIONS_DIR.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for day_dir in month_dir.iterdir():
                if not day_dir.is_dir() or not day_dir.name.isdigit():
                    continue
                # Codex files are named rollout-{timestamp}-{uuid}.jsonl
                # UUID format: 8-4-4-4-12 hex chars (e.g., 019b9da7-1f41-7af2-80d9-6e293902fea8)
                for session_file in day_dir.glob("*.jsonl"):
                    stem = session_file.stem
                    if stem.startswith("rollout-"):
                        # UUID is last 5 dash-separated segments (8-4-4-4-12 format)
                        # Use rsplit to extract from end, robust to timestamp format changes
                        parts = stem.rsplit("-", 5)
                        if len(parts) == 6:
                            # parts[0] = "rollout-{timestamp}", parts[1:] = UUID segments
                            file_uuid = "-".join(parts[1:])
                            if file_uuid == session_id:
                                return session_file
    return None


def sync_session_file(
    source_path: Path,
    project_name: str,
    machine: str = "local",
    force: bool = False,
) -> Optional[dict]:
    """
    Sync a single session file using smart incremental sync.

    Skips processing if file size and hash match stored values.

    Returns:
        Session metadata dict if synced, None if skipped
    """
    session_id = source_path.stem

    # Skip agent files
    if session_id.startswith("agent-"):
        return None

    # Get source file info
    source_size = source_path.stat().st_size

    # Check if file has changed using size + hash
    stored_info = db.get_session_file_info(session_id)
    if stored_info and not force:
        stored_size, stored_hash = stored_info
        if stored_size == source_size:
            # Size matches, check hash
            source_hash = compute_file_hash(source_path)
            if source_hash == stored_hash:
                # File unchanged, skip entirely
                return {
                    "session_id": session_id,
                    "project": project_name,
                    "skipped": True,
                    "messages": 0,
                }

    # File is new or changed - compute hash if not already done
    source_hash = compute_file_hash(source_path)

    # Copy to local storage
    target_dir = SESSIONS_DIR / project_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / source_path.name
    shutil.copy2(source_path, target_path)

    # Parse and index
    metadata, messages = parse_session(target_path, project_name, machine)

    # Update database with file info
    db.upsert_session(
        session_id=metadata.session_id,
        project=metadata.project,
        machine=metadata.machine,
        first_message=metadata.first_message,
        started_at=metadata.started_at,
        ended_at=metadata.ended_at,
        message_count=metadata.message_count,
        file_size=source_size,
        file_hash=source_hash,
        agent=metadata.agent,
    )

    # Re-index messages
    db.delete_session_messages(session_id)
    if messages:
        batch = [
            (session_id, m.msg_id, m.role, m.content, m.timestamp)
            for m in messages
        ]
        db.insert_messages_batch(batch)

    return {
        "session_id": session_id,
        "project": project_name,
        "skipped": False,
        "messages": len(messages),
    }


def sync_project(project_dir: Path, machine: str = "local", on_progress=None) -> dict:
    """
    Sync all sessions from a project directory.

    Returns:
        Dict with sync stats
    """
    project_name = get_project_name(project_dir)
    # Filter out agent- files from the count
    session_files = [f for f in project_dir.glob("*.jsonl") if not f.stem.startswith("agent-")]

    if on_progress:
        on_progress("project_start", project=project_name, sessions=len(session_files))

    stats = {
        "project": project_name,
        "total": 0,
        "synced": 0,
        "skipped": 0,
    }

    for session_file in session_files:
        if on_progress:
            on_progress("session_start", session=session_file.stem)

        result = sync_session_file(session_file, project_name, machine)
        stats["total"] += 1

        msg_count = 0
        if result:
            msg_count = result.get("messages", 0)
            if result.get("skipped"):
                stats["skipped"] += 1
            else:
                stats["synced"] += 1

        if on_progress:
            on_progress("session_done", messages=msg_count)

    if on_progress:
        on_progress("project_done", project=project_name)

    return stats


def find_codex_sessions() -> list[Path]:
    """Find all Codex session files (in year/month/day subdirectories)."""
    if not CODEX_SESSIONS_DIR.exists():
        return []

    sessions = []
    # Codex stores in ~/.codex/sessions/{year}/{month}/{day}/*.jsonl
    for year_dir in CODEX_SESSIONS_DIR.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for day_dir in month_dir.iterdir():
                if not day_dir.is_dir() or not day_dir.name.isdigit():
                    continue
                for session_file in day_dir.glob("*.jsonl"):
                    sessions.append(session_file)

    return sorted(sessions)


def sync_codex_session(
    source_path: Path,
    machine: str = "local",
    force: bool = False,
) -> Optional[dict]:
    """
    Sync a single Codex session file.

    Returns:
        Session metadata dict if synced, None if skipped (including non-interactive sessions)
    """
    # Get source file info
    source_size = source_path.stat().st_size

    # Parse to get session_id and project from content
    # Non-interactive (codex_exec) sessions are skipped by default
    metadata, messages = parse_codex_session(source_path, machine)

    # Skip non-interactive sessions
    if metadata is None:
        return None

    session_id = metadata.session_id

    # Check if file has changed using size + hash
    stored_info = db.get_session_file_info(session_id)
    if stored_info and not force:
        stored_size, stored_hash = stored_info
        if stored_size == source_size:
            source_hash = compute_file_hash(source_path)
            if source_hash == stored_hash:
                return {
                    "session_id": session_id,
                    "project": metadata.project,
                    "skipped": True,
                    "messages": 0,
                }

    source_hash = compute_file_hash(source_path)

    # Copy to local storage under codex/ prefix
    target_dir = SESSIONS_DIR / f"codex_{metadata.project}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{session_id}.jsonl"
    shutil.copy2(source_path, target_path)

    # Update database
    db.upsert_session(
        session_id=metadata.session_id,
        project=metadata.project,
        machine=metadata.machine,
        first_message=metadata.first_message,
        started_at=metadata.started_at,
        ended_at=metadata.ended_at,
        message_count=metadata.message_count,
        file_size=source_size,
        file_hash=source_hash,
        agent=metadata.agent,
    )

    # Re-index messages
    db.delete_session_messages(session_id)
    if messages:
        batch = [
            (session_id, m.msg_id, m.role, m.content, m.timestamp)
            for m in messages
        ]
        db.insert_messages_batch(batch)

    return {
        "session_id": session_id,
        "project": metadata.project,
        "skipped": False,
        "messages": len(messages),
    }


def sync_all(machine: str = "local", on_progress=None) -> dict:
    """
    Sync all matching projects from Claude and Codex.

    Returns:
        Dict with overall sync stats
    """
    # Claude projects
    projects = find_matching_projects()
    # Codex sessions
    codex_sessions = find_codex_sessions()

    total_items = len(projects) + (1 if codex_sessions else 0)

    if on_progress:
        on_progress("start", projects=total_items)

    results = {
        "timestamp": datetime.now().isoformat(),
        "projects": [],
        "total_sessions": 0,
        "total_synced": 0,
    }

    # Sync Claude projects
    for project_dir in projects:
        stats = sync_project(project_dir, machine, on_progress=on_progress)
        results["projects"].append(stats)
        results["total_sessions"] += stats["total"]
        results["total_synced"] += stats["synced"]

    # Sync Codex sessions
    if codex_sessions:
        if on_progress:
            on_progress("project_start", project="codex", sessions=len(codex_sessions))

        codex_stats = {
            "project": "codex",
            "total": 0,
            "synced": 0,
            "skipped": 0,
        }

        for session_file in codex_sessions:
            if on_progress:
                on_progress("session_start", session=session_file.stem)

            result = sync_codex_session(session_file, machine)
            codex_stats["total"] += 1

            msg_count = 0
            if result:
                msg_count = result.get("messages", 0)
                if result.get("skipped"):
                    codex_stats["skipped"] += 1
                else:
                    codex_stats["synced"] += 1

            if on_progress:
                on_progress("session_done", messages=msg_count)

        if on_progress:
            on_progress("project_done", project="codex")

        results["projects"].append(codex_stats)
        results["total_sessions"] += codex_stats["total"]
        results["total_synced"] += codex_stats["synced"]

    if on_progress:
        on_progress("done")

    return results


def reindex_all():
    """Re-index all sessions from the data/sessions directory."""
    results = {
        "sessions": 0,
        "messages": 0,
    }

    for project_name, session_path in iter_project_sessions(SESSIONS_DIR):
        metadata, messages = parse_session(session_path, project_name)

        db.upsert_session(
            session_id=metadata.session_id,
            project=metadata.project,
            machine=metadata.machine,
            first_message=metadata.first_message,
            started_at=metadata.started_at,
            ended_at=metadata.ended_at,
            message_count=metadata.message_count,
        )

        db.delete_session_messages(metadata.session_id)
        if messages:
            batch = [
                (metadata.session_id, m.msg_id, m.role, m.content, m.timestamp)
                for m in messages
            ]
            db.insert_messages_batch(batch)
            results["messages"] += len(messages)

        results["sessions"] += 1

    return results
