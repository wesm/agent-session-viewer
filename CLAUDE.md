# Claude Code Instructions

## Git Workflow

- **Never amend commits** - always create new commits for fixes
- **Let the user handle branch management** - don't create, switch, or manage branches
- Use conventional commit messages
- Run tests before committing when applicable

## Project Structure

- `agent_session_viewer/` - Python FastAPI backend
- `tauri-app/` - Native Rust/Tauri desktop app
  - `src/` - Frontend (static HTML/JS)
  - `src-tauri/` - Rust backend

## Development

### Python version
```bash
uv sync
uv run agent-session-viewer
```

### Tauri version
```bash
cd tauri-app
npm install
npm run tauri dev
```

## Testing

**All new features and bug fixes must include unit tests.** Run tests before committing:

```bash
uv run pytest -v
```

For Tauri Rust tests:
```bash
cd tauri-app/src-tauri
cargo test
```

### Test Guidelines

- Add tests for new parser/sync functionality in `tests/test_sync.py`
- Add tests for database operations in `tests/test_db.py`
- Use `tmp_path` fixture for temporary files
- Use `unittest.mock.patch` to mock module-level constants (e.g., `CLAUDE_PROJECTS_DIR`)
