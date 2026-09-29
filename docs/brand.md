# HealthRelay visual identity

HealthRelay is a fork of Apple Health AI Bridge (see `FORK.md`, `NOTICE`). The upstream names
"Apple Health AI Bridge", "Health Bridge for AI" and "Health Bridge" and their brand assets are not
licensed to this fork and are not used for the product. `health_bridge` / `health-bridge` remain code
identifiers only.

## Naming
- Product, app display name and repository: **HealthRelay** (one word, capital H and R).
- Bundle id: the tracked project keeps the neutral `com.example.*` placeholder; the real id is passed as `BUNDLE_ID` at build time.
- Do not imply Apple affiliation.

## Mark
Deep-teal gradient tile (#0B3D4A to #07222E), white ECG-style pulse line, mint (#5EEAD4) chevron.
Assets and their uses are listed in `assets/brand/README.md`; regenerate with
`python3 tools/generate_healthrelay_icon.py` (Pillow only).

## Lockup
Mark plus the **HealthRelay** wordmark and the line "Apple Health to your own receiver".
Two files share the same art: `healthrelay-lockup.png` (ink #111827, slate #4B5563) for light
backgrounds and `healthrelay-lockup-dark.png` (#F0F6FC, #AEB8C2) for dark ones. Serve both with a
`<picture>` element so each viewer gets the readable one; never place the ink lockup on a dark
background.
