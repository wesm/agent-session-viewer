# macOS Code Signing and Notarization

This document explains how to sign and notarize the Agent Session Viewer app for distribution.

## Local Development (Ad-hoc Signing)

For local testing, the app is signed with ad-hoc signing (`-` identity):

```bash
cd tauri-app
npx @tauri-apps/cli build
```

The resulting app will be at:
```
src-tauri/target/release/bundle/macos/Agent Session Viewer.app
```

## Production Distribution

For distribution to other users, you need:

1. **Apple Developer Account** ($99/year)
2. **Developer ID Application certificate**
3. **App-specific password** for notarization

### Step 1: Set Up Certificates

1. Log into [Apple Developer Portal](https://developer.apple.com)
2. Create a "Developer ID Application" certificate
3. Download and install in your Keychain

Find your signing identity:
```bash
security find-identity -v -p codesigning
```

### Step 2: Create App-Specific Password

1. Go to [appleid.apple.com](https://appleid.apple.com)
2. Sign in and go to "App-Specific Passwords"
3. Generate a new password for "Agent Session Viewer Notarization"

### Step 3: Build with Proper Signing

Set environment variables and build:

```bash
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_ID="your@email.com"
export APPLE_PASSWORD="xxxx-xxxx-xxxx-xxxx"  # App-specific password
export APPLE_TEAM_ID="XXXXXXXXXX"

cd tauri-app
npx @tauri-apps/cli build
```

Alternatively, use API Key authentication:
```bash
export APPLE_API_KEY="XXXXXXXXXX"
export APPLE_API_ISSUER="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export APPLE_API_KEY_PATH="/path/to/AuthKey_XXXXXXXXXX.p8"
```

### Step 4: Verify Signing

```bash
# Check code signature
codesign -dv --verbose=4 "target/release/bundle/macos/Agent Session Viewer.app"

# Verify notarization
spctl -a -vv "target/release/bundle/macos/Agent Session Viewer.app"
```

## CI/CD (GitHub Actions)

For automated builds, store secrets in GitHub repository settings:

- `APPLE_CERTIFICATE` - Base64-encoded .p12 certificate
- `APPLE_CERTIFICATE_PASSWORD` - Password for the .p12 file
- `APPLE_ID` - Your Apple ID email
- `APPLE_PASSWORD` - App-specific password
- `APPLE_TEAM_ID` - Your team ID

Example workflow is provided in `.github/workflows/build-tauri.yml`.

## Entitlements

The app uses the following entitlements (`entitlements.plist`):

- `com.apple.security.files.user-selected.read-only` - Read user-selected files
- `com.apple.security.files.user-selected.read-write` - Read/write files
- `com.apple.security.network.client` - Network access (for future features)

## Troubleshooting

### "App is damaged and can't be opened"
The app wasn't signed or notarized properly. Try:
```bash
xattr -cr "/Applications/Agent Session Viewer.app"
```

### "Developer cannot be verified"
1. Right-click the app and select "Open"
2. Click "Open" in the dialog
3. The app will be allowed to run in the future

### Notarization failed
Check the notarization log:
```bash
xcrun notarytool log <submission-id> --apple-id $APPLE_ID --password $APPLE_PASSWORD --team-id $APPLE_TEAM_ID
```
