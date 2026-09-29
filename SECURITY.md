# Security Policy

HealthRelay moves Apple Health data from an iPhone to a receiver you run yourself. It has four parts: an iOS app distributed as an unsigned IPA that you sign with your own Apple developer certificate, a self-hosted receiver and CLI, a read-only MCP server, and a single-use pairing setup page. Everything is user-owned and local-first.

## Supported versions

| Component | Supported |
|---|---|
| iOS app | The release marked **Latest** and the current beta pre-release on the [Releases page](https://github.com/mwdearing/health-relay/releases). Older builds are not patched; update instead. |
| Receiver, CLI and MCP server | The `main` branch of this repository. Its version is in `pyproject.toml`. Fixes land here and are not backported to upstream tags, so install from this repository's `main` rather than a pinned upstream receiver tag. |
| Batch protocol | `health_bridge.batch.v1` |

## Reporting a vulnerability

Do not file sensitive vulnerabilities, secrets, or personal health data in a
public GitHub issue.

Use [GitHub private vulnerability reporting](https://github.com/mwdearing/health-relay/security/advisories/new) on this repository.

Start with the minimum information needed to coordinate privately. Do not attach
real HealthKit values, tokens, pairing material, setup pages, receiver databases,
cursor values, local outbox payloads, private keys, provisioning profiles, or
private endpoint details. If a sensitive artifact is genuinely necessary, first
agree on a safe transfer method with the maintainer.

A useful report should include:

- affected component;
- impact;
- reproduction steps using synthetic data;
- whether HealthKit permissions, receiver authentication, local outbox storage, or MCP output are involved;
- no real tokens, no real health values, and no pairing material.

For non-security setup and product questions, open a [GitHub issue](https://github.com/mwdearing/health-relay/issues). See the [privacy policy](PRIVACY.md) for the current privacy statement.

## What happens next

This is a best-effort project with no bug bounty.

- We acknowledge your report within 7 days.
- You get a status update at least every 30 days until it is resolved.
- Fixes are coordinated and disclosed through a GitHub Security Advisory, and we request a CVE through GitHub when one is warranted.
- We credit reporters in the advisory unless you ask us not to.

## Scope

In scope:

- the iOS app;
- the receiver, CLI and MCP server;
- pairing and the setup page;
- the iCloud Mailbox Beta;
- this repository's release workflows.

Out of scope:

- the upstream project (report it to [apple-health-ai-bridge](https://github.com/roian6/apple-health-ai-bridge) instead; upstream is credited for attribution and is not a channel for this project);
- exposing your receiver to the public internet against the deployment guidance below;
- a phone or computer that is already compromised;
- what third-party AI or MCP clients do with data after they receive it;
- bugs in Apple platforms;
- findings that can only be demonstrated with real users' health data.

## Verify what you install

- Get the app only from the [Releases page](https://github.com/mwdearing/health-relay/releases), from a `Build unsigned IPA` workflow run in this repository, or by building it yourself from a checkout you trust.
- For a release download, check the IPA against the attached checksum (workflow artifacts include one too): `sha256sum -c HealthRelay-unsigned.ipa.sha256` (on macOS: `shasum -a 256 -c HealthRelay-unsigned.ipa.sha256`).
- Each release names the commit and the "Build unsigned IPA" workflow run that built it.
- You sign the IPA with your own Apple developer certificate. Keep that signing identity private.

## Deploy the receiver safely

- Run the receiver on localhost or a private network such as a tailnet. A same-LAN setup uses plain HTTP, so health payloads and the device credential are visible to that network: use it only on a trusted, isolated LAN, never a shared or guest network. Do not open a public port without separate hardening.
- The setup page is a private file (mode 0600) holding an invitation that is valid for 20 minutes and works once. Open it only on a trusted screen and delete it after pairing. A legacy pairing (`--legacy-v1`) instead holds a long-lived credential that does not expire: revoke it when you no longer need it.
- The device credential is stored in the iPhone Keychain.
- Protect the receiver database file like any health record.

## Security model

- HealthKit access is read-only. The app never writes to Apple Health.
- MCP and CLI tools are read-only query surfaces over the local store, with no raw SQL.
- The project does not intentionally include telemetry, analytics, advertising hooks, hidden cloud upload, or third-party AI calls.
- Direct is the default transport and never falls back to another one. The encrypted iCloud Mailbox is an explicit opt-in, Mac-only Beta with application-layer encryption and signed delivery and ACK semantics.
- The mailbox path uses a user-owned iCloud container and user-owned receiver; the optional receiver LaunchAgent runs only for the Mac user who explicitly installs it.

## Protections on this repository

- Private vulnerability reporting.
- Secret scanning with push protection.
- Dependabot alerts and security updates.
- CodeQL code scanning.
- Required CI on `main`.

## Sensitive data

Do not post any of the following in public issues, pull requests, screenshots, logs, docs, or chats:

- real HealthKit exports or sample values;
- screenshots containing health values or identifiable sources;
- receiver SQLite databases;
- bearer tokens, token hashes, pairing URLs, pairing deep links, or setup-page contents;
- local outbox payloads;
- sync cursor values;
- private-network endpoint details when they identify a real deployment.

Use synthetic fixtures and redacted aggregate counts instead.

HealthRelay is a fork of [apple-health-ai-bridge](https://github.com/roian6/apple-health-ai-bridge) by roian6; upstream is credited for attribution only and is not a support or reporting channel for this project.
