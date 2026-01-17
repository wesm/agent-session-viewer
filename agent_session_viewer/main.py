"""FastAPI server for AI agent session viewer."""

import argparse
import asyncio
import json
import sys
import webbrowser
from pathlib import Path
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
import urllib.request
import urllib.error
import urllib.parse

from fastapi import FastAPI, Query, UploadFile, File, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import db
from . import sync as sync_module

# Paths - static files bundled with package, data in user directory
PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / "static"
DATA_DIR = Path.home() / ".agent-session-viewer"
CONFIG_FILE = DATA_DIR / "config.json"


def load_config() -> dict:
    """Load configuration from config file."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_config(config: dict) -> None:
    """Save configuration to config file with secure permissions."""
    import os
    import stat

    # Ensure directory exists with restricted permissions (0o700)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(stat.S_IRWXU)  # 0o700 - owner only
    except OSError:
        pass  # May fail on some systems, but directory exists

    # Write config with restricted permissions (0o600)
    content = json.dumps(config, indent=2)
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
        # Enforce permissions on existing files (os.open mode only applies on creation)
        CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except Exception:
        os.close(fd)
        raise


def get_github_token() -> Optional[str]:
    """Get GitHub token from config."""
    config = load_config()
    return config.get("github_token")


def set_github_token(token: str) -> None:
    """Set GitHub token in config."""
    config = load_config()
    config["github_token"] = token
    save_config(config)


def create_github_gist(content: str, filename: str, description: str, token: str) -> dict:
    """Create a GitHub Gist and return the response."""
    url = "https://api.github.com/gists"
    data = json.dumps({
        "description": description,
        "public": True,
        "files": {
            filename: {"content": content}
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "agent-session-viewer",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        raise HTTPException(
            status_code=e.code,
            detail=f"GitHub API error: {e.reason}. {error_body}"
        )
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to GitHub: {e.reason}")


class TokenRequest(BaseModel):
    token: str


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


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str):
    """Export session as a self-contained HTML file."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = db.get_session_messages(session_id)
    html = generate_export_html(session, messages)

    # Create filename from project and date
    project = session.get("project", "session").replace("/", "-").replace("\\", "-")
    date_str = ""
    if session.get("started_at"):
        try:
            dt = datetime.fromisoformat(session["started_at"].replace("Z", "+00:00"))
            date_str = dt.strftime("%Y%m%d")
        except (ValueError, AttributeError):
            pass
    filename = sanitize_filename(f"{project}-{date_str or session_id[:8]}.html")

    return Response(
        content=html,
        media_type="text/html",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for Content-Disposition header.

    Removes control characters, quotes, and other problematic chars.
    """
    import re
    # Remove control characters (0x00-0x1F, 0x7F)
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)
    # Remove/replace problematic characters for Content-Disposition
    filename = filename.replace('"', "'").replace('\\', '_')
    # Remove any remaining newlines/carriage returns (safety)
    filename = filename.replace('\n', '').replace('\r', '')
    return filename


def sanitize_role_class(role: str) -> str:
    """Whitelist role values for safe CSS class names."""
    allowed = {"user", "assistant"}
    return role if role in allowed else "unknown"


def sanitize_agent_class(agent: str) -> str:
    """Whitelist agent values for safe CSS class names."""
    allowed = {"claude", "codex"}
    return agent if agent in allowed else "claude"


def is_thinking_only(content: str) -> bool:
    """Check if message contains only thinking blocks."""
    if not content:
        return False
    import re
    # Remove thinking blocks and check if anything meaningful remains
    without_thinking = re.sub(r"\[Thinking\]\n?[\s\S]*?(?=\n\[|\n\n\[|$)", "", content).strip()
    return without_thinking == ""


def format_content_for_export(text: str) -> str:
    """Format message content with markdown-ish formatting."""
    import re
    if not text:
        return ""

    html = escape_html(text)
    # Code blocks
    html = re.sub(r"```(\w*)\n([\s\S]*?)```", r"<pre><code>\2</code></pre>", html)
    # Inline code
    html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
    # Thinking blocks
    html = re.sub(
        r"\[Thinking\]\n?([\s\S]*?)(?=\n\[|\n\n\[|$)",
        r'<div class="thinking-block"><div class="thinking-label">Thinking</div>\1</div>',
        html,
    )
    # Tool blocks
    html = re.sub(
        r"\[(Tool|Read|Write|Edit|Bash|Glob|Grep|Task|Question|Todo List|Entering Plan Mode|Exiting Plan Mode)([^\]]*)\]([\s\S]*?)(?=\n\[|\n\n|<div|$)",
        r'<div class="tool-block">[\1\2]\3</div>',
        html,
    )
    return html


def format_timestamp(ts: str) -> str:
    """Format timestamp for display."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return ts


def generate_export_html(session: dict, messages: list) -> str:
    """Generate a self-contained HTML export of a session."""

    # Generate messages HTML (in chronological order - CSS handles sort toggle)
    messages_html_parts = []
    for i, m in enumerate(messages):
        role_raw = m.get("role", "unknown")
        role_class = sanitize_role_class(role_raw)
        content = m.get("content", "")
        timestamp = m.get("timestamp", "")
        thinking_only_class = " thinking-only" if role_class == "assistant" and is_thinking_only(content) else ""

        messages_html_parts.append(f'''
            <div class="message {role_class}{thinking_only_class}" data-index="{i}">
                <div class="message-header">
                    <span class="message-role">{escape_html(role_raw)}</span>
                    <span class="message-time">{format_timestamp(timestamp)}</span>
                </div>
                <div class="message-content">{format_content_for_export(content)}</div>
            </div>''')

    messages_html = "\n".join(messages_html_parts)

    # Session metadata
    project = escape_html(session.get("project", "Unknown"))
    agent_raw = session.get("agent", "claude")
    agent_class = sanitize_agent_class(agent_raw)
    # Preserve original agent name for display, with friendly names for known agents
    if agent_raw == "claude":
        agent_display = "Claude"
    elif agent_raw == "codex":
        agent_display = "Codex"
    else:
        agent_display = escape_html(agent_raw) if agent_raw else "Claude"
    message_count = session.get("message_count", len(messages))
    started_at = format_timestamp(session.get("started_at", ""))
    first_message = escape_html(session.get("first_message", "")[:100])

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{project} - Agent Session</title>
    <style>
        :root {{
            --bg: #0d1117;
            --surface: #161b22;
            --surface-hover: #21262d;
            --border: #30363d;
            --text: #e6edf3;
            --text-muted: #8b949e;
            --accent: #58a6ff;
            --accent-muted: #388bfd;
            --user-bg: #1c2128;
            --assistant-bg: #1a1f26;
            --success: #3fb950;
            --warning: #d29922;
            --tool-bg: #1a2332;
            --thinking-bg: #1f1a24;
            --agent-accent: #9d7cd8;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Consolas', monospace;
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
        }}

        /* Header */
        header {{
            background: var(--surface);
            border-bottom: 1px solid var(--border);
            padding: 16px 24px;
            position: sticky;
            top: 0;
            z-index: 100;
        }}

        .header-content {{
            max-width: 900px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
        }}

        .header-left {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        h1 {{
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text);
        }}

        .session-meta {{
            font-size: 0.8rem;
            color: var(--text-muted);
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}

        .session-meta .agent-name {{
            color: #d4a574;
        }}

        .session-meta .agent-name.codex {{
            color: #7dd3fc;
        }}

        .controls {{
            display: flex;
            gap: 12px;
            align-items: center;
        }}

        /* CSS-only toggle buttons using checkbox hack */
        .toggle-input {{
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }}

        .toggle-label {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            background: var(--surface-hover);
            border: 1px solid var(--border);
            border-radius: 6px;
            color: var(--text);
            cursor: pointer;
            font-size: 0.85rem;
            user-select: none;
            transition: background 0.15s, border-color 0.15s;
        }}

        .toggle-label:hover {{
            background: var(--border);
        }}

        .toggle-input:checked + .toggle-label {{
            background: var(--accent-muted);
            border-color: var(--accent);
        }}

        .toggle-indicator {{
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--text-muted);
            transition: background 0.15s;
        }}

        .toggle-input:checked + .toggle-label .toggle-indicator {{
            background: var(--text);
        }}

        /* Main content */
        main {{
            max-width: 900px;
            margin: 0 auto;
            padding: 24px;
        }}

        .messages {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}

        .message {{
            padding: 16px;
            border-radius: 8px;
            border: 1px solid var(--border);
        }}

        .message.user {{
            background: var(--user-bg);
            border-left: 3px solid var(--accent);
        }}

        .message.assistant {{
            background: var(--assistant-bg);
            border-left: 3px solid var(--agent-accent);
        }}

        .message-header {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 0.8rem;
        }}

        .message-role {{
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .message.user .message-role {{ color: var(--accent); }}
        .message.assistant .message-role {{ color: var(--agent-accent); }}

        .message-time {{ color: var(--text-muted); }}

        .message-content {{
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 0.9rem;
        }}

        .message-content code {{
            background: var(--bg);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: inherit;
            font-size: 0.85em;
        }}

        .message-content pre {{
            background: var(--bg);
            padding: 12px;
            border-radius: 6px;
            overflow-x: auto;
            margin: 12px 0;
        }}

        .message-content pre code {{
            background: none;
            padding: 0;
        }}

        /* Thinking blocks - hidden by default */
        .thinking-block {{
            background: var(--thinking-bg);
            border-left: 2px solid #8b5cf6;
            padding: 12px;
            margin: 8px 0;
            border-radius: 4px;
            font-style: italic;
            color: var(--text-muted);
            display: none;
        }}

        .thinking-label {{
            font-size: 0.75rem;
            font-weight: 600;
            color: #8b5cf6;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
            font-style: normal;
        }}

        /* Messages that only contain thinking content */
        .message.thinking-only {{
            display: none;
        }}

        /* When thinking toggle is checked, show thinking blocks */
        #thinking-toggle:checked ~ main .thinking-block {{
            display: block;
        }}

        #thinking-toggle:checked ~ main .message.thinking-only {{
            display: block;
        }}

        .tool-block {{
            background: var(--tool-bg);
            border-left: 2px solid var(--warning);
            padding: 8px 12px;
            margin: 8px 0;
            border-radius: 4px;
            font-size: 0.85rem;
        }}

        /* Sort order toggle - reverse message order when checked */
        #sort-toggle:checked ~ main .messages {{
            flex-direction: column-reverse;
        }}

        /* Footer */
        footer {{
            max-width: 900px;
            margin: 40px auto;
            padding: 16px 24px;
            border-top: 1px solid var(--border);
            font-size: 0.8rem;
            color: var(--text-muted);
            text-align: center;
        }}

        footer a {{
            color: var(--accent);
            text-decoration: none;
        }}

        footer a:hover {{
            text-decoration: underline;
        }}

        /* Responsive */
        @media (max-width: 600px) {{
            header {{
                padding: 12px 16px;
            }}
            main {{
                padding: 16px;
            }}
            .header-content {{
                flex-direction: column;
                align-items: flex-start;
            }}
        }}
    </style>
</head>
<body>
    <!-- CSS-only toggles using the checkbox hack -->
    <input type="checkbox" id="thinking-toggle" class="toggle-input">
    <input type="checkbox" id="sort-toggle" class="toggle-input">

    <header>
        <div class="header-content">
            <div class="header-left">
                <h1>{project}</h1>
                <div class="session-meta">
                    <span class="agent-name {agent_class}">{agent_display}</span>
                    <span>{message_count} messages</span>
                    <span>{started_at}</span>
                </div>
            </div>
            <div class="controls">
                <label for="thinking-toggle" class="toggle-label">
                    <span class="toggle-indicator"></span>
                    Thinking
                </label>
                <label for="sort-toggle" class="toggle-label">
                    <span class="toggle-indicator"></span>
                    Newest first
                </label>
            </div>
        </div>
    </header>

    <main>
        <div class="messages">
{messages_html}
        </div>
    </main>

    <footer>
        Exported from <a href="https://github.com/wesm/agent-session-viewer">Agent Session Viewer</a>
    </footer>
</body>
</html>'''

    return html


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


@app.get("/api/config/github")
async def get_github_config():
    """Check if GitHub token is configured."""
    token = get_github_token()
    return {"configured": bool(token)}


@app.post("/api/config/github")
async def set_github_config(request: TokenRequest):
    """Set GitHub token."""
    token = request.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")

    # Validate token by making a test request
    test_url = "https://api.github.com/user"
    req = urllib.request.Request(
        test_url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "agent-session-viewer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            user_data = json.loads(response.read().decode("utf-8"))
            username = user_data.get("login", "unknown")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise HTTPException(status_code=401, detail="Invalid GitHub token")
        raise HTTPException(status_code=e.code, detail=f"GitHub API error: {e.reason}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to GitHub: {e.reason}")

    set_github_token(token)
    return {"success": True, "username": username}


@app.post("/api/sessions/{session_id}/publish")
async def publish_session(session_id: str):
    """Publish session as a GitHub Gist."""
    token = get_github_token()
    if not token:
        raise HTTPException(status_code=401, detail="GitHub token not configured")

    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = db.get_session_messages(session_id)
    html = generate_export_html(session, messages)

    # Create filename and description
    project = session.get("project", "session").replace("/", "-").replace("\\", "-")
    date_str = ""
    if session.get("started_at"):
        try:
            dt = datetime.fromisoformat(session["started_at"].replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            pass

    filename = f"{project}-{date_str or session_id[:8]}.html"
    first_msg = session.get("first_message", "")[:100]
    description = f"Agent session: {project} - {first_msg}"

    # Create the gist
    gist_response = create_github_gist(html, filename, description, token)

    gist_id = gist_response.get("id")
    gist_url = gist_response.get("html_url")
    owner = gist_response.get("owner", {}).get("login", "")

    # Build the raw file URL for htmlpreview (URL-encode filename for special chars)
    encoded_filename = urllib.parse.quote(filename, safe="")
    raw_url = f"https://gist.githubusercontent.com/{owner}/{gist_id}/raw/{encoded_filename}"
    view_url = f"https://htmlpreview.github.io/?{raw_url}"

    return {
        "success": True,
        "gist_id": gist_id,
        "gist_url": gist_url,
        "view_url": view_url,
        "raw_url": raw_url,
    }


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
        return index_path.read_text(encoding="utf-8")
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
    # Windows console defaults to legacy encoding (e.g., cp1252) which can't
    # handle Unicode characters in progress bars. Reconfigure to UTF-8.
    if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
        sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

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
