# HealthRelay receiver release healthrelay-receiver-2026.11.24

**Receiver-only pinned release.** This release does not change the receiver package version: it stays
`1.1.1`, the version the Mailbox helper manifests are bound to. The tag, not the version number,
identifies the code.

| Field | Value |
| --- | --- |
| Tag | `healthrelay-receiver-2026.11.24` |
| Base commit | `c0fe70d` |
| Previous pinned release | `healthrelay-receiver-2026.10.12` |
| Receiver/CLI package version | `1.1.1` (unchanged) |
| Batch Protocol | `health_bridge.batch.v1` (`1.0.0`, unchanged) |
| Database migrations | through `021` (the previous pinned release ended at `014`) |

The tag names one immutable commit. It is not marked **Latest** (**Latest** stays the stable app
release) and it is not a pre-release. `receiver-v*` and `ios-v*` tags are never pushed from this
repository, so this tag matches no workflow tag trigger.

## Read before upgrading from 2026.10.12

- **The first start applies seven migrations (`015` to `021`) to an existing database.** `015` adds
  triggers that refuse reversed intervals and negative workout values. `016` rebuilds `health_types`,
  `health_type_aliases` and `samples` so each source owns its type rows. `017` to `021` carry upstream's
  receiver `1.2.0` schema forward (see below). The receiver prints no progress while it migrates; give a
  large database a short time before judging the start.
- **Back up the database first**, for example `sqlite3 <db> ".backup <copy>"` with the receiver running or
  stopped. The receiver keeps its own pre-migration copy only for the delivery-receipt migration.
- **Going back to `2026.10.12` after the first start is not supported without restoring that backup.**
  Migration `016` changes how the type catalog is keyed, which older code does not expect.
- **`receiver create-pairing --legacy-v1` is gone** (#249). It minted a long-lived bearer token inside the
  pairing bundle. Pairing uses the single-use invitation flow; update any script that passed the flag.
- **`POST /v1/batches` is limited to 120 requests per 60 seconds per batch token**, then answers `429
  rate_limited` with `Retry-After`. Failed authentication is not counted. A normal sync, including a
  backlog flush, stays under the cap.

## What this release changes

- **Request limits.** The receiver caps concurrent connections (64 in total, 8 per client address), puts a
  30-second deadline on the request head and a 120-second wall-clock budget on the body (`408` on expiry),
  and closes connections over a cap without a response. A deadline counts only for a request the client
  actually started (#147, #165). Each of the nine top-level batch arrays is capped at 5,000 entries
  (`422`), and health type declaration fields are bounded (#148, #162).
- **Malformed batches are client errors, not crashes.** Integers outside the SQLite 64-bit range, over-long
  sleep reset cursors, and rows that name an undeclared source or type code are refused with a `4xx` before
  anything is written, instead of answering `500` or recording a failed sync run. One bad delivery no
  longer stops the mailbox worker (#149, #155).
- **Rejected requests are counted.** The receiver logs a periodic one-line summary of requests it rejected
  (#156).
- **Stored data is harder to damage.** A later batch that redeclares a health type keeps the stored
  metadata (#161). A replay that sends the fallback medication name keeps the stored name (#173). Database
  triggers refuse reversed intervals and negative workout duration, energy and distance (`015`, #169).
  Each source owns its own type rows and stored aliases are capped at 64 per owned type (`016`, #170).
- **`health-bridge prune-catalog`** removes type rows that no stored sample uses, are older than a
  threshold (default 30 days) and are not named by a deletion tombstone of the same source. Without
  `--apply` it only counts, read-only (#171, #172).
- **Less work per request.** Token authentication initializes the database once per file per process,
  not on every call (#204).
- **Purge is complete.** `receiver purge` also removes the pre-migration database copy and its checksum
  guard (#148).
- **CLI and query behaviour.**
  - `query` and the MCP tools share one argument validator, so bad dates and timestamps fail cleanly;
    `daily-summary` and `get_daily_summary` accept at most 366 days per call (#229).
  - `receiver smoke` no longer follows redirects or uses `HTTP(S)_PROXY`, so the bearer token goes only to
    the URL it was given; it adds `--token-file` and deprecates `--token` (#228).
  - `receiver revoke-token` exits `1` with nothing on standard output when nothing was revoked, and never
    creates a database (#227).
  - `receiver intake-setup --rotate` refuses a secret file whose token belongs to another owner or
    producer (#232).
  - `mailbox import --once` refuses a missing, uninitialized or unreadable database before importing and
    reports permanent setup errors with exit `3` and a fixed `error_code`; busy, locked and transient
    errors, a helper that timed out or failed, and a mismatched receipt stay exit `2` (#233, #267). A
    database error that a rerun will not clear, raised during the import pass itself, exits `3` with
    `import_database_error`. The mailbox commands belong to the macOS iCloud Mailbox Beta, which is out of
    support scope (#167).
- **Upstream sync through `receiver-v1.2.0`.** Two upstream receiver fixes are taken, and nothing else
  changes behaviour (#264):
  - a daily aggregate counts toward `daily_activity_totals` only when it is complete and its start and end
    are the exact calendar-day boundaries for its time zone; partial aggregates stay in `sample_statistics`
    and `sample_counts`;
  - `health-bridge status` and `get_bridge_status` report a metric's sync freshness from its current
    anchored and automatic cursor kinds.
  - Migrations `017` to `021` are upstream's `010` to `014`, renumbered because this fork's `010` to `016`
    were already in use (#265). They add four empty `daily_projection_*` tables, indexes, three nullable
    pairing columns and a backfill that relabels stored raw UV exposure rows from `sum` to
    `min_max_average`. No receiver code reads the new tables or columns yet. `FORK.md` lists every
    upstream commit held or skipped, with its reason.

## Compatibility

- Batch Protocol: `health_bridge.batch.v1` (`1.0.0`). No wire change; a compliant batch is accepted as
  before.
- HealthRelay app: not part of this release. The receiver accepts the same batches the shipping app sends.
- Mailbox helper: unchanged at `1.1.1`. Helper manifests must carry the release tag `receiver-v<version>`,
  which is why the package version does not move and this release is identified by its tag instead.
- Agent plugin: unchanged. No MCP tool is added or removed, and the tools stay read-only; the read-only
  intake-evidence tool `get_intake_evidence_v1` is not changed by this release.
- Intake-context routes: unchanged and still **off by default**; start the receiver with
  `--enable-intake-context` to serve them.

## Install or upgrade the receiver

```bash
uv tool install "git+https://github.com/mwdearing/health-relay.git@healthrelay-receiver-2026.11.24"
```

## Verify what you have

```bash
cat "$(uv tool dir)"/apple-health-ai-bridge/lib/python*/site-packages/apple_health_ai_bridge-*.dist-info/direct_url.json
```

`vcs_info.requested_revision` must be `healthrelay-receiver-2026.11.24`, and `vcs_info.commit_id` must
equal the commit the release tag points to (`git ls-remote
https://github.com/mwdearing/health-relay.git refs/tags/healthrelay-receiver-2026.11.24`).

## Privacy and operating boundaries

- HealthKit access remains read-only, and the MCP server stays read-only.
- This release adds no new data surface, no new tool and no new read path. The new tables and columns are
  empty and unused; the one data change is the UV label backfill in `021`, which rewrites metadata
  in place and moves no data off the device.
- No telemetry, advertising, data broker, or third-party AI upload path is added.
