# HealthRelay receiver release healthrelay-receiver-2026.10.06

**Receiver-only pinned release.** This release does not change the receiver package version: it stays
`1.1.1`, the version the Mailbox helper manifests are bound to. The tag, not the version number,
identifies the code.

| Field | Value |
| --- | --- |
| Tag | `healthrelay-receiver-2026.10.06` |
| Base commit | `f28c4ca` |
| Receiver/CLI package version | `1.1.1` (unchanged) |
| Batch Protocol | `health_bridge.batch.v1` (`1.0.0`, unchanged) |

The tag names one immutable commit. It is not marked **Latest** (**Latest** stays the stable app
release) and it is not a pre-release. `receiver-v*` and `ios-v*` tags are never pushed from this
repository, so this tag matches no workflow tag trigger.

## What this receiver adds that matters to plugin users

- **Source binding now covers electrocardiograms and medication dose events.** A device-bound token's
  batch was bound to its installation for samples, workouts, sleep, deletions, lab results and cursors,
  but not for these two families, and their upsert key is `(source_id, client_record_id)` — so one
  installation could write into, and overwrite, another's rows. Both families are now part of the
  claimed-key check and are rewritten to the caller's canonical source.
- **An export claim on a live-only family is refused.** `apple_health.export` is one shared, unscoped
  source, and the ownership check exempts it so that a manual import is not rejected. That exemption is
  sound only for the families a manual import can carry, and the export importer carries `lab_results`
  alone: it reads clinical records, while ECG and medications sync live. An export-keyed ECG or
  medication **row** is now refused, and so is an export-keyed **tombstone** for either family, on every
  token kind — including a legacy token with no installation binding, whose batches are returned
  unchanged and would otherwise have carried one through to storage. A tombstone names the same shared
  partition as a row, so before this a legacy token could delete export-keyed ECG or medication rows
  left by an earlier receiver. `lab_result` tombstones keep the exemption.

- **The read-only intake-evidence tool is unchanged.** `get_intake_evidence_v1` still answers one
  question per intake component: is the HealthKit sample the producer claims stored here, and is it
  the right kind of sample from the right writer. It reports `verified`, `pending`, `unlinked` or
  `mismatch` per component link, with identifiers, link status and metadata plus the component
  amount and unit the producer supplied — and never a HealthKit sample value. This release does not
  change it.

Nothing here changes the wire contract, and none of it changes what a correctly behaving client sees:
no shipping client emits an export-keyed ECG or medication row. A batch that does is now refused with
`403 source_principal_mismatch`, which the delivery worker parks for a person rather than retrying.

## Compatibility

- Batch Protocol: `health_bridge.batch.v1` (`1.0.0`). No wire change; a compatible receiver patch does
  not bump the protocol.
- HealthRelay app: unaffected. The app never sends an export claim for these families.
- Mailbox helper: unchanged at `1.1.1`. The signed Mac helper is validated against the receiver version
  and helper manifests must carry the release tag `receiver-v<version>`, which is why the package
  version does not move and this release is identified by its tag instead.
- Agent plugin: unchanged. No tool is added or removed.

## Install or upgrade the receiver

```bash
uv tool install "git+https://github.com/mwdearing/health-relay.git@healthrelay-receiver-2026.10.06"
```

## Verify what you have

```bash
cat "$(uv tool dir)"/apple-health-ai-bridge/lib/python*/site-packages/apple_health_ai_bridge-*.dist-info/direct_url.json
```

`vcs_info.requested_revision` must be `healthrelay-receiver-2026.10.06`, and `vcs_info.commit_id` must
equal the commit the release tag points to (`git ls-remote
https://github.com/mwdearing/health-relay.git refs/tags/healthrelay-receiver-2026.10.06`).

## Privacy and operating boundaries

- HealthKit access remains read-only, and the MCP server stays read-only.
- This release changes which source a stored row is filed under, and refuses a claim it cannot verify.
  It adds no new data surface, no new tool and no new read path.
- No telemetry, advertising, data broker, or third-party AI upload path is added.
