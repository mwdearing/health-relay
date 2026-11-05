# Changelog

Recent changes to the HealthRelay receiver and app. The newest entry is first. This file lists what changed in the code and its contracts.

## Unreleased

- **#170**: each source owns its health type rows, keyed by `(source_id, type_code)`. A batch writes only the sources it declares, and stored aliases are capped at 64 per owned type. Migration `016_source_scoped_types` rebuilds `health_types`, `health_type_aliases` and `samples`. Closes #135.
- **#169**: migration `015_range_guards` adds database triggers that refuse reversed intervals and negative workout duration, energy and distance. Schema reference: `docs/reference/sqlite-v1.md`.
- **#168**: `POST /v1/batches` allows 60 requests per 60 seconds per batch token, then returns 429 `rate_limited` with `Retry-After`. Failed authentication is not counted. The budget is set by `batch_rate_limit_count`.
- **#167**: the iCloud Mailbox Beta is out of support scope in the README and SECURITY.md.
- **#165**: a deadline counts only for a request the client started.
- **#164**: the unsigned IPA release workflows are removed. `build-ipa.yml` remains as the self-build path. Releases are not an install path. Older `app-v*` releases keep their unsigned IPAs as unmaintained history.

## Open fix PRs (not merged)

From the security, bug and usability review of 2026-11-05. Each line moves under Unreleased when its PR merges, and is dropped if the PR closes unmerged.

- **#244**: when the receiver already stores a newer Sleep baseline reset epoch, the mailbox ACK is a terminal `sleep_baseline_reset_epoch_stale` carrying `minimum_reset_epoch`, not a retryable receipt that never succeeds. Delivery schema and docs updated. Closes #223.
- **#243**: `Build unsigned IPA` stamps the marketing version from `component-versions.json`, as `Signed beta upload` does. Signed-beta failure diagnostics read only error lines, and the archive is checked before upload. The docs no longer say which release GitHub marks Latest. Closes #221, #222.
- **#242**: the app blocks plain HTTP to qualified hostnames (ATS) and says so, and `health-bridge setup` warns about such URLs. Plain HTTP needs an IP address, a single-label name or a `.local` name. VoiceOver reads the receiver host on the review screen. The unused iCloud key-value entitlement and an undeclared disk-space call are removed. Closes #217, #218, #219.
- **#241**: `mailbox service` accepts the `uv tool` symlink for `--executable`, resolves it once and records the real path. Validation errors name the rejected field and reason, with no path. Closes #211.
- **#240**: `receiver start` reports a bind failure with a clean exit 1 and accepts an IPv6 `--host`. The pairing page's renewal command uses the real `--db`. An unsafe database parent directory gives guidance, not a traceback. The setup guide link points to this repository. Closes #207, #208.
- **#239**: Sync Now ends in an error when the receiver refuses every upload, and says "paused by iOS" when iOS stops the run. Stale background-upload ledger records are cleared at launch. Closes #236, #237.
- **#238**: deleting an ECG recording in Health sends an `electrocardiogram` tombstone (`hk-ecg-<uuid>`) from an anchored query, cursor `foreground_electrocardiogram_anchor`. Closes #226.
- **#235**: step, ECG and lab-import uploads are split to fit the receiver's 5,000,000-byte limit. An upload the receiver permanently rejects (413, 422, 400 `invalid_json`/`invalid_batch`) is moved aside instead of blocking the outbox, and can be discarded under Connection. Closes #213, #214.
- **#234**: clearer messages when data-sharing consent is off and when pairing input fails local checks. The consent screen's "Accept Again" button is now "Agree". Closes #215, #216.
- **#233**: `mailbox import --once` checks the database before importing. A missing, uninitialized or unreadable database and permanent mailbox errors exit 3 with a fixed `error_code`; retryable conditions still exit 2. Closes #212.
- **#232**: `receiver intake-setup --rotate` refuses a secret file whose token belongs to another owner or producer, so it cannot revoke another producer's credential. Closes #224.
- **#231**: a medication dose event replaced by a corrected log is uploaded from the anchored read. Before, a replacement older than the replay window was missed and only the tombstone went out. Closes #225.
- **#230**: `public-release-audit.py --strict` runs the built-in patterns even when the local denylist is missing, including under the CI-only opt-out for a missing denylist. The published support address is allowed only in its own files. Closes #220.
- **#229**: the `query` CLI and the MCP tools share one argument validator, so bad dates and timestamps fail cleanly. `daily-summary` and `get_daily_summary` accept at most 366 days per call. Closes #209, #210.
- **#228**: `receiver smoke` no longer follows redirects or uses `HTTP(S)_PROXY`, so the bearer token goes only to the given URL. It adds `--token-file` (or `-` for stdin), deprecates `--token`, and reports a non-JSON or malformed response as a clean failure. Closes #205.
- **#227**: `receiver revoke-token` exits 1 with nothing on stdout when the database is missing, no active token has the prefix, or several do. It no longer reports success for a typo or a second revocation, and it never creates a database. Closes #206.

## Migrations

Migrations run in order through `016`. A running receiver applies a migration only after it restarts from a checkout that includes it.
