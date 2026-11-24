# HealthRelay: fork notes

HealthRelay is a fork of [Apple Health AI Bridge](https://github.com/roian6/apple-health-ai-bridge)
(Apache-2.0). Forked 2026-09-27 at upstream `4818cdc` (iOS companion 1.1.1 build 50,
receiver 1.1.1, batch schema `health_bridge.batch.v1` 1.0.0). Upstream is the `upstream`
git remote (fetch only; its push URL is set to `DISABLED` so nothing can be pushed by accident —
re-establish it with the commands in §1 of that doc, since remotes are local configuration and do
not travel with a clone);
`origin` is the public repo `mwdearing/health-relay`.

**To sync from upstream, follow [`docs/upstream-sync.md`](docs/upstream-sync.md).** The
`Upstream sync, <date>:` entries below are the record of what has been taken and, more importantly,
what has been deliberately skipped and why.

## Why a fork
The upstream companion syncs 67 HealthKit read types in the background to a receiver
you control. HealthRelay keeps that and adds what the upstream does not read:
electrocardiograms, the full dietary/nutrient set, richer workouts, medication dose
events, plus a one-button "export now" flow and a Shortcut, and an Apple Health
`export.zip` importer for lab results. Pieces that are generic (ECG, dietary, workouts)
are intended to be offered upstream as pull requests; the medication and
receiver-specific parts stay here.

## Rules for changes
- Contract first: extend the schemas in `schemas/` before the receiver, then the app.
- Keep `health_bridge` / `health-bridge` as Python package and CLI identifiers (the
  upstream brand note allows these as code identifiers); product name, bundle id,
  display strings and icon are HealthRelay's own.
- Keep `LICENSE` and `NOTICE`; list every modification below.
- Bundle id: keep `com.example.*` in tracked files (public-release audit rule); pass the real reverse-DNS id as `BUNDLE_ID` at build time. The value is not written in this repo.
- Version: the marketing version is bumped by hand, and only there: `MARKETING_VERSION` in the Xcode project (currently 1.2.2) and `component-versions.json` `ios_companion.marketing_version` must match, and a guardrail checks that. `Signed beta upload` and `Build unsigned IPA` both stamp that recorded version unless a `marketing_version` input overrides it. The build number is never edited: `Signed beta upload` uses the run number + 100, `Build unsigned IPA` the run number, and the tracked `CURRENT_PROJECT_VERSION` (50) is only the floor the fresh-device evidence gate compares against.

## Modifications (newest first)

- Upstream sync, 2026-11-24: upstream checked through `d6f2534` (upstream `main` after `receiver-v1.2.0`; the 2026-11-23 entry below covers everything through the tag). **Nothing new taken**: the 21 commits after the tag and the held commits from the previous entry were reviewed against `docs/upstream-sync.md` §3a, and each is held or skipped for a stated reason. The package version stays `1.1.1`.
  - **Held until an iOS pass can verify the app half (coupled receiver and iOS changes):** `0629d8b` with `d6f2534` (daily projection state: a 666-line storage engine on the ingest path, lock-file changes, and an iOS repair path; its schema is carried as `017` and `018` in the held migrations branch), and `8eefdd2` (raw UV as minimum, average and maximum; a guardrail ties the Swift and receiver type catalogs together, so neither half can land alone; the receiver half exists as a patch).
  - **Held for an owner decision (contract or tool-surface change):** `77169f8` (adds extents and two long notes to `list_synced_metrics`, which agent clients call first and on every run), `c8dac1e` (new sleep-report tools), `21bc66b` (capability gate that overlaps this fork's intake-context capabilities), `309745c` (committed-snapshot reads: changes how queries take database locks; status and MCP already work against a running receiver).
  - **Skipped, macOS Mailbox Beta and native pairing (out of support scope, needs signed helper releases this fork does not publish, and changes pairing):** `aeae5c8`, `cc7db49`, `52afa65`, `0b003d6`, `29a8a2d`, `1acd63b`, `13be0ce`, `19b514e`, `1911884`. The migration `013_mailbox_enrollment` that belongs to this group is carried as `020` in the held migrations branch and is unused unless native pairing is ever ported.
  - **Skipped, other:** `5d03ff5` (performance change that removes a canonical re-encode check), `5009e1c` (this fork's catalogue already carries the five dietary codes), release and version bookkeeping (`401ac49`, `c6ed5eb`) and the documentation-only commit `9a82687`.
  - **iOS commits not taken (macOS CI is the gate; a separate pass):** `9446af7`, `4c5b784`, `9b698df`, `27ea4f1`, `88686a0`, `a352177`, `e549664`, `417164b`, `0ff88ba`, `a1d04b7`, `40030e9`, plus the 16 listed in the 2026-11-23 entry.
- Upstream sync, 2026-11-23: upstream checked through `8eefdd2` (receiver `receiver-v1.2.0`, an annotated, SSH-signed tag on upstream's release line; the release carries GitHub release attestation, which `gh release verify` accepts). **Took two receiver fixes; the package version stays `1.1.1`** (`docs/upstream-sync.md` §4), so this is a partial sync, not an upgrade to 1.2.0. Only the commits `d97e15f..8eefdd2` were classified; the 21 main-only commits after the tag are not reviewed and the next sync lists them with `git log 8eefdd2..upstream/main`.
  - **Took `be7dc91`** (daily aggregate counts toward `daily_activity_totals` only when it is complete and its start and end are the exact IANA calendar-day boundaries). Read path only: no new data read or written, no schema or protocol change. Conflict in `queries/daily.py` (import order only, kept both imports). Its test file is taken with the Havana fixture fix from upstream `5402285`, and one case is dropped because an `end_time` before `start_time` cannot be stored here (migration `015_range_guards` refuses it at write time).
  - **Took `e6ce6b3`** (status reports a metric's current anchored and automatic cursor kinds). Read path only. The test edit to `tests/ingest/test_daily_projection_corrections.py` is not taken (that file belongs to held `0629d8b`), and the two request helpers the new test needs are inlined instead of importing held `309745c`'s support module.
  - **Held for review, not taken, because upstream's schema migrations `010` to `014` collide by number with this fork's `010` to `016` and renumbering is a schema decision for the owner:** `0629d8b` (daily projection state, with its `011` fence order), `77169f8` (`012` catalogue coverage index), `aeae5c8` (`013` mailbox enrollment, URL-free native pairing and invitation rewrite), `8eefdd2` (`014` discrete UV raw aggregation).
  - **Held for the section 3a review (behaviour, pairing or contract change):** `309745c` (queries read committed snapshots during receiver writes), `c8dac1e` (timezone-aware sleep reports and new MCP tools), `cc7db49` (capability discovery over iCloud), `52afa65` (mailbox bootstrap retries), `21bc66b` (nutrition delivery gated on a receiver capability, overlaps this fork's intake-context capabilities), `5009e1c` (dietary types; this fork's catalogue already carries all five codes).
  - **Skipped with reason:** `05a3222` (MCP metadata; this fork already rewrote its MCP tool descriptions and nine description hunks conflict; the read-only annotations alone could be taken later); `19b514e` (macOS helper archive checks, this fork ships no signed helper); `1911884` (the 1.1.4 release-line copy of the `19b514e` helper fix, skipped for the same reason); release and version bookkeeping `a4fd59e`, `e95ca4c`, `92444b0`, `115799b` and the `1.1.3` to `1.2.0` release-source, install-pin and provenance commits (`docs/upstream-sync.md` §4); upstream README, brand, labelling, CodeQL, Scorecard and test-pruning commits.
  - **iOS commits not taken (cannot be verified on the Linux review host; macOS CI is the gate):** `125abc6`, `c23790c`, `8d73970`, `03244d2`, `e4c7530`, `11e05c5`, `eff3fac`, `1a2a98d`, `8a3fe79`, `4b82dac`, `1762cc4`, `ec9585a`, `f968f59`, `f6e1098`, `c2a4a93`, `8a0b273`. An iOS pass is a separate task.
  - Dependencies: nothing to port. `uv.lock` here already pins `msgpack` 1.2.4 (upstream 1.2.3) and `filelock` 4.2.9 (upstream 4.2.1).
- 2026-11-05: iOS version metadata has one source. `Build unsigned IPA` now stamps the marketing version
  recorded in `component-versions.json` (the same default as `Signed beta upload`) instead of
  `1.2.<run number>`, so a build from either workflow can satisfy the fresh-device evidence gate. The docs no
  longer say which release GitHub marks Latest: that mark is not an install pointer (#222).
- 2026-10-14: retired the `Publish IPA release` and `Promote IPA release` workflows (#127), so
  no workflow here attaches an unsigned IPA to a GitHub Release or promotes one to Latest, and
  a guardrail limits the workflow token's write scopes so only the receiver release pipeline
  can write releases. `Build unsigned IPA` stays as the self-build artifact path.
  Existing releases and their attachments are unchanged.
- 2026-10-13: CI trigger record corrected. The 2026-09-27 entry below says `ios.yml` and `python.yml`
  run only on `ios-v*` tags or manual dispatch; that is no longer true. Since 2026-09-28 both workflows
  run on pull requests targeting `main`, on pushes of `ios-v*` tags, and on manual `workflow_dispatch`.
  Neither workflow has a push trigger for branches, including `main`. The old entry is kept as history.
  Same change: `pyproject.toml` `[project.urls]` now point at this repository instead of upstream's,
  user-facing guides call the app HealthRelay instead of the upstream product names, and
  `tests/guardrails/test_guides_use_product_name.py` fails if those names reappear in docs or
  README.md outside its attribution allowlist.
- 2026-10-12 (`0b0aee6`): pinned receiver release `healthrelay-receiver-2026.10.12`, notes in `.github/release/notes-healthrelay-receiver-2026.10.12.md`. It carries the source-binding and export-claim refusals (#103, #110, prepared as the never-tagged `2026.10.06`, whose notes file this one replaces) and the MCP `ping` response from upstream `d97e15f` (#142). Package version unchanged.
- 2026-10-04 (`0b42c4f`): pinned receiver releases. A receiver release in this fork is the tag
  `healthrelay-receiver-<YYYY.MM.DD>` on an immutable merge commit, created through `gh release
  create` with `--latest=false`; the first one is `healthrelay-receiver-2026.10.04`, with notes in
  `.github/release/notes-healthrelay-receiver-2026.10.04.md` (`.github/release/README.md:5`). The
  tag names the commit, not a version: `version` in `pyproject.toml` is unchanged, because helper
  manifests must carry `receiver-v<version>` and are validated against the receiver version
  (`docs/versioning.md:34`). Tag distinction: `receiver-v*` and `ios-v*` are upstream tags and are
  never pushed from this fork — they trigger upstream release workflows HealthRelay does not use
  (`docs/versioning.md:48`, `.github/release/README.md:7`) — while the app's own releases use
  `app-v<marketing-version>`; the fork's pinned receiver releases therefore use the
  `healthrelay-receiver-<YYYY.MM.DD>` form, which matches no workflow tag trigger, enforced by the
  new guardrail `tests/guardrails/test_receiver_release_scheme.py` (`:184` asserts the
  `healthrelay-receiver-` filter matches, `:187` that `receiver-v*` does not). README and
  `docs/versioning.md` corrected accordingly. Follow-up: #81.
- 2026-10-04 (`4677570`): observer wake-up acknowledgement by deadline, and background-delivery
  re-arm. HealthKit stops background updates for an app that misses the observer completion handler
  three times, so every observer update now arms a 15-second acknowledgement deadline first thing in
  the callback, off the main actor; an already expired budget acknowledges at once. When the app
  becomes active with automatic sync on, background delivery is disabled and re-enabled per observed
  type, at most once per 10 minutes across relaunches, atomically with `stop()`. Diagnostics record
  deadline acknowledgements. Part of #64, closes #76. No build or release.
- 2026-10-04 (`86b2f0e`): guided receiver intake-setup command. `health-bridge receiver
  intake-setup` registers the intake producer and writes a new intake token into `--output-secret`
  (exclusive `0600` create, never printed); `--rotate` revokes the old token when it can be
  identified and fails loudly with the still-active prefix otherwise, serialized by a lock file;
  `--url` is validated before any database change, and the printed next steps quote every path and
  respect `--start-option`. Docs: `docs/pairing.md`, `docs/architecture.md`. Closes #74.
- 2026-10-03 (`260d74e`): rate limiting on the intake capabilities endpoint. `GET
  /v1/intake-context/capabilities` gets its own per-token sliding window — 30 requests per minute,
  `429` with `Retry-After` — separate from the batch budget, so polling never blocks uploads. A
  `200` carries `Cache-Control: private, max-age=300` and `Vary: Authorization`, so a client may
  reuse it only for the same credential and shared caches never store it. `IntakeRateLimiter` treats
  an empty window as idle instead of indexing into it. Closes #65, #66.
- Upstream sync, 2026-10-12: upstream checked through `d97e15f` (receiver `receiver-v1.1.2` is `27ef4a4`, already reviewed 2026-10-01). **Took `d97e15f`** (MCP `ping` returns an empty result and keeps the request ID; source-only protocol fix, no data read or written, tests first). Skipped: the `1.1.2` version bump and release docs (versions stay at `1.1.1`, `docs/upstream-sync.md` §4). **Took `cb8c6ad`** (iOS: deliver completed-day Steps in automatic sync) after the §3a review: it reads no new HealthKit type (Steps and daily statistics are already read), writes nothing to HealthKit, adds no endpoint, and does not move the batch protocol or schema; it only collects raw Steps and completed-day statistics independently in the existing automatic dispatcher and keeps pending work across partial failures. One conflict, in `HealthBridgeCompanionViewModel.swift`: kept this fork's `setStatus(.syncFailed(...))` and lane-type catalog, and took upstream's `.failed` outcome and `automaticSyncEligibleTypeCodesForTesting` seam (production still reads `supportedAutomaticLaneTypeCodes`).
- Upstream sync, 2026-10-05: upstream checked through `40aa1c9`. **Nothing taken — both commits are deliberate skips, and neither touches `src/` or `ios/`.**
  - `40aa1c9` (README led with upstream's own product value) adds guardrail assertions that the README must **not** contain `Health Bridge for AI` or `open-source project behind`. Our `test_product_and_project_names_have_an_explicit_relationship` requires the opposite — `HealthRelay is a fork of Apple Health AI Bridge` in `README.md`, `docs/brand.md` and `assets/brand/README.md`. **Taking it would delete our rule**, and that rule is load-bearing: `NOTICE` records that those names and the brand assets are outside the Apache-2.0 grant, §4(d) requires retaining attribution, and a user needs to know which project they run and where to report problems. Correct for upstream, wrong for a fork that is a distinct product with its own issue tracker.
  - `8e4065e` (verified receiver install instructions point at 1.1.2) corrects `docs/versioning.md` and `docs/roadmap.md` for *upstream's* release identifiers. Ours describe our own pinned `healthrelay-receiver-<date>` scheme and must not adopt upstream's `receiver-v1.1.2` values.
  - The `upstream` remote is now configured (fetch only; push disabled), and the repeatable procedure is `docs/upstream-sync.md`. Remotes are local Git configuration and are not committed, so a fresh clone must add it first — `docs/upstream-sync.md` §1 has the two commands.
- Upstream sync, 2026-10-01: upstream checked through `8e4065e`; took the live-read test from `27ef4a4`; skipped version bump, release docs, release-guardrail tests; urllib3 already in PR #35.
- 2026-09-29: ZIP64 support and clearer errors in the export.zip reader (`MinimalZipReader`), including a disk-number check for split archives. Issue and privacy links, SUPPORT/SECURITY routing, plain-language usage strings, plurals, a Diagnostics page and export-sheet fixes; no sync, outbox or pairing change.
- 2026-09-29: README gains a "Use it with Hermes Agent" section linking the companion plugins `hermes-healthrelay` (read-only MCP + skills) and `hermes-health-insights` (local analysis CLI + skills). No code change.
- 2026-09-29: beta/stable release channels. `Publish IPA release` now creates only GitHub
  pre-releases (never "Latest"), requires a successful `iOS CI` and `Python CI` run on the exact build
  commit, and marks the notes as beta. New `Promote IPA release` workflow (manual, runs in the
  approval-gated `stable` environment) turns a verified beta into the stable "Latest" release after
  re-checking CI on the tagged commit. The README download link points at `/releases/latest`.
- 2026-09-29: Import Health Export UI brought into the pastel system. "Choose Export File" is a quiet
  capsule (`glass` on iOS 26+, bordered before) so Sync Now is the only mint button; the export path
  hint is visible text under it (it was only a VoiceOver hint). The review sheet uses the
  contrast-checked `RelayFailedInk` / `RelaySecondaryText` colors instead of system red and
  `.secondary`, sets the accent tint explicitly, and drops the ASCII double-dash from its footer.
- 2026-09-29: `Publish IPA release` takes an optional `bundle_id` dispatch input. An IPA with the
  placeholder `com.example.*` id is still accepted; any other id is accepted only if it equals that
  input exactly, so a real id is never written in the repo.
- 2026-10-07: distribution moved to signed beta builds (manual `signed-beta` workflow, reviewer-gated).
  GitHub Releases no longer carry unsigned IPA files; the `Beta access request` issue form is the
  way to ask for a build. Self-build and the `Build unsigned IPA` artifact workflow stay available.
- 2026-09-28: `Publish IPA release` refuses a build whose version is not newer than the newest
  existing `app-v*` tag, so publishing an older build can no longer land on top as "Latest".
- 2026-09-28: iOS: disabled `PrimaryButton`s no longer get a second 0.65 fade on top of the system's
  disabled dimming (the Connect button was nearly invisible in dark mode before a link is pasted).
- 2026-09-28: `Publish IPA release` workflow (manual). Takes the run ID of a successful `Build unsigned
  IPA` run on main, downloads its artifact, verifies the checksum and build stamp, reads the version
  from the IPA, refuses any bundle id that isn't `com.example.*`, and creates release `app-v<version>`
  with the IPA and SHA-256. It never overwrites an existing tag or release. Write access is limited to
  this one job; the build workflows stay read-only. Uses the `gh` CLI only, no third-party actions.
- 2026-09-28: lab-results export.zip importer (B3, labs only -- ECG already syncs live via
  HealthKit, so a manual export never needs it). Contract: optional `lab_results` array
  (`LabResult` model, migration 012, `_upsert_lab_results`, `lab_result` deleted-record family
  and sync_runs count column), uploaded through the existing `/v1/batches` endpoint like every
  other record family -- no new HTTP surface. iOS: `AppleHealthExportLabImporter` streams
  `export.zip` via a new ZIPFoundation SPM dependency (Xcode project graph +
  `Package.swift`), reads only `clinical-records/*.json` entries (never unpacked to disk),
  parses FHIR Observation fields (LOINC, name, category, date, numeric/text value, reference
  range including free-text `<`/`>`/`a-b` parsing, multi-component observations like blood
  pressure). `client_record_id` is a stable SHA-256-derived `hk-labobs-<hex16>` id (FHIR
  observation ids don't match the receiver's `hk-`/`synthetic-` pattern). New "Import Health
  Export" card, file picker, and a review sheet showing the parsed count before anything is
  sent -- on-device only until the user confirms. No cursor: manual, user-initiated, dedup is
  `client_record_id` uniqueness on the receiver.
- 2026-09-28: iOS UI for iOS 26/27 design (companion). Pastel palette drawn from the app icon, as
  named color sets (`RelayMint`/`RelayOnMint` for the primary action; `Ready`/`Waiting`/`Failed`
  tint+ink pairs for status; `AccentColor`, set as the global accent), replacing the blue, indigo,
  purple and saturated status tints. Every text pair is at least 6.9:1 in light and dark. Standard
  large title, Settings as a toolbar button (was a card), Sync Now and Cancel as a bottom-anchored
  capsule (`glassProminent` on iOS 26+, prominent capsule before), two card radii (26 / 12), 52pt
  status glyph. Copy and logic unchanged. Layered app icon: `AppIcon.icon` (Icon Composer bundle,
  pulse and chevron as separate SVG layers over a deep-teal gradient, dark-appearance fill) added
  beside the flat `AppIcon.appiconset`, which stays as the fallback for Xcode 16 and older iOS.
- 2026-09-28: README redesign and dark-theme lockup. The lockup's ink text was unreadable on
  GitHub's dark theme; `healthrelay-lockup-dark.png` (same art, text recolored to #F0F6FC/#AEB8C2
  by `_dark_lockup` in the generator) is served through `<picture>`. README restructured: hero with
  badges and navigation, fork note as a callout, what-it-adds table, Mermaid flow diagram, route
  table, collapsible reference detail, grouped documentation table. All pinned setup copy kept.
- 2026-09-28: receiver setup page and CLI copy. Both pairing setup pages (invitation and legacy)
  now carry the HealthRelay mark and name, share one stylesheet with light and dark palettes
  (every text pair at least 4.5:1), capsule buttons at least 44px tall, visible focus rings, and a
  QR plate that stays white in dark mode. "Open in Health Bridge" becomes "Open in HealthRelay";
  the manual step names the app's real "Use a code instead" control. CLI help/output and the
  systemd unit description say HealthRelay. MCP output (`# Health Bridge Context`, the database
  error) is unchanged: `fixtures/delivery_compatibility_v1.synthetic.json` pins its bytes.
- 2026-09-28: HealthRelay pairing scheme `healthrelay://pair` (receiver emits it; app registers it first;
  both sides still accept legacy `healthbridge://pair`), so a scanned QR can no longer open the upstream
  app. ECG and medication lanes honour the selected Apple Health history window on first sync (was a
  fixed 30 days). `ingest-fixture` summary now includes electrocardiograms and medication_dose_events.
- 2026-09-28 (superseded 2026-11-05, see the Version rule above): IPA builds stamp marketing version `1.2.<run number>` by default (a distinct version
  string per build, since sideload signers show only the marketing version).
- 2026-09-28: diagnostics: the ECG and medication lanes prefix their status with `[ECG]` / `[Medication]`
  and the Activity Log keeps those lines verbatim (counts and error codes only), so a lane outcome is
  visible on the phone instead of being collapsed into generic labels.
- 2026-09-28: README/setup no longer point at the upstream App Store app; install = your own build
  (`Build unsigned IPA` workflow or Xcode). Upstream website/privacy/support links kept as attribution
  (a guardrail requires them). Component table: iOS Companion (HealthRelay) 1.2.0.
- 2026-09-28: iOS display strings and contrast. User-facing "Health Bridge" text becomes
  "HealthRelay" when it names the app (header, Health permission usage descriptions, the
  Health app Privacy path) and "your server" when it names the receiver; status classifiers
  and `CompanionPrimaryStatusMessage` matchers updated in step, with their tests. Added
  contrast-checked color sets (`RelayGreen`, `RelayOrange`, `RelayRed`, `RelayBlue`,
  `RelayIndigo`, `RelayOnTint`, `RelaySecondaryText`; every text pair at least 4.5:1 in light
  and dark) and switched `ContentView` off the system tints and `.secondary` text. iOS
  marketing version 1.1.1 → 1.2.0 (project and the About fallback); Receiver/CLI stays 1.1.1
  per docs/versioning.md's independent component versions.
- 2026-09-28: the unsigned-IPA workflow stamps `MARKETING_VERSION` (input, default 1.2.0) and
  `CURRENT_PROJECT_VERSION` = workflow run number at build time, so every build is distinguishable
  on the phone (previously the version number stayed the same across installs). The tracked project keeps
  upstream's 1.1.1/50 so the release guardrails stay untouched.
- 2026-09-28: fix: the automatic sync engine scheduled `electrocardiogram` as a quantity lane
  (found on device: `quantity[electrocardiogram]:attempted/not_run`, cycle deferred). New
  `supportedAutomaticLaneTypeCodes` = unified read set filtered by catalog `backgroundEligible`;
  the view model's automatic lane selection uses it. ECG stays in the authorization/disclosure set
  and syncs through the foreground lane only.
- 2026-09-28: medication dose events, iOS side (B2 step 3b). `HealthBridgeMedicationDoseEvent`
  batch model (encoded only when non-empty), `HealthKitMedicationDoseEventReader` (iOS 26+:
  per-object read authorization for the medication and dose-event types, medication list joined
  by a SHA-256 concept key, dose events by date), `MedicationDoseEventSyncBatchFactory`
  (`foreground_medication_dose_event_sync`, `hk-meddose-<uuid>`), registry/catalog entries
  (`medication_dose_event`, category `other`, never in the unified `requestAuthorization` set),
  view-model lane after ECG, 4 XCTest cases, disclosure-doc note. Ported from
  HealthDataExporter's `MedicationExporter` (same concept-key scheme).
- 2026-09-28: receiver storage for medication dose events: migration 011 (table + sync_runs count), upsert with tombstone respect, ingest count, tests.
- 2026-09-28: medication dose events, contract only (B2 step 3a). Optional top-level
  `medication_dose_events` array (`$defs/medicationDoseEvent`: name, optional concept key,
  status enum + raw, start/scheduled UTC times, optional dose/unit), tombstone family
  `medication_dose_event`, `MedicationDoseEvent` pydantic model, fixture
  `fixtures/health_bridge_batch_v1.medication.synthetic.json`, docs. Receiver storage/ingest
  (migration 011) and the iOS reader follow as separate steps; until then the receiver parses
  and ignores the array.
- 2026-09-28: ECG app lane. `syncRecentElectrocardiograms()` in the view model (foreground,
  30-day fallback window, 3-day replay overlap, cursor `foreground_electrocardiogram_sync`),
  run as a manual-sync lane after sleep; `electrocardiogram` is now a dedicated sync type, so it
  is part of the unified read authorization set and listed in `docs/supported-health-data.md`.
  Upload policy counts ECG records. No background/anchored ECG lane yet.
- 2026-09-28: 38 HealthKit dietary quantity types (all but water, which is `hydration`) added to
  the receiver timeseries catalog, the Swift catalog expansion entries, the disclosure doc and the
  pinned tests; units g/mg/mcg/kcal as in health-insights `dietary_types.py`. Delivered as a
  Python half (R-1) plus a Swift half and pins.
- 2026-09-28: ECG, iOS core (B2 step 2). `HealthBridgeElectrocardiogram` batch model; the batch
  encodes `electrocardiograms` only when non-empty (upstream byte vectors unchanged) and decodes
  it as optional. Registry static `.electrocardiogram` (category heart, unit "recording",
  sensitivity high), catalog entry (`objectKind: .electrocardiogram`, not a dedicated lane, not
  background-eligible yet), read-type mapping to `HKObjectType.electrocardiogramType()`,
  `HealthKitElectrocardiogramReader` (`HKSampleQuery` + per-sample `HKElectrocardiogramQuery`
  voltages in microvolts, summary-only option), `ElectrocardiogramSyncBatchFactory`
  (`foreground_electrocardiogram_sync`, `hk-ecg-<uuid>`, mismatched/non-finite voltage series
  sent summary-only), 3 XCTest cases. Not yet requested in the unified authorization set, so
  `docs/supported-health-data.md` is unchanged until the app lane (next step) requests it.
- 2026-09-27: ECG, contract + receiver (B2 step 1). Batch schema: optional top-level
  `electrocardiograms` array (`$defs/electrocardiogram`), `deleted_records.record_family`
  gains `electrocardiogram`. Receiver: `Electrocardiogram` pydantic model, migration
  `010_electrocardiograms` (table + `sync_runs.electrocardiogram_count`), upsert with
  tombstone respect and voltage retention on summary-only replay, ingest count. New fixture
  `fixtures/health_bridge_batch_v1.electrocardiogram.synthetic.json` (the canonical fixture is
  byte-bound by delivery vectors, so it is unchanged); protocol version stays `1.0.0` until the
  companion ships the field. Docs: `docs/reference/batch-v1.md`, `sqlite-v1.md`. iOS reader
  and Swift batch model follow in the next step.
- 2026-09-27: brand replaced. Upstream `assets/brand/*`, `tools/generate_brand_assets.py`
  and the app icon set removed; HealthRelay mark, icon set and lockup generated by
  `tools/generate_healthrelay_icon.py` (Pillow only); `docs/brand.md`, `assets/brand/README.md`
  rewritten; guardrail tests (`test_brand_assets.py`, relationship sentence, brand README link
  count) adapted; `scripts/public-release-audit.py` allow-list updated. CI: `ios.yml` and
  `python.yml` now run only on `ios-v*` tags or manual dispatch (no push/PR runs) while
  there is nothing worth building; `release.yml` unchanged (tag-only already).
- 2026-09-27: fork created; `NOTICE` and this file added; display name "HealthRelay" in the
  project and Info.plist strings; README fork banner. The tracked project keeps upstream's
  neutral `com.example.HealthBridgeCompanion` id (public-release audit rule); the real id
  is applied at build time via `BUNDLE_ID` for `scripts/ios-device-build.sh`. No functional change yet.
