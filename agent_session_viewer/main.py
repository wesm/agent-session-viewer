"""FastAPI server for AI agent session viewer."""

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict

from fastapi import FastAPI, Query, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import db
from . import sync as sync_module

# Paths - static files bundled with package, data in user directory
PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / "static"
DATA_DIR = Path.home() / ".agent-session-viewer"


@dataclass
class SyncStatus:
    is_syncing: bool = False
    current_project: str = ""
    current_session: str = ""
    projects_total: int = 0
    projects_done: int = 0
    sessions_total: int = 0
    sessions_done: int = 0
    messages_indexed: int = 0
    phase: str = "idle"  # idle, discovering, syncing, done


# Global state
last_sync_time: Optional[datetime] = None
scheduler: Optional[AsyncIOScheduler] = None
sync_status = SyncStatus()


def run_sync():
    """Run sync and update last sync time."""
    global last_sync_time, sync_status
    import sys

    def on_progress(event: str, **kwargs):
        """Handle sync progress updates."""
        global sync_status
        if event == "start":
            sync_status.is_syncing = True
            sync_status.phase = "discovering"
            sync_status.projects_total = kwargs.get("projects", 0)
            sync_status.projects_done = 0
            sync_status.sessions_total = 0
            sync_status.sessions_done = 0
            sync_status.messages_indexed = 0
            print(f"Syncing {sync_status.projects_total} projects...")
        elif event == "project_start":
            sync_status.phase = "syncing"
            sync_status.current_project = kwargs.get("project", "")
            sync_status.sessions_total += kwargs.get("sessions", 0)
        elif event == "project_done":
            sync_status.projects_done += 1
        elif event == "session_start":
            sync_status.current_session = kwargs.get("session", "")
        elif event == "session_done":
            sync_status.sessions_done += 1
            sync_status.messages_indexed += kwargs.get("messages", 0)
            # Print progress line
            pct = (sync_status.sessions_done / sync_status.sessions_total * 100) if sync_status.sessions_total > 0 else 0
            bar_width = 30
            filled = int(bar_width * sync_status.sessions_done / sync_status.sessions_total) if sync_status.sessions_total > 0 else 0
            bar = "█" * filled + "░" * (bar_width - filled)
            sys.stdout.write(f"\r{bar} {pct:5.1f}% | {sync_status.sessions_done}/{sync_status.sessions_total} sessions | {sync_status.messages_indexed} msgs | {sync_status.current_project}")
            sys.stdout.write("\033[K")  # Clear to end of line
            sys.stdout.flush()
        elif event == "done":
            sync_status.is_syncing = False
            sync_status.phase = "done"
            sync_status.current_project = ""
            sync_status.current_session = ""
            print()  # Newline after progress bar

    result = sync_module.sync_all(on_progress=on_progress)
    last_sync_time = datetime.now()
    sync_status.phase = "idle"
    print(f"Sync complete: {result['total_sessions']} sessions, {sync_status.messages_indexed} messages indexed")
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    global scheduler, last_sync_time

    # Ensure database is initialized
    db.init_db()

    # Initial sync
    print("Running initial sync...")
    run_sync()

    # Start scheduler for periodic sync
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_sync, "interval", minutes=15)
    scheduler.start()
    print("Scheduler started (sync every 15 minutes)")

    yield

    # Shutdown
    if scheduler:
        scheduler.shutdown()


app = FastAPI(
    title="Claude Code Session Viewer",
    lifespan=lifespan,
)


# API Routes

@app.get("/api/status")
async def get_status():
    """Get server status."""
    stats = db.get_stats()
    return {
        "status": "ok",
        "last_sync": last_sync_time.isoformat() if last_sync_time else None,
        "stats": stats,
        "sync": asdict(sync_status),
    }


@app.post("/api/sync")
async def trigger_sync():
    """Trigger a manual sync."""
    result = run_sync()
    return result


@app.get("/api/sessions")
async def list_sessions(
    project: Optional[str] = None,
    machine: Optional[str] = None,
    limit: int = Query(default=100, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """List sessions with optional filters."""
    sessions = db.get_sessions(
        project=project,
        machine=machine,
        limit=limit,
        offset=offset,
    )
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details with messages."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = db.get_session_messages(session_id)
    return {
        "session": session,
        "messages": messages,
    }


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=100, le=500),
    project: str | None = Query(default=None),
):
    """Full-text search across messages, optionally filtered by project."""
    # Prepare query for FTS5 (wrap in quotes for phrase, add * for prefix)
    fts_query = q.strip()
    if " " in fts_query and not fts_query.startswith('"'):
        # Multi-word: search as phrase
        fts_query = f'"{fts_query}"'

    results = db.search_messages(fts_query, limit=limit, project=project)
    return {"query": q, "results": results, "count": len(results)}


@app.get("/api/projects")
async def list_projects():
    """List all projects."""
    projects = db.get_projects()
    return {"projects": projects}


@app.get("/api/machines")
async def list_machines():
    """List all machines."""
    machines = db.get_machines()
    return {"machines": machines}


@app.post("/api/sessions/upload")
async def upload_session(
    file: UploadFile = File(...),
    project: str = Query(...),
    machine: str = Query(default="remote"),
):
    """Upload a session file from a remote machine."""
    if not file.filename.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="File must be .jsonl")

    # Save file
    session_id = Path(file.filename).stem
    target_dir = DATA_DIR / "sessions" / project
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / file.filename

    content = await file.read()
    target_path.write_bytes(content)

    # Index it
    from .parser import parse_session
    metadata, messages = parse_session(target_path, project, machine)

    db.upsert_session(
        session_id=metadata.session_id,
        project=metadata.project,
        machine=metadata.machine,
        first_message=metadata.first_message,
        started_at=metadata.started_at,
        ended_at=metadata.ended_at,
        message_count=metadata.message_count,
    )

    db.delete_session_messages(session_id)
    if messages:
        batch = [
            (session_id, m.msg_id, m.role, m.content, m.timestamp)
            for m in messages
        ]
        db.insert_messages_batch(batch)

    return {
        "session_id": session_id,
        "project": project,
        "machine": machine,
        "messages": len(messages),
    }


# SSE for real-time session updates
@app.get("/api/events")
async def event_stream(session_id: Optional[str] = None):
    """Server-sent events for real-time session updates.

    If session_id is provided, watches the source file for changes
    and pushes updates when the file is modified.
    """
    async def generate():
        last_mtime = None
        source_path = None

        # Find source file if session_id provided
        if session_id:
            source_path = sync_module.find_source_file(session_id)
            if source_path:
                try:
                    last_mtime = source_path.stat().st_mtime
                except (FileNotFoundError, PermissionError):
                    source_path = None

        heartbeat_counter = 0
        while True:
            # Check for file changes every 1.5 seconds
            await asyncio.sleep(1.5)
            heartbeat_counter += 1

            # Helper to sync based on session type
            is_codex = session_id and session_id.startswith("codex:")

            async def do_sync():
                if is_codex:
                    return await asyncio.to_thread(
                        sync_module.sync_codex_session,
                        source_path, machine="local", force=True
                    )
                else:
                    project_name = sync_module.get_project_name(source_path.parent)
                    return await asyncio.to_thread(
                        sync_module.sync_session_file,
                        source_path, project_name, machine="local", force=True
                    )

            if source_path:
                try:
                    current_mtime = source_path.stat().st_mtime
                    if last_mtime and current_mtime > last_mtime:
                        # File changed - sync and notify (run in thread to avoid blocking)
                        last_mtime = current_mtime
                        result = await do_sync()
                        if result and not result.get("skipped"):
                            yield f"event: session_updated\ndata: {session_id}\n\n"
                    elif last_mtime is None:
                        last_mtime = current_mtime
                except (FileNotFoundError, PermissionError):
                    # File was deleted or became inaccessible, reset and try to re-resolve
                    source_path = None
                    last_mtime = None
            elif session_id:
                # Try to re-resolve source file (handles transient errors or file recreation)
                source_path = sync_module.find_source_file(session_id)
                if source_path:
                    try:
                        last_mtime = source_path.stat().st_mtime
                        # Sync on re-resolve since file may have changed while missing
                        result = await do_sync()
                        if result and not result.get("skipped"):
                            yield f"event: session_updated\ndata: {session_id}\n\n"
                    except (FileNotFoundError, PermissionError):
                        source_path = None

            # Send heartbeat every 20 iterations (~30 seconds)
            if heartbeat_counter >= 20:
                heartbeat_counter = 0
                yield f"event: heartbeat\ndata: {datetime.now().isoformat()}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# Serve static files and SPA
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the SPA."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text()
    return "<h1>Claude Code Session Viewer</h1><p>Static files not found.</p>"


def find_available_port(start_port: int = 8080) -> int:
    """Find an available port starting from start_port."""
    import socket
    port = start_port
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            port += 1


def cli():
    """CLI entry point for agent-session-viewer."""
    parser = argparse.ArgumentParser(
        description="Agent Session Viewer - View AI agent coding sessions"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8080,
        help="Port to run server on (default: 8080, auto-finds available port)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser automatically"
    )
    args = parser.parse_args()

    import uvicorn

    # Find available port
    port = find_available_port(args.port)
    if port != args.port:
        print(f"Port {args.port} in use, using {port}")

    url = f"http://{args.host}:{port}"
    print(f"Starting Agent Session Viewer at {url}")

    # Open browser when server is ready
    if not args.no_browser:
        import threading
        def open_browser():
            import time
            import urllib.request
            import urllib.error
            # Poll until server is ready (max 60 seconds)
            for _ in range(120):
                time.sleep(0.5)
                try:
                    urllib.request.urlopen(f"{url}/api/status", timeout=1)
                    webbrowser.open(url)
                    return
                except (urllib.error.URLError, ConnectionRefusedError):
                    continue
            print("Warning: Server did not become ready, opening browser anyway")
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(app, host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    cli()
