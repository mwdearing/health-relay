# HealthRelay: fork notes

HealthRelay is a private fork of [Apple Health AI Bridge](https://github.com/roian6/apple-health-ai-bridge)
(Apache-2.0). Forked 2026-09-27 at upstream `4818cdc` (iOS companion 1.1.1 build 50,
receiver 1.1.1, batch schema `health_bridge.batch.v1` 1.0.0). Upstream is the `upstream`
git remote; `origin` is the private repo `mwdearing/health-relay`.

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
- Version: bump the iOS marketing version on every app change; CI supplies the build number.

## Modifications (newest first)
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
- 2026-09-28: IPA builds stamp marketing version `1.2.<run number>` by default (a distinct version
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
  on the phone (Michael: the version did not change between installs). The tracked project keeps
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
  pinned tests; units g/mg/mcg/kcal as in health-insights `dietary_types.py`. Python half by the
  Hermes bot (R-1), Swift half and pins by Claude.
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
