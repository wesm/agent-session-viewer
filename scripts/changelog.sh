#!/bin/bash
# Generate a changelog since the last release using codex
# Usage: ./scripts/changelog.sh [version] [extra_instructions]
# If version is not provided, uses "NEXT" as placeholder

set -e

VERSION="${1:-NEXT}"
EXTRA_INSTRUCTIONS="$2"

# Find the previous tag
PREV_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
if [ -z "$PREV_TAG" ]; then
    # No previous tag - include all commits including root
    echo "No previous release found. Generating changelog for all commits..." >&2
    COMMITS=$(git log --pretty=format:"- %s (%h)" --no-merges)
    # Use empty tree to diff against for full history
    EMPTY_TREE=$(git hash-object -t tree /dev/null)
    DIFF_STAT=$(git diff --stat "$EMPTY_TREE" HEAD)
else
    RANGE="$PREV_TAG..HEAD"
    echo "Generating changelog from $PREV_TAG to HEAD..." >&2
    COMMITS=$(git log $RANGE --pretty=format:"- %s (%h)" --no-merges)
    DIFF_STAT=$(git diff --stat $RANGE)
fi

if [ -z "$COMMITS" ]; then
    echo "No commits since $PREV_TAG" >&2
    exit 0
fi

# Use codex to generate the changelog
echo "Using codex to generate changelog..." >&2

TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

codex exec --skip-git-repo-check --sandbox read-only -c reasoning_effort=high -o "$TMPFILE" - >/dev/null <<EOF
You are generating a changelog for agent-session-viewer version $VERSION.

IMPORTANT: Do NOT use any tools. Do NOT run any shell commands. Do NOT search or read any files.
All the information you need is provided below. Simply analyze the commit messages and output the changelog.

Here are the commits since the last release:
$COMMITS

Here's the diff summary:
$DIFF_STAT

Please generate a concise, user-focused changelog. Group changes into sections like:
- New Features
- Improvements
- Bug Fixes

Focus on user-visible changes. Skip internal refactoring unless it affects users.
Keep descriptions brief (one line each). Use present tense.
Do NOT mention bugs that were introduced and fixed within this same release cycle.
${EXTRA_INSTRUCTIONS:+

Additional context: $EXTRA_INSTRUCTIONS}
Output ONLY the changelog content, no preamble.
EOF

cat "$TMPFILE"
