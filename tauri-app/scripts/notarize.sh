#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Agent Session Viewer"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
DIST_DIR="$ROOT_DIR/dist"
DMG_PATH="${1:-}"
MOUNT_PATH=""

# Cleanup trap to detach DMG on exit
cleanup() {
  if [ -n "$MOUNT_PATH" ] && [ -d "$MOUNT_PATH" ]; then
    hdiutil detach "$MOUNT_PATH" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Find or validate DMG path
if [ -n "$DMG_PATH" ]; then
  if [ ! -f "$DMG_PATH" ]; then
    echo "Error: Specified DMG not found: $DMG_PATH"
    exit 1
  fi
else
  # Find newest DMG in dist directory (handles spaces in paths)
  if [ ! -d "$DIST_DIR" ]; then
    echo "Dist directory not found: $DIST_DIR"
    echo "Run build_release.sh first."
    exit 1
  fi

  # Use find -print0 and sort by mtime to handle spaces safely
  DMG_PATH=$(find "$DIST_DIR" -name "*.dmg" -type f -print0 2>/dev/null | \
    xargs -0 ls -t 2>/dev/null | head -1) || true

  # Verify we found a valid DMG (not empty and is a file)
  if [ -z "$DMG_PATH" ] || [ ! -f "$DMG_PATH" ]; then
    echo "No DMG files found in $DIST_DIR"
    echo "Run build_release.sh first."
    echo "Or specify DMG path: ./scripts/notarize.sh /path/to/app.dmg"
    exit 1
  fi
fi

echo "Found DMG: $DMG_PATH"

if [ -z "$NOTARY_PROFILE" ]; then
  echo ""
  echo "Set NOTARY_PROFILE to your keychain profile name."
  echo "If you haven't created one yet, run:"
  echo "  xcrun notarytool store-credentials"
  echo ""
  echo "Then run:"
  echo "  NOTARY_PROFILE=your-profile-name ./scripts/notarize.sh"
  exit 1
fi

echo "Submitting for notarization..."
xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait

echo "Stapling notarization ticket..."
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"

# Verify the app inside the DMG
# Use plist output to correctly handle volume names with spaces
MOUNT_INFO=$(hdiutil attach -nobrowse -readonly -plist "$DMG_PATH")
MOUNT_PATH=$(/usr/bin/python3 -c "
import plistlib
import sys
data = plistlib.loads(sys.stdin.buffer.read())
for entity in data.get('system-entities', []):
    mp = entity.get('mount-point')
    if mp:
        print(mp)
        break
" <<< "$MOUNT_INFO")

if [ -n "$MOUNT_PATH" ] && [ -d "$MOUNT_PATH" ]; then
  APP_PATH="$MOUNT_PATH/${APP_NAME}.app"
  if [ -d "$APP_PATH" ]; then
    echo "Verifying Gatekeeper acceptance..."
    spctl --assess --type execute --verbose "$APP_PATH"
  fi
  # Detach handled by trap
fi

echo ""
echo "Notarization complete: $DMG_PATH"
