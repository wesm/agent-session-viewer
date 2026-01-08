#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Agent Session Viewer"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
DIST_DIR="$ROOT_DIR/dist"

# Find the DMG in dist directory (created by build_release.sh)
DMG_PATH=$(find "$DIST_DIR" -name "*.dmg" -type f 2>/dev/null | head -1)

if [ -z "$DMG_PATH" ] || [ ! -f "$DMG_PATH" ]; then
  echo "DMG not found in $DIST_DIR. Run build_release.sh first."
  exit 1
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
MOUNT_PATH=$(echo "$MOUNT_INFO" | plutil -extract 'system-entities' json -o - - | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(next((e.get('mount-point','') for e in d if 'mount-point' in e),''))")

if [ -n "$MOUNT_PATH" ] && [ -d "$MOUNT_PATH" ]; then
  APP_PATH="$MOUNT_PATH/${APP_NAME}.app"
  if [ -d "$APP_PATH" ]; then
    echo "Verifying Gatekeeper acceptance..."
    spctl --assess --type execute --verbose "$APP_PATH"
  fi
  hdiutil detach "$MOUNT_PATH" >/dev/null 2>&1 || true
fi

echo ""
echo "Notarization complete: $DMG_PATH"
