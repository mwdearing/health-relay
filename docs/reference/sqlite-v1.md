# SQLite Schema v1 Notes

The local SQLite schema is defined by migrations under
`src/health_bridge/storage/migrations/`. The schema is the destination for
synthetic fixture ingest, receiver batches, and read-only query responses.

Tables:

- `schema_migrations`: applied migration identifiers and timestamps.
- `sync_runs`: one row per ingest attempt with status, counts, sync-window
  bounds when available, and redacted errors.
- `sources`: source registry keyed by `source_key`.
- `health_types`: bridge type registry keyed by `type_code`.
- `health_type_aliases`: source or platform aliases for a bridge type.
- `samples`: scalar and interval observations.
- `workouts`: workout records.
- `electrocardiograms`: ECG recordings (classification, symptoms status, average heart rate, sampling frequency, voltage count, optional voltages as a JSON array in `voltages_json`; a summary-only replay keeps stored voltages). Added by migration `010_electrocardiograms`, which also adds `sync_runs.electrocardiogram_count`.
- `medication_dose_events`: medication dose events (medication name, optional concept key, status enum + raw, start time, optional scheduled time, optional dose/unit). Added by migration `011_medication_dose_events`, which also adds `sync_runs.medication_dose_event_count`.
- `lab_results`: clinical lab observations from a manual Health export (LOINC code, name, category, effective date, numeric or text value, unit, reference range). Added by migration `012_lab_results`, which also adds `sync_runs.lab_result_count`. Unique on `(source_id, client_record_id)`.
- `delivery_receipts`: delivery receipts for the mailbox transport (migration `008_delivery_receipts`).
- `sleep_sessions`: sleep session records.
- `sleep_stage_intervals`: intervals attached to sleep sessions.
- `deleted_records`: tombstones by record family and client identity.
- `sync_cursors`: latest cursor state per source.
- `sleep_baseline_namespaces`: every anchored sleep reset namespace seen per source and whether a non-empty authoritative baseline was applied. Replayed older namespaces are ignored; an empty reset remains non-destructive and pending until the same namespace supplies readable sleep sessions.
- `receiver_tokens`: receiver bearer-token hashes, lookup prefixes, usage timestamps,
  and revocation state. Legacy v1 rows may retain a short raw token prefix for
  compatibility; v2 rows use a lookup prefix derived from the token hash.
- `pairing_invitations`: short-lived invitation secret/code hashes, expiry,
  failed-attempt counters, and redemption/revocation state.
- `receiver_devices`: domain-separated installation-ID hashes, user-facing
  labels, platform, pairing timestamps, and device revocation state.
- `receiver_token_devices`: one device mapping for each v2 receiver token.
- `pairing_invitation_redemptions`: immutable invitation-to-device/token result
  mapping used for exact response-loss retries.

Intake-context tables (migration `013_intake_context`, see
[the intake-context contract](intake-context-v1.md)):

- `intake_producers`: registered intake producers and their owner.
- `intake_state`: current state per intake identity.
- `intake_revisions`: committed intake revisions.
- `intake_compound_facts`: compound facts of a revision.
- `intake_blend_members`: members of a blend fact.
- `intake_projection_snapshots`: projection (link) snapshots by sequence.
- `intake_sample_links`: claims about HealthKit sample identities (sample UUID, type, sync identifier and version, disposition) made by an intake component. There is no foreign key to `samples`: a row can exist before the sample arrives, and the evidence query reports it as pending until then.
- `intake_operation_receipts`: receipts for operations the receiver could key, unique on `(owner_id, producer_id, operation_id)` (not `operation_id` alone), used for duplicate and conflict detection. It is not one row per submitted operation: batch or schema failures and digest mismatches return results without writing a receipt, and a `retryable_failure` is never stored.
- `intake_tombstones`: deletions that supersede older revisions.

`intake_context_tokens` (migration `014_intake_context_tokens`): intake-only
bearer tokens (owner, producer, label, token hash, lookup prefix, revocation).
These tokens are separate from `receiver_tokens`.

Migrations from `010_` on: `010_electrocardiograms`,
`011_medication_dose_events`, `012_lab_results`, `013_intake_context`,
`014_intake_context_tokens`.

Idempotency keys:

- `sources.source_key`
- `health_types.type_code`
- `health_type_aliases(type_code, alias)`
- `samples(source_id, type_code, client_record_id)`
- `workouts(source_id, client_record_id)`
- `electrocardiograms(source_id, client_record_id)` and `electrocardiograms(source_id, start_time)`
- `medication_dose_events(source_id, client_record_id)` and `medication_dose_events(source_id, start_time)`
- `lab_results(source_id, client_record_id)`
- `intake_context_tokens.token_hash`
- `sleep_sessions(source_id, client_record_id)`
- `sleep_sessions(source_id, start_time)` for logical-session revision
  reconciliation. The anchored iOS lane assigns a never-reused namespaced,
  monotonic identity to each changed grouped revision and sends the displaced
  identity as a tombstone in the same payload. That explicit tombstone makes a
  legitimate shorter-end or equal-window stage correction authoritative;
  unmarked shorter stale replays remain rejected. The receiver tombstones every
  displaced or rejected identity before deleting it, so a late partial replay
  cannot resurrect after the winning revision is deleted. A first non-empty
  `anchored_sleep_sync` baseline transactionally tombstones pre-manifest rows
  absent from that baseline before inserting the current grouped sessions.
  Empty initial reads never trigger this reset. The migration serializes under
  `BEGIN IMMEDIATE`, and historical deduplication, tombstone creation, index
  creation, and migration bookkeeping commit atomically.
- `sleep_stage_intervals(sleep_session_id, stage, start_time, end_time)`
- `deleted_records(source_id, record_family, client_record_id)`
- `sync_cursors(source_id, cursor_kind)`
- `receiver_tokens.token_hash`
- `pairing_invitations.pairing_invitation_id`
- `pairing_invitations.invitation_secret_hash`
- `pairing_invitations.invitation_code_selector`
- `receiver_devices.installation_id_hash`
- `receiver_token_devices.receiver_token_id`
- `pairing_invitation_redemptions.pairing_invitation_id`
- `pairing_invitation_redemptions.receiver_token_id`

Pairing and authentication storage rules:

- SQLite never stores a raw v2 device credential, installation UUID, invitation
  secret, or full manual invitation code.
- The manual code's public five-character selector is stored for bounded lookup;
  its secret portion is verified with a salted `scrypt` hash.
- v2 token lookup prefixes are hash-derived, so the raw credential prefix is not
  persisted. Authentication still checks legacy v1 raw-prefix rows during the
  compatibility window, then verifies the complete token hash with a
  constant-time comparison.
- Invitation consumption, device upsert, previous-device-token revocation, new
  token insertion, and mapping insertion execute inside one `BEGIN IMMEDIATE`
  transaction. A partial failure rolls back the complete redemption.
- An exact retry is accepted only when the invitation, installation-ID hash, and
  token hash match the prior redemption mapping.
- Device revocation selects exactly one active hash-derived device reference and
  revokes the device plus all mapped active tokens in one transaction.

All future query surfaces should read from SQLite without mutation by default.
Authentication, pairing redemption, and explicit revocation commands are the
reviewed mutation exceptions.
