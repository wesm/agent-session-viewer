"""Sync sessions from local Claude Code projects."""

import hashlib
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
import fnmatch

from . import db
from .parser import parse_session, iter_project_sessions


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
    """Find the source .jsonl file for a session ID in Claude projects."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return None

    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
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


def sync_all(machine: str = "local", on_progress=None) -> dict:
    """
    Sync all matching projects.

    Returns:
        Dict with overall sync stats
    """
    projects = find_matching_projects()

    if on_progress:
        on_progress("start", projects=len(projects))

    results = {
        "timestamp": datetime.now().isoformat(),
        "projects": [],
        "total_sessions": 0,
        "total_synced": 0,
    }

    for project_dir in projects:
        stats = sync_project(project_dir, machine, on_progress=on_progress)
        results["projects"].append(stats)
        results["total_sessions"] += stats["total"]
        results["total_synced"] += stats["synced"]

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
