# Versioning and compatibility

HealthRelay contains independently versioned components. Always include the component name when presenting a version; the phrase “repository version” is intentionally avoided because a Git checkout is identified by a commit, not by a single product version.

## How this fork releases

| Component | Version source | Distribution |
| --- | --- | --- |
| iOS Companion (HealthRelay app) | Xcode `MARKETING_VERSION`; build number = the `Build unsigned IPA` workflow run number for CI-built IPAs; a self-build uses `CURRENT_PROJECT_VERSION` from the Xcode project | Signed beta builds through TestFlight (access on request, see the README). GitHub Releases tagged `app-v<marketing-version>` carry the notes; releases published before 2026-10-07 still hold their unsigned IPA attachments as history, newer ones hold no IPA. Build and sign it yourself as the alternative ([self-build guide](self-build.md)). |
| Receiver/CLI | `version` in `pyproject.toml` | Installed from this repository's `main`: `uv tool install "git+https://github.com/mwdearing/health-relay.git"`; update with `uv tool upgrade apple-health-ai-bridge`. A pinned receiver release is the alternative: `uv tool install "git+https://github.com/mwdearing/health-relay.git@healthrelay-receiver-<YYYY.MM.DD>"` (see below). |
| Batch Protocol | `health_bridge.batch.v1` | Wire contract only; see below. |

Signed app builds come from the manual `signed-beta` workflow (`main` only, reviewer-approved environment) and reach testers through TestFlight; the build number is the workflow run number plus 100. GitHub Releases no longer receive unsigned IPA files; the `Build unsigned IPA`, `Publish IPA release` and `Promote IPA release` workflows remain for self-builders and for release notes, and a new build is still announced as a **pre-release** first and marked **Latest** only after it has been checked on a device. The receiver can also be installed from a pinned receiver release instead of tracking `main`: a fix is available from `main` as soon as it is merged, and a pinned receiver release freezes one known state of the receiver for users who want it.

### Pinned receiver releases

A pinned receiver release is a GitHub Release whose tag names one immutable commit:

```text
healthrelay-receiver-<YYYY.MM.DD>
```

- The tag is created at a merge commit and is never moved or reused for a different commit.
- The release is **not** marked **Latest**. **Latest** stays the stable app release, chosen through the approval-gated `Promote IPA release` workflow.
- Create the tag only through the release: if `git ls-remote --tags origin healthrelay-receiver-<YYYY.MM.DD>` already returns a tag, stop unless it points at the intended merge commit (`--target` only applies when the tag does not exist yet; a leftover tag at another commit would be published as is).
- Publish it with an explicit `--latest=false`; GitHub otherwise marks a new non-prerelease Latest automatically, which would move the README's `/releases/latest` download link off the stable app:

  ```bash
  gh release create healthrelay-receiver-<YYYY.MM.DD> --repo mwdearing/health-relay \
    --target <merge commit> --title "HealthRelay receiver <YYYY.MM.DD>" \
    --notes-file .github/release/notes-healthrelay-receiver-<YYYY.MM.DD>.md --latest=false
  ```
- The release is not a pre-release; it carries no beta framing.
- The package version in `pyproject.toml` does not change. Helper manifests must carry the release tag `receiver-v<version>` and are validated against the receiver version (`src/health_bridge/mailbox/helper_lifecycle.py`, `cli_mailbox_helper.py`), so a version bump would make Mailbox helper installs require a notarized helper release that does not exist. The tag identifies the commit instead.
- `receiver-v*` and `ios-v*` tags are never pushed from this fork, so `healthrelay-receiver-*` names a receiver release without matching any workflow tag trigger.

Install a pinned receiver release:

```bash
uv tool install "git+https://github.com/mwdearing/health-relay.git@healthrelay-receiver-2026.10.04"
```

Check what you have:

- `health-bridge --version` prints the package version, which stays `1.1.1` for every pinned receiver release.
- The release tag names the commit. Read that commit to see the exact receiver source you installed.

This fork does not push `receiver-v*` or `ios-v*` tags. Those tags trigger the upstream release workflows, which HealthRelay does not use.

### Upstream bookkeeping (not used by this fork)

[`component-versions.json`](../component-versions.json) is machine-readable bookkeeping for upstream's release tooling. Its iOS Companion marketing version follows the Xcode project, but its `receiver-v1.1.1` tag and build number are upstream identifiers, not HealthRelay release numbers. The sections below describe Apple Health AI Bridge's own process: signed `receiver-v*` tags, `ios-v*` checkpoints and App Store distribution gates.

## Inherited from upstream

Everything below this heading describes upstream's process and is not used by HealthRelay. Do not create `receiver-v*` or `ios-v*` tags from it.

### Compatibility model

| Surface | Current version | Public identifier |
| --- | --- | --- |
| Receiver/CLI | `1.1.1` | Release tag `receiver-v1.1.1` |
| iOS Companion | `1.1.1` | Source candidate build `50` |
| Batch Protocol | `1.0.0` | `health_bridge.batch.v1` |

The authoritative machine-readable copy is [`component-versions.json`](../component-versions.json). It declares `release_scope` explicitly rather than deriving scope from equal version numbers. Release validation compares the index with `pyproject.toml`, the Xcode project settings, and the canonical batch fixture. For a tagged release, it also requires the tag target to equal the trusted default-main commit and compares the candidate with that commit’s first-parent baseline. A stale branch or regressing Receiver/CLI, iOS Companion, or Batch Protocol value therefore fails before publication.

### Version surfaces

#### Receiver/CLI

The Python package, receiver service, CLI, and MCP server share one semantic version from `pyproject.toml`. Receiver-only fixes may advance this version without changing the iOS app.

The signed macOS mailbox ACK helper is a Receiver/CLI release asset. Its public manifest binds the helper component version/build, signed zip digest, exact Receiver/CLI tag object/commit/tree, canonical helper source tree, Developer ID publisher and Team ID, non-device-limited provisioning profile, secure timestamp, hardened runtime, notarization, stapled ticket, and Gatekeeper assessment. It is required only for the explicit Mac-only mailbox Beta; Direct installations do not download, install, or validate it.

Starting with the release after `1.0.1`, receiver release tags use the component-scoped form:

```text
receiver-v1.0.2
```

Release notes use the same tag in their filename and install examples.

#### iOS Companion

The user-visible app version is Xcode `MARKETING_VERSION`. App Store Connect additionally requires a monotonically increasing `CURRENT_PROJECT_VERSION` build number. Display both when identifying an installed build:

```text
iOS Companion 1.1.1 (build 50)
```

An iOS source or distribution checkpoint may use a component-scoped tag such as:

```text
ios-v1.1.1-build.50
```

An iOS tag does not publish Receiver/CLI artifacts. App Store release gates remain authoritative for distributed app builds.

#### Batch Protocol

Batch Protocol versions describe the wire contract, not either product artifact. A compatible Receiver/CLI or iOS Companion patch must not bump the protocol merely to align numbers. Breaking protocol changes require a new schema identifier/version and an explicit compatibility or migration policy.

### Compatibility policy

- Do not bump an unchanged component merely to make the numbers match.
- Future release notes must state the exact compatible iOS Companion version/build and Batch Protocol schema identifier/version.
- Historical release notes remain as published. When their prose predates explicit component labels, use the immutable tagged source and `release-metadata.json` to establish exact compatibility rather than rewriting history.
- `component-versions.json` must change in the same commit as any authoritative version source.
- Receiver-only releases still run the iOS source gates and record the compatible app build.
- Receiver-only transitions keep iOS Companion and Batch Protocol values identical to the predecessor baseline while Receiver/CLI advances.
- iOS-only transitions declare `release_scope` as `ios`, keep Receiver/CLI and Batch Protocol identical to the predecessor baseline, and advance the iOS marketing version and/or build with a higher build number.
- Coordinated transitions declare `release_scope` as `coordinated`, advance Receiver/CLI plus at least one other component, and must not regress any component.
- App-only releases verify compatibility with the published receiver before App Store promotion.
- Existing `v1.0.0` and `v1.0.1` tags remain immutable. They are historical receiver release tags and are not renamed.
- Future Receiver/CLI tags use `receiver-v<semver>`; future iOS source/distribution tags use `ios-v<marketing-version>-build.<build>`.

### Release presentation

Use labels such as:

```text
Receiver/CLI 1.1.1
Compatible iOS Companion 1.1.1 (build 50)
Batch Protocol health_bridge.batch.v1 (1.0.0)
```

Avoid an unlabeled phrase such as “Health Bridge 1.1.0” when it could be read as the app, receiver, or protocol version.
