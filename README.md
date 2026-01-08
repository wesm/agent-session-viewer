# Agent Session Viewer

Browse, search, and revisit your AI coding sessions. Never lose track of that clever solution Claude came up with three weeks ago.

## Why?

Claude Code sessions pile up fast. Finding that one conversation where you solved a tricky bug or implemented a specific pattern means digging through `~/.claude/projects/` by hand. This tool gives you instant full-text search across every session, organized by project.

## Features

- **Full-text search** - Find any message across all your sessions instantly
- **Auto-sync** - Pulls new sessions from Claude Code every 15 minutes
- **Keyboard-first** - Vim-style navigation (j/k/[/]) for fast browsing
- **Project organization** - Sessions grouped by codebase
- **Local-first** - Your data stays on your machine in `~/.agent-session-viewer/`
- **Zero config** - Works out of the box

## Quick Start

```bash
uv tool install agent-session-viewer
agent-session-viewer
```

Opens a browser at `http://localhost:8080`. Use `--port 9000` for a custom port or `--no-browser` to skip auto-open.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `j` / `k` | Next / previous message |
| `]` / `[` | Next / previous session |
| `o` | Toggle message order |
| `r` | Sync sessions |
| `⌘K` | Focus search |
| `?` | Show all shortcuts |

## License

MIT
