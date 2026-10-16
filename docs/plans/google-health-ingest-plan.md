# HealthRelay: Google Health ingest and cross-source dedup (plan, v10)

Status: plan approved by Mike (2026-10-16 22:39Z). Pushed to `docs/google-health-ingest-plan` for review. No code or PR for the build until the Google spike passes (§3.2).
Open for the build, not for this plan: §4.1 handling of unrecognised origins (hold out vs keep; planner recommends hold out).
Date: 2026-10-16. Supersedes v1 to v9.

## 1. Summary

- Reported by Mike (2026-10-16), not yet verified: the iOS Google Health app syncs Apple Health data, Apple Watch included, into Google Health. Apart from the Pixel test windows, this is the only way data reaches his Google Health account, so his Google side otherwise holds Apple echoes only.
- Google-native test data: the Pixel's Google Health app tracks steps from the phone's own sensors and stores them in Google Health (screenshot, 2026-10-16). Mike carries and powers the Pixel only during scheduled windows, of two kinds (§8 step 7a).
- Two user types:
  - Apple users who also connect Google Health: echo filtering applies; source of truth defaults to Apple Health and is configurable (§4.4).
  - Google-only users (including Android users): no Apple source, so nothing is dropped as an echo; Google Health is the source.
- Hypothesis (Google spike items G1, G2, G9): the Google Health API returns Apple-synced records and phone-tracked steps, with an origin the puller can filter on.
- Apple-only fix first (§8 steps 1 to 6, not gated on Google): check whether Apple-side steps arrive as raw overlapping iPhone and Watch samples; if so, fix that double counting with sub-source reconciliation.
- Google spike (gate): feasibility against the Google Health API. It needs a Google Cloud project (§9.2). No Google build until it passes.
- Phase 1: a server-side Google Health puller on the receiver, writing through the existing `health_bridge.batch.v1` path under its own account-scoped `source_key`. The receiver is multi-account and multi-app by design (§4.5); the first deployment runs one account, Mike's.
- Phase 2: an Android client (in `android/` in this repo) reading Health Connect and posting batch.v1. It serves Google-only Android users and other non-Fitbit Android sources. Mike has a Google Pixel for testing and intends an eventual Google Play release.
- Not options: the Fitbit Web API (support ended 2026-09-30, switched off 2026-10-30) and the Google Fit APIs (deprecated, new signups closed May 2024).

## 2. What exists today (verified in the repo unless marked)

- Receiver migrations through `016_source_scoped_types.sql`. 015 = range guards (reversed intervals and negative workout values abort). 016 = `health_types` and `health_type_aliases` keyed by `(source_id, type_code)`, `samples` rebuilt with a composite FK.
- Live receiver: per the 2026-10-15 CHANGELOG it still runs schema 014. Treat live as 014 until verified (§8 step 0).
- Idempotency keys (docs/reference/sqlite-v1.md): `samples(source_id, type_code, client_record_id)`, `workouts(source_id, client_record_id)`, `sleep_sessions(source_id, client_record_id)` plus `(source_id, start_time)`, `deleted_records(source_id, record_family, client_record_id)`, `sync_cursors(source_id, cursor_kind)`.
- No cross-source key exists. The same real event from two sources produces two rows.
- Sleep uses revision-with-tombstone semantics (displaced identities are tombstoned, never resurrected).
- iOS origin metadata: generic quantity samples carry `healthkit_source_bundle_id`, `healthkit_device_name`, `healthkit_device_model`, `healthkit_device_manufacturer` in `metadata_json` (GenericQuantitySyncBatchFactory.swift:29-39; reader HealthKitGenericQuantityReader.swift:7-14). The bundle id names the WRITING APP, not the device; device fields are often empty.
- Unverified: `StepCountSyncBatchFactory.swift:383` hard-codes `deviceModel: "iPhone"`. Whether steps arrive as raw `HKQuantitySample`s or as bucketed statistics is not confirmed. Answered in §8 step 1. If the step path drops per-sample origin, the iOS client must send real origin fields, and rows already stored stay without origin (§8 step 3a).
- Receiver auth (new work, checked before build): the SQLite reference ties v2 receiver tokens to devices (`receiver_token_devices`). A write-only, `google-health:`-prefixed token scope does not exist today. It is new receiver code (§8 step 8).
- Downstream readers that will see double counts if dedup is skipped:
  - `src/health_bridge/queries/daily.py`, `timeseries.py`, `_common.py`, `catalog.py`
  - `src/health_bridge/status.py`, `src/health_bridge/mcp/tools.py`
  - `/home/michael/data/peptide-dosing-agent/scripts/health_bridge_reader.py` (resting heart rate and weight straight from `samples`, lines 65-128; feeds production cron health lines)
  - `hermes-healthrelay` plugin (reads the receiver; catalog-pinned 0.4.4)
  - health-insights (`data/health-insights`): NOT yet checked line by line
  - agent-dashboard: check for any receiver reads
  - The reader list is not complete until the last three are checked.
- Contract: `docs/reference/batch-v1.md`. `source_key` is installation-scoped; a server-side puller is not an installation, so the contract text needs an update (§8).
- Repo: health-relay already has `ios/`. The Android client goes in `android/` in the same repo (§8 step 10).

## 3. Google side

### 3.1 API choice
Google Health API (cloud, OAuth 2.0), pulled by the receiver. Health Connect is on-device Android only and cannot be read server-side (Phase 2; read as a platform limit, not yet checked against Health Connect docs).

From developers.google.com/health/about (WebFetch 2026-10-16):
- Families: calories and energy, vitals, skin temperature, workouts, nutrition and hydration, steps, sleep and recovery, women's health. More types planned (Q4 2026 to Q1 2027).
- Write access: not stated as available today.
- Google-side dedup: a "Reconciled Stream" merges overlapping points from multiple sources.
- Scopes: all Restricted; a privacy and security review is needed for production access. The API may change before "the end of May" (year not stated).
- Stated coverage: Fitbit and Pixel Watch data. Whether the API returns Apple-synced data, and whether it returns phone-tracked steps, is unverified (spike G1, G2, G9).

### 3.2 Google spike (hard gate, after the Cloud project exists, §9.2)
One OAuth grant, one day per family, written to a scratch DB (never the live DB). Answer:
- G1. Do records synced from Apple Health come back through the API?
- G2. If so, does each record carry an origin the puller can filter on, and what exact values identify Apple (source app name or package, device manufacturer, device model)? The echo filter (§4.1) is built from these exact values.
- G3. Does the Google Health app also write INTO Apple Health (two-way sync)? Checked on the iPhone, not via the API: Health app, Sharing, Apps, Google Health, write permissions. If any write permission is on, HealthRelay's iOS client would ingest Google-origin rows from HealthKit: a loop.
- G4. Stable data point IDs per family (needed for `client_record_id`).
- G5. Are updates and deletions exposed (needed for tombstones)? If not, the cursor model becomes periodic full re-pull windows.
- G6. Refresh-token lifetime. Unverified: an External app in Testing status with restricted scopes may get 7-day refresh tokens, and production restricted scopes may require a third-party security assessment. Either could rule out unattended pull.
- G7. Reconciled stream vs per-source raw stream: which carries origin?
- G8. Quotas and the maximum backfill depth.
- G9. Does the Pixel's phone-tracked step data come back through the API, and with what origin values (to confirm they are distinguishable from Apple values)? If it does not come back, the Pixel reaches the plan only through the Android client (Phase 2).

Spike outcomes:
- Origin present (G1 and G2 yes): echo filter at ingest for accounts with `apple_source_connected` (§4.1); reconciliation is the fallback.
- Apple data returned without origin: full time-and-value reconciliation (§4.2 to §7) becomes required for Google data.
- Token lifetime unworkable (G6): stop the Google work; decision for Mike. The Apple-only fix (§8 steps 1 to 6) is unaffected.

### 3.3 Phase 2: Android client (planned)
- Kotlin, in `android/` in the health-relay repo; Health Connect SDK; own `source_key` per installation; same batch and cursor contract. Per-type permissions; background reads via WorkManager.
- Android pulls from Google Health and Health Connect only. There is no Apple Health app on Android.
- Health Connect is the route to Samsung, Oura and other non-Fitbit Android data.
- Origin mapping: Health Connect `DataOrigin` package name plus device manufacturer and model map into the same origin fields as §4.3.
- Release: Google Play. Health Connect apps need a permissions declaration and a privacy policy review: Mike's outward-facing steps.
- Starts after Phase 1 has proven the reconciliation layer.

## 4. Matching events

### 4.1 Rule 0: echo filter (driven by setting, matched on Apple-specific identifiers)
- The filter runs when the account's `apple_source_connected` setting (§4.5) is true. It does NOT depend on whether Apple rows for that hour have arrived yet: Google can deliver before the iPhone syncs, and a data-presence rule would keep the echo and then duplicate it when the Apple row lands.
- What counts as Apple-origin: ONLY Apple-specific identifiers, taken as exact values from the spike (G2): the source app is Apple Health (or the Google Health app's Apple Health import, as Google labels it), or the device manufacturer is Apple. A generic origin such as "phone" or "mobile" without an Apple manufacturer is NEVER treated as Apple: it may be the Pixel. The allow-list of Apple values is a versioned file, covered by fixtures.
- With `apple_source_connected` true: a Google-side record that matches the Apple allow-list is dropped by the puller and counted in a per-run "echoes skipped" metric (counts only). Records that do not match are kept.
- Records with an origin the filter does not recognise are counted in a per-run "unrecognised origin" metric, so new Google labels are noticed. Handling of those records is an open build decision: hold them out of canonical totals until mapped (planner recommendation) or keep them.
- With `apple_source_connected` false (Google-only account): nothing is dropped as an echo; Google data is the record.
- Changing the setting triggers a full re-pull window for the Google source so the stored rows match the new rule.
- On iOS, the Google Health app's bundle id is recorded as a bridge source; HealthKit rows written by it are treated as Google-origin and excluded from the Apple source if they would loop (G3).
- A known bridge app (any bundle id that mirrors a vendor, e.g. a Fitbit-to-HealthKit app) marks the record as vendor-origin.

### 4.2 Fallback matching (when origin is missing)
Two records from different sub-sources (§4.3) are candidates if they share the canonical type, overlap in time and have compatible values.

- Workouts: overlap over the shorter interval (intersection / min(duration)) >= 0.8, same workout type family. Energy and distance only as tie-breakers, 10 percent tolerance.
- Sleep sessions: intersection over union >= 0.7. Stages are not matched; the winner's stages are used.
- Point readings (weight and similar): time window configurable per type (default 2 minutes; some apps stamp sync time, not measurement time) plus a value tolerance per type (weight 0.1 kg after unit conversion). Out-of-tolerance readings are never merged. Near-misses are logged so tolerances can be tuned.
- Resting heart rate and other vendor-computed daily values: never merged by value. One sub-source per day by priority (§5).
- Heart rate series and other high-frequency series: one sub-source per time window (default 1 hour) by priority, not per-sample matching.
- Clustering for N sub-sources: matching is not transitive. Anchor on the highest-priority record, assign other records greedily by match score, each record joins at most one cluster. Deterministic given the same inputs and `rule_version`.

These tolerances decide whether two records are the SAME event. They are not alert thresholds (§4.4).

### 4.3 Reconciliation unit: the sub-source
- A sub-source is `source_key` plus origin: for Apple, `healthkit_source_bundle_id` plus device model (e.g. Apple Watch vs iPhone); for Google, the origin the API reports (e.g. Fitbit, Pixel Watch, Pixel phone); for Android, `DataOrigin` package plus device.
- Matching (§4.2) and winner selection (§5) compare sub-sources, so iPhone and Apple Watch rows inside the single Apple source are reconciled the same way as cross-source rows.
- Records with no origin form one "unknown" sub-source per `source_key`. Overlap inside the "unknown" sub-source is NOT reconciled (there is nothing to tell the devices apart); see §8 step 3a for historical rows.
- Default Apple order (chosen default, not a claim about HealthKit): Apple Watch before iPhone, then other apps. Mike's decision (§9.6). The production default stays this unless Mike changes it. The Apple-only acceptance check uses a separate test-only priority (§8 acceptance) and never changes this default.

Type mapping: `source_type_map` (source, source_type, canonical type_code, unit conversion) as a versioned file in the repo, covered by synthetic fixtures.

### 4.4 Source of truth and discrepancy alerts
- Platform defaults (Mike, 2026-10-16):
  - iOS users: Apple Health is the source of truth by default; the user can configure Google Health instead.
  - Android users: Google Health only. There is no Apple Health app on Android.
  - Google-only accounts have only Google, so no choice is needed.
- Stored in `source_settings` (§4.5).
- What source of truth decides: which INDEPENDENT device wins when two different devices recorded the same real activity (for example an Apple Watch and a Pixel carried together). It does not decide echoes; echoes are resolved by origin (§5 step 1).
- Discrepancy alerts (accounts with both sources and alerts opted in):
  - Shared hours only: for cumulative types (steps, active energy), the comparison uses only local hours in which BOTH sub-sources have coverage of at least 30 minutes (`coverage_minutes`, §7). Those shared hours are summed per day on each side, and the two sums are compared. Hours covered by only one side never count toward a discrepancy: a second device worn for part of the day is not a mismatch.
  - Vendor-computed daily metrics (resting heart rate and similar): compared per day only when both sides report a value for that day.
  - Days with fewer than 2 shared hours are not compared for cumulative types (too little overlap to judge), and are counted in a "not comparable" metric.
  - Alert thresholds are their own settings, separate from the §4.2 dedup tolerances. Mike's decision (§9.5): daily buckets (over shared hours); 20 percent for steps and energy; 1 kg for weight. The 20 percent figure is a starting suggestion, not a measured value; the real disagreement between wearables is unverified and is checked on the overlap windows (§8 step 7a).
  - "Missing counterpart" alerts apply only to point readings (for example a weight reading in Google with no Apple reading that day). Not to cumulative types: Google-native hours with no Apple data are legitimate (acceptance counts them as a valid increase).
  - Alerts are capped and rolled up: at most one alert per type per day, a daily digest for the rest, following the dashboard's capped-alert rule.
  - Output: dashboard for every alert. Email only for a large mismatch, defined as at least twice the alert threshold (40 percent for steps and energy, 2 kg for weight by default; configurable). Alert bodies name the type and time window only, never values. Mike's own inbox and dashboard follow the existing rule-4 clarifications; alerts never name medications, doses or values.
- Discrepancy alerts are not generated for Google-only accounts, which have no second source.
- Testing on real data uses the overlap windows (§8 step 7a).

### 4.5 Account, app and per-account settings
- The receiver is multi-account and multi-app by design (Mike, 2026-10-16).
- Where the keys live: `account_key` and `app_key` are columns on the sources table (one row per `source_id`; for example `app_key` `apple-healthkit` for the iOS app, `google-health-api` for the puller, `health-connect` for Android). Raw tables (`samples`, `workouts`, `sleep_sessions`, `deleted_records`, `sync_cursors`) are NOT changed: every raw row already has a `source_id`, and its account and app are derived through that. No rebuild of `samples` after 016.
- Canonical tables (§6) carry `account_key` directly, because reconciliation and reads are always scoped to one account.
- Existing sources are backfilled with Mike's `account_key` and the matching `app_key` in migration 017.
- `source_settings` table: `account_key`, `apple_source_connected` (bool), `source_of_truth` per canonical type, `discrepancy_alerts_enabled`, alert threshold and email multiplier per type, `updated_at`.
- Set during setup; changes are logged and trigger recompute of affected canonical rows (and a Google re-pull window when `apple_source_connected` changes).
- Reconciliation never matches records across different `account_key` values.
- The first deployment runs one account (Mike) on the personal receiver. The shape supports more accounts now; serving other people needs §9.4a.

## 5. Which sub-source wins

Per canonical type, within one account:
1. Echo resolution by origin: when two records are the same data (one is a mirror of the other), the originating record wins. Apple-origin data goes with the Apple record; Fitbit, Pixel Watch or Pixel phone-origin data goes with the Google record. This step only applies to echoes.
2. Independent devices: when two different devices recorded the same activity, the user's source of truth (§4.4) wins, then configured priority per type and sub-source (defaults in §4.3).
3. Tie: lowest `source_key`, then lowest origin string (stable across rebuilds and restores; never the local `source_id`).

Losers stay in the raw tables as members; nothing is deleted.

## 6. Provenance and canonical schema

- Raw rows stay in `samples`, `workouts`, `sleep_sessions` under their own `source_id`, unchanged. Account and app come from the sources table (§4.5).
- `canonical_events`: `canonical_event_id`, `account_key`, `canonical_type`, `record_family`, `start_time`, `end_time`, `winner_source_key`, `winner_origin`, `winner_type_code`, `winner_client_record_id`, `rule_version`, `computed_at`. No copied values: values are read from the winner row by join, so a revised winner never leaves a stale copy.
- `canonical_event_members`: `canonical_event_id`, `source_key`, `origin`, `record_family`, `type_code` (NULL for workouts and sleep), `client_record_id`, `role` (winner or member), `match_score`, `match_rule`. With `type_code` the member resolves to exactly one `samples` row.
- `canonical_daily_aggregates` (§7): `account_key`, `local_date`, `time_zone`, `canonical_type`, `hour` (NULL for day rows), `winner_source_key`, `winner_origin`, `coverage_minutes`, `rule_version`, `computed_at`. Per-sub-source hourly coverage is kept alongside (one row per hour and sub-source) so §4.4 can find shared hours.
- `reconcile_cutovers` (§8 step 3a Option B): `account_key`, `source_key`, `canonical_type`, `cutover_at`, `reason`. Canonical rows before `cutover_at` are flagged `uncorrected` so readers and reports can show that history may double count.
- `source_settings` (§4.5).
- `discrepancy_alerts`: `alert_id`, `account_key`, `canonical_type`, `bucket` (day), `bucket_start`, `shared_hours`, `kind` (aggregate_mismatch or missing_point_reading), `severity` (dashboard or email), `apple_ref`, `google_ref`, `raised_at`, `acknowledged_at`, `rolled_up_into`. Holds references and kinds, not values, in any non-local output.
- Recompute: incremental via a dirty window (the changed record's time range widened by the largest tolerance for its type). A `rule_version` change triggers a full rebuild; a full rebuild is idempotent and deterministic.
- Tombstone on a winner: the cluster is recomputed, the next member wins.

## 7. Aggregates (steps and similar)

- Cumulative types (steps, active energy, distance, flights) are never summed across sub-sources and never take the maximum across sub-sources: two devices can count the same walk, so max overcounts.
- Reconcile per local hour, then sum hours for the day. Per hour:
  1. Echoes are already resolved (dropped at ingest by §4.1, or by origin per §5 step 1).
  2. Independent devices with data in the same hour: the user's source of truth, then configured priority.
  3. Then the sub-source with fuller coverage for that hour, then the §5 tie rule.
- An Apple user who picks Google as source of truth gets Google for hours where both an Apple device and a Google-native device recorded; hours with only Apple data stay Apple; hours with only Google-native data stay Google.
- Coverage: the number of distinct minutes in that hour that have data from the sub-source (after splitting, below). Stored per hour and sub-source.
- Hour-spanning samples: a sample that crosses an hour boundary is split pro rata by time into each hour it covers before coverage and totals are computed. The canonical hourly views (§8 step 5) use the same split.
- Bucket by local civil time; store the zone per day; travel days keep each hour's own zone.
- If a sub-source only reports day totals, it can win the whole day only when no sub-source has hourly data for that day. Never mix a day total from one sub-source with hourly data from another for the same day.
- Recompute the affected day when any member changes.

## 8. Ingest and rollout (in order)

Deploy gate (applies to every step that changes the live DB or restarts the receiver: steps 0, 3, 4, and the receiver side of 8): back up first, run on a backup copy and check counts, then restart idle-gated from the green committed main checkout (never a worktree). Each gets its own Change Log entry with backup path and rollback.

Flag: `google_in_canonical` (off by default) controls ONE thing: whether Google sub-sources are included in canonical views and reader output. Raw Google ingest, echo filtering and discrepancy-alert computation run with it off. Turning it on is step 10.

0. Prerequisite: deploy 015/016 to the live receiver under the deploy gate; confirm the range guards reject no existing rows (counts only).
1. Apple step check (not gated on Google): are steps raw overlapping iPhone and Watch samples or bucketed statistics, and does each step row carry real origin fields? Read-only, counts only.
2. Apple-only reconciliation design and fixtures (proceeds on step 1's answer alone; synthetic fixtures written first, failing first).
3. Migration `017_canonical_events` under the deploy gate: canonical tables, `source_settings`, `account_key` and `app_key` columns on the sources table only (existing sources backfilled to Mike's account). Raw tables unchanged; no `samples` rebuild. Then sub-source reconciliation (§4.3). If step 1 shows raw overlapping samples, this fixes double counting for rows that carry origin.
   - If step rows lack origin: the iOS step path is fixed first to send real origin fields. That is an iOS app change: it needs a signed build installed on Mike's phone before step 3 can reconcile new rows. Step 3 waits on that build (HealthRelay 1.2.2 is in App Review; the origin fix rides a later build). Signed-build runs follow the standing relay approval; App Store Connect actions stay Mike's.
   - 3a. Historical rows without origin: Option B, dated cutover (chosen by Mike, 2026-10-16, least conflict). History before the recorded cutover date stays uncorrected; `reconcile_cutovers` marks it and readers flag it. No re-sync, so no tombstone churn. Option A (re-sync) is not used.
4. Backfill under the deploy gate: reconcile existing data after 017; keep the pre-reconcile DB backup.
5. Canonical views: `samples_canonical` and equivalents, including the hourly split (§7). Readers switch by table name.
6. Reader migration in its own PR: `daily.py`, `timeseries.py`, `status.py`, `mcp/tools.py`, `hermes-healthrelay`, health-insights, any dashboard reads. The peptide-dosing reader feeds production cron health lines: it moves in its own change after the canonical views exist, with a local test run first, and only after Mike's explicit go-ahead at that point (Mike, 2026-10-16: "if it makes sense, then do it"; host rules still require the cron change to be tested before it goes live).
7. Google spike (§3.2): only after the Cloud project exists (§9.2). Report to Mike; stop the Google work if it fails.
   - 7a. Pixel test windows (Mike, 2026-10-16). The Pixel tracks steps from its own sensors in the Google Health app. Mike powers and carries it only during windows he schedules in advance (§9.14). Two kinds:
     - Overlap window: Pixel plus iPhone and Apple Watch carried together for at least 2 full local hours (for example 10:00 to 12:00), so the §4.4 shared-hours rule (at least 30 minutes of coverage in each of at least 2 hours) is met. A short walk alone does not qualify for the discrepancy check; it still tests source of truth. Tests source of truth (Apple wins by default, Google wins when configured) and discrepancy alerts on shared hours. Also gives the first measured disagreement between the devices, to check the 20 percent threshold.
     - Pixel-only window: iPhone and Apple Watch left behind, Pixel carried alone. Tests the Google-only contribution: Pixel steps appear in canonical totals and explain the whole increase. Apple records nothing for that walk, by design.
     - Each window is checked against the Google-native rows that arrive for it. Hours outside windows are expected to be Apple-only or empty and are counted as such, not as failures. Days without a window use synthetic fixtures. Results are labelled by window kind. Heart rate, sleep and weight stay on fixtures.
8. Google puller (Python, on the receiver host): posts batch.v1 through the HTTP ingest path (same validation as the iOS client), never writes to the DB directly. `source_key` = `google-health:<account hash>`; registered with `app_key` `google-health-api` on the sources table. No Google token in any batch. One `sync_cursors` row per family, updated only after a successful write. Echo filter (§4.1) driven by `apple_source_connected` and the Apple allow-list. Runs with `google_in_canonical` off.
   - Receiver token scope (new receiver work, auth code reviewed before build): write-only to the batch ingest endpoint, accepted only for `source_key` values starting with `google-health:`, no read or query endpoints, stored under `~/.config/` (0600), never printed, revocable on its own without touching iOS installation tokens.
   - Security review before merge: the planner runs an explicit security review of the puller PR (OAuth token storage and refresh, receiver token scope, outbound calls, logging of secrets) in addition to the standard review (§10). Until a dedicated reviewer project exists (§9.9), this is the only security review, and the gap is recorded in §11.
9. Discrepancy alerts (§4.4): built after step 8, tested on synthetic fixtures, then on overlap windows (7a). Runs with `google_in_canonical` off; enabled only for accounts with both sources and alerts opted in. Blocked on §9.14 for the real-data part.
10. Turn `google_in_canonical` on. Blocked on §9.14 for the real-data acceptance below. Android client work (`android/`, Phase 2) starts after this.

Synthetic fixtures (written first, failing first): per family, exact match, partial overlap, value out of tolerance (must not merge), Apple-origin echo dropped with `apple_source_connected` true, same record kept with it false, Pixel phone-origin steps kept with `apple_source_connected` true (generic "phone" origin is not Apple), unrecognised origin counted and handled per the open §4.1 decision, Google record arriving before its Apple counterpart (still dropped as an echo, no later duplicate), two-way sync loop rejected, origin override, iPhone vs Apple Watch overlap inside one source (Watch wins by default), overlap inside the "unknown" sub-source left unreconciled and flagged, non-transitive three-sub-source cluster, tombstone on the winner (the loser comes back), hourly step handover between devices, hour-spanning sample split pro rata, travel day, rows before a cutover flagged `uncorrected`, Apple user with Google as source of truth (Google wins for independent-device overlap only; Apple-only hours stay Apple), Android account with Google only (no echo filter), shared-hours mismatch above the alert threshold raises one dashboard alert, at twice the threshold also raises email, mismatch within threshold raises none, part-day second device (30 minutes of Pixel against a full Apple day) raises NO alert, day with fewer than 2 shared hours not compared, missing point reading raises an alert, Pixel-only hour with no Apple data raises NO alert, alert cap and roll-up, no alert for a Google-only account, multi-account isolation (rows, reconciliation and alerts for one account never affect another), sources table backfill (existing sources get Mike's account and app keys; raw row counts unchanged), Google-only hour with no Apple data (kept, total rises by exactly that amount).

Real-data acceptance: compared locally and shown to Mike in chat. GitHub, Brain and the Change Log get only counts or pass/fail.
- Apple-only fix (after step 4), for N recent days:
  - Read the phone's Data Sources order for each compared type (Health app, the type, Data Sources & Access).
  - Run the comparison with a test-only priority set to that order (not stored as the production default). Pass: canonical daily totals for steps, active energy and sleep duration match the Apple Health app within an agreed tolerance. Days before a cutover (Option B) are excluded and counted separately.
  - Report separately how the production default (§4.3) differs from the phone's order and what that does to the totals (counts only outside chat).
- After `google_in_canonical` is on (step 10):
  - All days: zero Apple-origin rows from the Google source for accounts with `apple_source_connected`; the Apple-origin share of each daily total is unchanged from before the flag.
  - Days without a window: daily totals unchanged (Mike's account is echo-only outside windows).
  - Pixel-only windows: the daily total rises, and the whole increase is explained by the Pixel rows in that window: (new total) minus (pre-flag total) minus (Pixel contribution in non-overlapping hours) = 0.
  - Overlap windows: with Apple as source of truth, totals unchanged; with Google as source of truth (test setting), the window's hours switch to the Pixel's count and nothing else changes. Discrepancy alerts behave per §4.4 on the shared hours.

Operations: refresh-token handling and storage under `~/.config/`; an alert (not a silent stop) when consent lapses or the API errors persistently; quota-aware backoff; documented backfill depth; "echoes skipped", "unrecognised origin" and "not comparable" metrics visible on the dashboard (counts only).

Contract: update `docs/reference/batch-v1.md` to allow account-scoped server-side sources, with the auth boundary in step 8, and to document `account_key` and `app_key` on sources.

## 9. Decisions

Resolved by Mike (2026-10-16 19:16Z, 19:18Z and 22:39Z):

| # | Decision | Answer |
|---|---|---|
| 9.1 | Push the plan to GitHub | Approved. Pushed on `docs/google-health-ingest-plan`. No health data in the file. |
| 9.2 | Google Cloud project | Needed: the Google Health API issues OAuth client credentials per Cloud project and verifies restricted scopes under it. No cost expected in Testing status (unverified); production verification may need a paid third-party assessment (§9.4a). Creating the project is outward-facing (AGENT.md §6): Mike sets it up when asked. |
| 9.3 | Outbound dependency, host timer or service | Yes, as scoped in §8 steps 7 and 8. |
| 9.4 | Other users | The receiver ingests from multiple accounts and multiple apps under one account (§4.5). First deployment is one account. Serving others needs §9.4a. |
| 9.5 | Discrepancy alerts | Daily buckets over shared hours; 20 percent for steps and energy; 1 kg for weight; dashboard for every alert, email at twice the threshold. |
| 9.6 | Platform defaults | iOS: Apple Health is the source of truth by default, configurable. Android: Google Health only. |
| 9.7 | Peptide reader | Move it when the canonical views exist, as its own change with a local test first (§8 step 6). |
| 9.8 | Repo layout | Same repo: `ios/` and `android/` side by side. |
| 9.9 | Dedicated reviewer project | Mike creates it when the scope and spec are ready. Scope brief to be written when asked. Does not gate the spike. |
| 9.10 | Historical rows | Option B, dated cutover (least conflict). |
| 9.11 | Reader default | Canonical views only; raw for debugging. |
| 9.12 | Test source | Pixel steps during scheduled overlap and Pixel-only windows; synthetic fixtures for the rest. |
| 9.13 | Supabase | Local Supabase in Docker (on this host) is a viable option; the Kynk-hosted instance must never hold health data. Not built. A short spike comparing SQLite and local Postgres-with-Supabase on the same fixtures is tracked for after the reconciliation layer works. |

Still open:
- 9.4a. Serving other people: needs full restricted-scope verification for production (not Testing status, likely a paid third-party security assessment), and a privacy decision on alert bodies and stored health data of other people. Not started.
- 9.14. Pixel-window schedule: at least one overlap window and one Pixel-only window, dates and times picked by Mike in advance. BLOCKS the real-data part of §8 steps 9 and 10.
- 9.15. Retire `pref-pr-review-gate` (o2b preference describing a Codex-only review gate on Kynk, now moot since Codex reviews are retired). Proposed; needs Mike's OK.
- §4.1 open build decision: unrecognised origins held out of canonical totals (planner recommendation) or kept.

## 10. Constraints

- Fedora only; the receiver runs on this host.
- No health values, medication names or doses in any GitHub text, commit, PR, issue or public file. Fixtures are synthetic. Real-data checks report counts only outside chat.
- Credentials under `~/.config/`, never in the repo, never printed.
- No AI or tool attribution in GitHub text (commits, PRs, branch names).
- Branch names `docs/...` or `feature/...`, never `claude/...`.
- Medication events are out of scope; dose events are not part of Google Health ingest.
- Review (matches o2b `pref-review-policy`): every PR gets a Haiku low-effort subagent review before merge. The planner and its advisor review each PR before merge. PRs that touch credentials, tokens or outbound calls also get the planner's explicit security review (§8 step 8) until a dedicated reviewer project exists. Read both comment surfaces on the current head before any merge (AGENT.md section 5a).
- Rule propagation: done 2026-10-16. The o2b preference `pref-worker-delegation` is tombstoned (verified 2026-10-16) and superseded by `pref-review-policy`. `pref-pr-review-gate` is still confirmed and proposed for retirement (§9.15).

## 11. Open risks

- Mike's Google account holds Apple echoes only outside Pixel windows; Google-native features are proven on the Pixel windows only, and only if the Pixel's phone steps come back through the API (G9).
- Google may return Apple-synced data without an origin, which makes full reconciliation mandatory for Google data.
- Google may label Apple and Pixel origins in ways the allow-list does not cover; the "unrecognised origin" metric catches it, and §4.1 decides what happens to those records.
- Two-way sync between the Google Health app and Apple Health can create a loop unless both guards are in place.
- 7-day refresh tokens or a mandatory security assessment may make unattended pull impractical (unverified).
- Serving other people (§9.4a) is not covered by the personal receiver as built.
- Apple-side steps may already double count (raw iPhone plus Watch samples), and the step path may not carry per-sample origin.
- Historical rows without origin stay uncorrected behind the cutover (Option B).
- The Apple-only fix for steps may wait on an iOS signed build reaching the phone, which is outside this host's control (App Review, TestFlight processing).
- Discrepancy alert thresholds that are too tight produce alert noise; wearable disagreement is unverified until the overlap windows measure it.
- The shared-hours rule (30 minutes per hour, at least 2 shared hours per day) is a starting value; short overlap windows may fall under it and need a longer walk.
- Reconciled vs raw Google streams may differ in origin visibility.
- Sleep stages and workouts differ in granularity across sources; the winner's detail is used without merging.
- Pro rata splitting of hour-spanning samples is an approximation; hourly totals near boundaries can differ slightly from the vendor app.
- Health Connect data shapes differ from HealthKit (no HKSource bundle ids); the origin mapping must exist before Phase 2.
- The Google Health API may change before "the end of May" (year not stated).
- Security review of the token-holding puller is done by the team that wrote it until the dedicated reviewer project exists. Accepted gap, pending §9.9.
- Pixel test coverage is limited to scheduled windows and steps only.
