# Changelog

Recent changes to the HealthRelay receiver and app. The newest entry is first. This file lists what changed in the code and its contracts.

## Unreleased

- **#170**: each source owns its health type rows, keyed by `(source_id, type_code)`. A batch writes only the sources it declares, and stored aliases are capped at 64 per owned type. Migration `016_source_scoped_types` rebuilds `health_types`, `health_type_aliases` and `samples`. Closes #135.
- **#169**: migration `015_range_guards` adds database triggers that refuse reversed intervals and negative workout duration, energy and distance. Schema reference: `docs/reference/sqlite-v1.md`.
- **#168**: `POST /v1/batches` allows 60 requests per 60 seconds per batch token, then returns 429 `rate_limited` with `Retry-After`. Failed authentication is not counted. The budget is set by `batch_rate_limit_count`.
- **#167**: the iCloud Mailbox Beta is out of support scope in the README and SECURITY.md.
- **#165**: a deadline counts only for a request the client started.
- **#164**: the unsigned IPA release workflows are removed. `build-ipa.yml` remains as the self-build path. Releases are not an install path. Older `app-v*` releases keep their unsigned IPAs as unmaintained history.

## Migrations

Migrations run in order through `016`. A running receiver applies a migration only after it restarts from a checkout that includes it.
