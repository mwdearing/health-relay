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
- Version: bump the iOS marketing version on every app change; CI supplies the build number.

## Modifications (newest first)
- 2026-09-27: fork created; `NOTICE` and this file added; iOS bundle id set to
  `com.mwdearing.HealthRelay`, display name "HealthRelay"; README fork banner. No
  functional change yet.
