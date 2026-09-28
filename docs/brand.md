# HealthRelay visual identity

HealthRelay is a private fork of Apple Health AI Bridge (see `FORK.md`, `NOTICE`). The upstream names
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
