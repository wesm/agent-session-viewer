# Makefile for Agent Session Viewer

.PHONY: run dev install test clean help

# Default target
all: run

# Install the package in development mode
install:
	uv pip install -e .

# Run the viewer
run:
	uv run agent-session-viewer

# Run with auto-reload for development
dev:
	uv run -- python -m uvicorn agent_session_viewer.main:app --reload --port 8080

# Run tests
test:
	uv run pytest

# Clean build artifacts
clean:
	rm -rf dist/ build/ *.egg-info/ __pycache__/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Help
help:
	@echo "Agent Session Viewer"
	@echo ""
	@echo "Commands:"
	@echo "  make install    Install package in development mode"
	@echo "  make run        Run the viewer (opens browser)"
	@echo "  make dev        Run with auto-reload for development"
	@echo "  make test       Run tests"
	@echo "  make clean      Clean build artifacts"
	@echo ""
	@echo "CLI options:"
	@echo "  agent-session-viewer --help"
