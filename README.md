# Agent Session Viewer

Browse, search, and revisit your AI coding sessions. Never lose track of that clever solution your AI pair programmer came up with three weeks ago.

![Session Viewer](https://raw.githubusercontent.com/wesm/agent-session-viewer/main/docs/screenshots/session-viewer.png)

## Why?

AI coding sessions pile up fast. Finding that one conversation where you solved a tricky bug or implemented a specific pattern means digging through session files by hand. This tool gives you instant full-text search across every session from Claude Code and Codex, organized by project.

## Features

- **Full-text search** - Find any message across all your sessions instantly
- **Live updates** - Active sessions refresh automatically as new messages arrive
- **Auto-sync** - Background sync every 15 minutes, plus manual sync with `r`
- **Keyboard-first** - Vim-style navigation (j/k/[/]) for fast browsing
- **Project organization** - Sessions grouped by codebase
- **Export & publish** - Download sessions as HTML or publish to GitHub Gist for sharing
- **Local-first** - All data stays on your machine in `~/.agent-session-viewer/`
- **Zero config** - Works out of the box

## Install

```bash
# With uv (recommended)
uv tool install agent-session-viewer

# With pip
pip install agent-session-viewer
```

## Usage

```bash
# If installed via uv tool install or pip
agent-session-viewer

# Or run directly without installing
uvx agent-session-viewer
```

Opens a browser at `http://localhost:8080`.

Options:
- `--port 9000` - Use a custom port
- `--no-browser` - Don't open browser automatically
- `--host 0.0.0.0` - Bind to all interfaces

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `j` / `k` | Next / previous message |
| `]` / `[` | Next / previous session |
| `o` | Toggle message order |
| `r` | Sync sessions |
| `e` | Export session as HTML |
| `p` | Publish session to Gist |
| `⌘K` | Focus search |
| `?` | Show all shortcuts |

## Export & Publish

Share your AI coding sessions with teammates, for blog posts, or to document how you solved a problem.

<!-- ![Export and Publish](https://raw.githubusercontent.com/wesm/agent-session-viewer/main/docs/screenshots/export-publish.png) -->

### Export to HTML

Press `e` or click **Export** to download the current session as a self-contained HTML file.

- Works offline in any browser
- Interactive toggles for thinking blocks and message order (pure CSS, no JavaScript)
- Can be hosted on any static file server or shared directly

### Publish to GitHub Gist

Press `p` or click **Publish** to instantly share a session via GitHub Gist.

**First-time setup:**

1. Click "Publish" — a setup modal appears
2. Click the link to create a GitHub token (only needs the `gist` scope)
3. Paste the token and click "Save Token"

**After setup, publishing is one click.** You'll get two URLs:

| URL | Purpose |
|-----|---------|
| **View URL** | Shareable link that renders the HTML directly in the browser |
| **Gist URL** | Link to the gist on GitHub (for editing or deleting) |

<!-- ![Publish Modal](https://raw.githubusercontent.com/wesm/agent-session-viewer/main/docs/screenshots/publish-modal.png) -->

Your token is stored locally in `~/.agent-session-viewer/config.json` with restricted file permissions.

## Supported Agents

- **Claude Code** - syncs from `~/.claude/projects/`
- **Codex** - syncs from `~/.codex/sessions/`

## How It Works

The viewer syncs sessions from each agent's local storage into its own database with full-text search indexing. When you're viewing an active session, it watches the source file and updates the UI within seconds of new messages.

## Acknowledgements

Inspired by [claude-history-tool](https://github.com/andyfischer/ai-coding-tools/tree/main/claude-history-tool) by Andy Fischer and [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) by Simon Willison.

## License

MIT
