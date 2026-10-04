# HealthRelay receiver release healthrelay-receiver-2026.10.04

**Receiver-only pinned release.** This release does not change the receiver package version: it stays `1.1.1`, the version the Mailbox helper manifests are bound to. The tag, not the version number, identifies the code.

| Field | Value |
| --- | --- |
| Tag | `healthrelay-receiver-2026.10.04` |
| Base commit | `4677570` |
| Receiver/CLI package version | `1.1.1` (unchanged) |
| Batch Protocol | `health_bridge.batch.v1` (`1.0.0`, unchanged) |

The tag names one immutable commit. It is not marked **Latest** (**Latest** stays the stable app release) and it is not a pre-release. `receiver-v*` and `ios-v*` tags are never pushed from this repository, so this tag matches no workflow tag trigger.

## What this receiver adds that matters to plugin users

- A read-only MCP tool, `get_intake_evidence_v1`, that answers one question per intake component: is the HealthKit sample the producer claims stored here, and is it the right kind of sample from the right writer. It reports `verified`, `pending`, `unlinked` or `mismatch` per component link, with identifiers, metadata and the producer-supplied component amount and unit, never HealthKit sample values ([reference](https://github.com/mwdearing/health-relay/blob/healthrelay-receiver-2026.10.04/docs/reference/intake-evidence.md)). The same page is available in a terminal as `health-bridge query intake-evidence`.
- Intake-context routes for producers that push intake context. They are **off by default**; start the receiver with `--enable-intake-context` to serve them. While off, both paths answer 404 like any unknown path, and each CLI start reports whether they are enabled.
- A guided intake-setup command, `health-bridge receiver intake-setup`, that does the whole database half of intake setup in one safe step: it registers the producer, issues its intake token into a mode-0600 private file that is never printed, and prints the restart, smoke-check and secret-handling steps in order. Registration is idempotent; issuing a token is not, so the command as a whole is not repeatable ([guide](https://github.com/mwdearing/health-relay/blob/healthrelay-receiver-2026.10.04/docs/pairing.md)).

## Compatibility

- Batch Protocol: `health_bridge.batch.v1` (`1.0.0`). No wire change; a compatible receiver patch does not bump the protocol.
- HealthRelay app: intake evidence does not need a particular app version. It matches each claimed component against the samples this receiver stored, using the source provenance every supported app already sends. The `1.2.29` beta started forwarding allowlisted intake metadata with samples; the `1.2.30` beta or later is recommended because it also restores background delivery.
- Mailbox helper: unchanged at `1.1.1`. The signed Mac helper is validated against the receiver version, and helper manifests must carry the release tag `receiver-v<version>`. That is why the package version stays `1.1.1` and this release is identified by its tag instead of by a version bump. The Mailbox helper release is not republished here.
- Agent plugin: `hermes-healthrelay` `0.3.0` or later exposes the `get_intake_evidence_v1` tool to agents. Earlier plugin versions expose the other nine read-only tools only.

## Install or upgrade the receiver

```bash
uv tool install "git+https://github.com/mwdearing/health-relay.git@healthrelay-receiver-2026.10.04"
```

To move between pinned receiver releases, install the tag you want; to go back to tracking `main`, install without a tag and update with `uv tool upgrade apple-health-ai-bridge`:

```bash
uv tool install --force "git+https://github.com/mwdearing/health-relay.git@healthrelay-receiver-<YYYY.MM.DD>"
uv tool install --force "git+https://github.com/mwdearing/health-relay.git"
```

## Verify what you have

- `health-bridge --version` prints the package version, which is `1.1.1` for this release.
- The release tag names the commit, which is the exact receiver source installed.

## Privacy and operating boundaries

- HealthKit access remains read-only, and the MCP server stays read-only.
- The intake evidence tool returns identifiers, link status and metadata, plus the component amount and unit the producer supplied with the intake (for example `95 mg`). It never returns HealthKit sample values.
- No telemetry, advertising, data broker, or third-party AI upload path is added.
