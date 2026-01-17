#!/bin/bash
# Release agent-session-viewer: bump version, generate changelog, tag, and publish to PyPI
#
# Usage: ./scripts/release.sh <version> [extra_instructions]
# Example: ./scripts/release.sh 0.1.0
# Example: ./scripts/release.sh 0.2.0 "Focus on Codex support"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Change to project root so all commands work correctly
cd "$PROJECT_DIR"

VERSION="$1"
EXTRA_INSTRUCTIONS="$2"

if [ -z "$VERSION" ]; then
    echo "Usage: $0 <version> [extra_instructions]"
    echo "Example: $0 0.1.0"
    echo "Example: $0 0.2.0 \"Focus on Codex support\""
    exit 1
fi

# Validate version format
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: Version must be in format X.Y.Z (e.g., 0.1.0)"
    exit 1
fi

TAG="v$VERSION"

# Check if tag already exists
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Error: Tag $TAG already exists"
    exit 1
fi

# Check for uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo "Error: You have uncommitted changes. Please commit or stash them first."
    exit 1
fi

# Update version in pyproject.toml (using Python for portability)
echo "Updating version to $VERSION in pyproject.toml..."
python3 -c "
import re
from pathlib import Path
p = Path('pyproject.toml')
content = p.read_text()
content = re.sub(r'^version = \".*\"', f'version = \"$VERSION\"', content, flags=re.MULTILINE)
p.write_text(content)
"

# Update uv.lock to reflect version change
echo "Updating uv.lock..."
uv lock

# Commit version bump (only if changed)
git add pyproject.toml uv.lock
if ! git diff --cached --quiet; then
    git commit -m "Bump version to $VERSION"
else
    echo "Version already at $VERSION, skipping commit"
fi

# Generate changelog
CHANGELOG_FILE=$(mktemp)
trap 'rm -f "$CHANGELOG_FILE"' EXIT

"$SCRIPT_DIR/changelog.sh" "$VERSION" "$EXTRA_INSTRUCTIONS" > "$CHANGELOG_FILE"

echo ""
echo "=========================================="
echo "PROPOSED CHANGELOG FOR $TAG"
echo "=========================================="
cat "$CHANGELOG_FILE"
echo ""
echo "=========================================="
echo ""

# Ask for confirmation
read -p "Accept this changelog and create release $TAG? [y/N] " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Release cancelled. Reverting version bump..."
    git reset --hard HEAD~1
    exit 0
fi

# Create the tag with changelog as message
echo "Creating tag $TAG..."
git tag -a "$TAG" -m "Release $VERSION

$(cat $CHANGELOG_FILE)"

# Clean and build
echo ""
echo "Cleaning old builds..."
rm -rf dist/ build/ *.egg-info

echo "Running tests..."
uv run --extra dev pytest --tb=short
if [ $? -ne 0 ]; then
    echo "Tests failed! Deleting tag..."
    git tag -d "$TAG"
    git reset --hard HEAD~1
    exit 1
fi
echo "All tests passed"

echo ""
echo "Building package..."
uv build
echo "Built dist/agent_session_viewer-$VERSION.tar.gz and .whl"
echo ""

# Show what will be uploaded
echo "Files to upload:"
ls -lh dist/
echo ""

# Final confirmation for PyPI
echo "You are about to publish to PyPI"
echo ""
read -p "Publish agent-session-viewer v$VERSION to PyPI? (yes/N): " -r
echo

if [[ ! $REPLY == "yes" ]]; then
    echo "Aborted PyPI upload. Tag $TAG created locally."
    echo "To delete the tag: git tag -d $TAG"
    echo "To push tag only: git push origin $TAG"
    exit 0
fi

# Upload to PyPI
echo ""
echo "Uploading to PyPI..."
uvx twine upload dist/*

# Push everything
echo ""
echo "Pushing tag and commits to origin..."
git push origin HEAD
git push origin "$TAG"

echo ""
echo "Release $TAG published successfully!"
echo ""
echo "Verify it worked:"
echo "  uvx agent-session-viewer@latest"
echo ""
echo "View on PyPI:"
echo "  https://pypi.org/project/agent-session-viewer/$VERSION/"
echo ""
echo "GitHub release:"
echo "  https://github.com/wesm/agent-session-viewer/releases/tag/$TAG"
