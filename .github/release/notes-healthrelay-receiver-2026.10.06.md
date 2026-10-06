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

- **Intake-context routes.** Unchanged by this release and still **off by default**: start the
  receiver with `--enable-intake-context` to serve `/v1/intake-context/capabilities` and
  `/v1/intake-context/batches`. While off, both paths answer 404 like any unknown path, and each CLI
  start reports whether they are enabled.

Nothing here changes the wire contract, but an ordinarily paired shipping app does see one change, and
it is a repair. `ElectrocardiogramSyncBatchFactory` and `MedicationDoseEventSyncBatchFactory` emit
their rows under the legacy `apple_health.phone` key. Before this release the receiver rewrote the
**declared** `sources` entry to the caller's canonical key but not the rows that family carries, so
storage could not resolve a row source and `/v1/batches` answered 500; the outbox then retried the
batch indefinitely. This release rewrites those rows too, so the same batch is now accepted with
`202` and its records are stored under the canonical source. Callers already sending the canonical
key are unaffected.

The new refusal is separate, and it applies only to a claim that cannot be legitimate: a batch
carrying an export-keyed ECG or medication **row** or **tombstone** is now refused with
`403 source_principal_mismatch`. No shipping client emits one — the export importer carries
`lab_results` alone.

How that refusal is handled depends on how the batch reached the receiver. Over **Mailbox
delivery** it is a terminal receipt, so the item is parked for a person rather than retried. Over
**Direct delivery** the app does retry: `DirectUploadFinalizer.finish` returns `.retained` for any
non-2xx response, 403 included, and the foreground `FileOutbox` path likewise leaves the item
pending. A Direct batch that trips this refusal therefore keeps retrying until the claim changes;
`lab_result` tombstones keep the export exemption and are unaffected.

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
