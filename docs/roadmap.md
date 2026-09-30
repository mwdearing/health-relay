# Apple Health AI Bridge Roadmap

HealthRelay is a fork of Apple Health AI Bridge. The iOS app ships as an unsigned IPA on GitHub Releases (betas first, then a promoted stable release) and the receiver is installed from this repository's `main`. Upstream's 1.1.0 coordinated release (Receiver/CLI `1.1.0`, iOS Companion `1.1.0 (39)`, Batch Protocol `health_bridge.batch.v1 (1.0.0)`) is the history this fork started from.

> The sections below are carried over from the upstream Apple Health AI Bridge roadmap and are being revised for HealthRelay. Where they mention App Store distribution, Mac/Xcode-only signing or the upstream release criteria, treat them as upstream history: HealthRelay ships an unsigned IPA (see [versioning](versioning.md)).

## Current state

Works today:

- synthetic fixture ingest into local SQLite;
- local receiver batch ingest;
- read-only CLI, JSON, Markdown, and MCP query surfaces;
- iOS companion source for a self-build HealthKit path;
- read-only sync support for steps, workouts, sleep, and direct quantity samples selected through the native Apple Health permission sheet;
- public brand assets, security guidance, contribution rules, and release criteria.

Current operational constraints:

- real Apple Health sync requires iPhone + Mac/Xcode + signing;
- receiver setup is aimed at technical users or agent-assisted local setup;
- background sync is best-effort and controlled by iOS;
- broad non-quantity HealthKit families are not implemented yet.
- Encrypted iCloud Mailbox remains an explicit opt-in, Mac-only Beta; Direct remains the default and has no automatic fallback to mailbox delivery.

## Near term

1. Keep the public docs short, current, and free of internal planning notes.
2. Keep the synthetic quickstart and MCP smoke path reliable for first-time users.
3. Build every release candidate from the current release tree with a unique build number (the workflow run number) and fresh validation.
4. Keep the tester-facing install and review guidance current for the exact approved build.
5. Improve receiver setup guidance and failure recovery.
6. Keep the [release criteria](../.github/release/criteria.md) passing.

## Later

- broader HealthKit family support beyond direct quantity samples;
- stronger receiver deployment guidance for private networks;
- optional hosted or managed relay design, only after a separate privacy/security review;
- richer onboarding for users who are not already using local agents.

## Non-goals for 1.1.0

- HealthKit write-back;
- medical decisions, scoring, or emergency use;
- hidden hosted sync;
- public remote MCP by default;
- committing real health data, pairing material, or private receiver evidence.
