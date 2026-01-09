# Agent Session Viewer - Tauri App

Native macOS app for browsing and searching AI coding sessions.

## Development

```bash
# Start dev server (required for hot reload)
cd tauri-app/src
python3 -m http.server 1420

# In another terminal, run the app
cd tauri-app
npx @tauri-apps/cli dev
```

## Building

### Development/Testing Build (ad-hoc signing)

```bash
npx @tauri-apps/cli build
```

This produces an ad-hoc signed app suitable for local testing.

### Release Build (Developer ID signing)

For distribution outside the App Store, you need a Developer ID certificate:

```bash
# Set your signing identity
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"

# Build and create DMG
./scripts/build_release.sh 0.1.0
```

The signing identity must match a certificate in your keychain. Find yours with:
```bash
security find-identity -v -p codesigning | grep "Developer ID"
```

### Notarization

After building a signed release:

```bash
# First time: store your Apple credentials
xcrun notarytool store-credentials

# Notarize the DMG
export NOTARY_PROFILE="YourProfileName"
./scripts/notarize.sh
# Or specify DMG path explicitly:
./scripts/notarize.sh dist/Agent\ Session\ Viewer-0.1.0.dmg
```

## CI

CI builds use ad-hoc signing (`APPLE_SIGNING_IDENTITY="-"`) which doesn't require certificates. These builds are for testing only and won't pass Gatekeeper on other machines.

## Configuration

- `src-tauri/tauri.conf.json` - App configuration
- `src-tauri/entitlements.plist` - macOS entitlements
- `signingIdentity: null` in config means signing is controlled by `APPLE_SIGNING_IDENTITY` env var
