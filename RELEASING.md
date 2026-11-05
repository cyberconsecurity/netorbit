# Releasing

1. Bump `version` in `custom_components/netorbit/manifest.json` and add a section to `CHANGELOG.md`.
2. Push and wait for the Validate workflow to pass: HACS action, hassfest, tests, secret scan. The HACS action must pass with nothing ignored.
3. Create a GitHub **release** (a tag alone isn't enough; the Marketplace and HACS read releases). Tag `vX.Y.Z` to match the manifest. Mark pre-releases as such.
4. The Release workflow checks the tag matches the manifest.

## Repository settings the Marketplace and HACS check

- Public repository with a description, issues enabled, and topics (for example `home-assistant`, `hacs`, `home-assistant-integration`, `network`, `network-map`, `snmp`).
- A license GitHub can identify (MIT).
- Brand icon at `custom_components/netorbit/brand/icon.png` (Home Assistant 2026.3 and newer show it; older versions show a generic icon).

## Default list

NetOrbit has been in the default list since November 2, 2026 (hacs/default#12978), so people find it by searching the Marketplace or HACS. New releases need no extra step there.

## Manual checks before promoting to Latest

The browser tests cover Chromium and WebKit, but a few things need real devices:

- iPhone, in the Home Assistant iOS app: pinch to zoom the map, then drag while zoomed in.
- Desktop Safari: trackpad pinch over the map.
- Share image and video, in Safari and in the iOS app (Share arrives with PR #16).
- The first-scan animation on a fresh install.
