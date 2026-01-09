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


def extract_cwd_from_session(session_path: Path) -> Optional[str]:
    """Extract the full cwd path from session file.

    Returns the complete cwd path, or None if cwd not found.
    This path is used as a unique project identifier to avoid collisions
    when multiple projects share the same leaf directory name.
    """
    import json

    try:
        with open(session_path, "r", encoding="utf-8") as f:
            # Check first 50 lines for a cwd field
            for i, line in enumerate(f):
                if i >= 50:  # Don't read entire file
                    break
                if not line.strip():
                    continue

                try:
                    entry = json.loads(line)
                    cwd = entry.get("cwd")
                    if cwd:
                        # Return the full path for uniqueness
                        return cwd
                except json.JSONDecodeError:
                    continue
    except (OSError, IOError):
        pass

    return None


def get_safe_storage_key(project_identifier: str) -> str:
    """Convert a project identifier to a safe directory name for storage.

    Args:
        project_identifier: Either a full cwd path like "/Users/user/Projects/app"
                           or an encoded directory name like "-Users-user-Projects-app"

    Returns:
        Safe directory name that won't escape SESSIONS_DIR
    """
    if project_identifier.startswith("/") or project_identifier.startswith("~"):
        # It's an absolute path - normalize it first to resolve .. and other tricks
        try:
            normalized = str(Path(project_identifier).resolve())
        except (ValueError, OSError):
            # If path resolution fails, fall back to basic sanitization
            normalized = project_identifier

        # Create a safe slug - replace slashes and other unsafe chars with dashes
        safe = normalized.replace("/", "-").replace("~", "home").lstrip("-")

        # Limit length to prevent filesystem issues
        if len(safe) > 200:
            # Use hash for very long paths
            import hashlib
            hash_suffix = hashlib.md5(project_identifier.encode()).hexdigest()[:8]
            safe = safe[:180] + "-" + hash_suffix
        return safe
    else:
        # Already an encoded directory name, safe to use
        return project_identifier


def get_project_display_name(cwd_or_encoded_path: str) -> str:
    """Get a clean display name from a cwd path or encoded directory name.

    Args:
        cwd_or_encoded_path: Either a full cwd path like "/Users/user/Projects/app"
                            or an encoded directory name like "-Users-user-Projects-app"

    Returns:
        Clean project name suitable for display (just the leaf directory)
    """
    if cwd_or_encoded_path.startswith("/") or cwd_or_encoded_path.startswith("~"):
        # It's a filesystem path - return the leaf directory name
        return Path(cwd_or_encoded_path).name
    else:
        # It's an encoded directory name - use the old extraction logic
        return get_project_name(Path(cwd_or_encoded_path))


def get_project_name(dir_path: Path) -> str:
    """Convert a project directory path to a clean name.

    DEPRECATED: This function tries to reverse-engineer the project name from
    the encoded directory name, which is ambiguous. Prefer extracting from
    the session file's cwd field using extract_project_name_from_session().

    This is kept for backwards compatibility and as a fallback.
    """
    name = dir_path.name
    # Strip common path prefixes like "-Users-user-code-"
    if name.startswith("-"):
        parts = name.split("-")
        # Common directory names that indicate path components
        path_markers = {"users", "home", "code", "projects", "documents", "downloads", "experiments", "src", "workspace"}

        # Find the last path marker and take up to last 2 components after it
        # This handles nested directories (e.g., /Projects/parent/child → "child")
        # while preserving hyphenated names (e.g., /Projects/my-app → "my-app")
        last_marker_idx = -1
        for i, part in enumerate(parts):
            if part.lower() in path_markers:
                last_marker_idx = i

        if last_marker_idx != -1 and last_marker_idx + 1 < len(parts):
            remaining = parts[last_marker_idx + 1:]
            # Take last 2 components to handle hyphenated names, or fewer if that's all there is
            name = "-".join(remaining[-2:]) if len(remaining) >= 2 else "-".join(remaining)
        else:
            # Fallback: take everything except the first empty part
            name = "-".join(parts[1:]) if len(parts) > 1 else name

    # Return name as-is, preserving dashes
    return name


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

    # Copy to local storage using safe storage key to prevent path traversal
    safe_dir_name = get_safe_storage_key(project_name)
    target_dir = SESSIONS_DIR / safe_dir_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / source_path.name
    shutil.copy2(source_path, target_path)

    # Parse and index (keep project_name as full identifier for DB uniqueness)
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
    # Get all non-agent session files sorted by modification time (newest first)
    session_files = sorted(
        [f for f in project_dir.glob("*.jsonl") if not f.stem.startswith("agent-")],
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )

    # Use encoded directory name as fallback (unique identifier)
    project_identifier = project_dir.name
    if session_files:
        # Try to get actual cwd from most recent session file (deterministic)
        actual_cwd = extract_cwd_from_session(session_files[0])
        if actual_cwd:
            project_identifier = actual_cwd

    # Get display name for progress reporting
    display_name = get_project_display_name(project_identifier)

    if on_progress:
        on_progress("project_start", project=display_name, sessions=len(session_files))

    stats = {
        "project": display_name,
        "total": 0,
        "synced": 0,
        "skipped": 0,
    }

    for session_file in session_files:
        if on_progress:
            on_progress("session_start", session=session_file.stem)

        # Use project_identifier (full cwd) for storage, ensures uniqueness
        result = sync_session_file(session_file, project_identifier, machine)
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
        on_progress("project_done", project=display_name)

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
    """Re-index all sessions from the data/sessions directory.

    Extracts correct project identifiers (full cwd paths) from session files,
    ensuring each unique project path is stored separately even if they share
    the same leaf directory name (e.g., /a/app vs /b/app).
    """
    results = {
        "sessions": 0,
        "messages": 0,
    }

    for old_project_name, session_path in iter_project_sessions(SESSIONS_DIR):
        # Try to extract full cwd from session file (unique identifier)
        project_identifier = extract_cwd_from_session(session_path)
        if not project_identifier:
            # Fallback to directory name if cwd not found
            project_identifier = old_project_name

        metadata, messages = parse_session(session_path, project_identifier)

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
