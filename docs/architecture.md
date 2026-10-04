# Architecture and trust boundaries

Health Bridge for AI is a local bridge between HealthKit and agent-readable tools. It is not a hosted health service.

## Data path

```text
iPhone HealthKit
  -> iOS companion
  -> user-owned receiver
  -> local SQLite database
  -> read-only CLI / MCP
  -> Hermes, OpenClaw, or another local agent
```

The companion asks HealthKit for read permission only. The receiver accepts authenticated batches from paired devices and stores normalized records in SQLite. The CLI and MCP server expose fixed, bounded read-only queries over that store.

## Components

| Component | Role |
| --- | --- |
| iOS companion | Reads allowed HealthKit records, protects receiver credentials in Keychain, queues failed uploads, and sends foreground or automatic batches. |
| Receiver | Runs on user-owned infrastructure, validates device credentials, ingests batches, and writes SQLite rows. |
| SQLite store | Keeps sources, type codes, time windows, sync runs, tombstones, and redacted cursor metadata. |
| Setup CLI | Creates the private store and pairing page, prepares the receiver, emits a client-neutral stdio MCP descriptor, and optionally runs installed client adapters. |
| MCP server | Exposes fixed read-only health observation tools. It does not provide raw SQL access. |

## Unified HealthKit scope

The product has one read scope: every implemented HealthKit type available on the current iOS runtime. The same set drives the native authorization request, foreground sync, observer registration, scheduled background refresh, and launch catch-up.

The code uses different **sync strategies**, not product tiers:

- steps use a dedicated anchored lane;
- workouts use a dedicated anchored lane;
- sleep uses a correction-aware anchored lane;
- supported quantity types use the generic anchored quantity lane.

Those implementation paths do not create “basic” and “additional” user scopes. Apple’s authorization sheet remains the only per-type allow/deny interface.

A type can produce records only when the companion implements its reader and payload mapping, the current runtime can construct the HealthKit object type, and the user grants read access. Unsupported, unavailable, denied, or absent data remains unknown rather than being fabricated.

`Sync Now` attempts the complete runtime-supported set and may perform historical catch-up. Automatic paths prioritize recent changes and use bounded fallback windows where no cursor exists. iOS controls whether and when background execution occurs, so automatic delivery is eventually complete rather than guaranteed to be immediate. Force-quitting the iOS app may suspend background delivery until the user opens it again.

HealthKit stops launching an app for observer wake-ups after three consecutive misses of the `HKObserverQuery` completion handler, and that loss survives an app update. The companion therefore acknowledges each observer wake-up as soon as the admission cycle finishes **or** 15 seconds pass, whichever comes first, and keeps processing the cycle afterwards. The timer starts in the HealthKit callback itself, off the main actor, and counts the time already spent since the callback, so a blocked or backlogged main actor can neither delay nor skip the acknowledgement. A wake-up answered by the deadline instead of by a finished cycle is recorded as `observer completion=deadline` in the automatic-sync diagnostic, so a slow cycle is visible without reading health values. When the app becomes active with automatic sync on, it also re-arms background delivery for the observed types (disable, then enable again at the same frequency), debounced to at most once per foreground session and never more than once every 10 minutes; the outcome appears in the background-sync activity log. A re-arm is per type, only enables after its own disable returns, skips the enable entirely if automatic sync stopped in the meantime, and counts as failed when HealthKit rejects the disable. Re-arming does not change which types are observed or how often iOS chooses to deliver.

Daily activity aggregates remain an internal derived projection. They use HealthKit statistics for completed local-day totals such as steps, distance, energy, exercise time, stand time, flights, and basal energy. In-progress local-day statistics are not published as daily totals; current-day raw, source-attributed samples remain separate. These aggregates are not a second user-selectable sync scope.

## Pairing and authorization boundary

Pairing invitations are temporary and single-use. Redemption produces a device credential stored in the iOS Keychain and hashed by the receiver.

The current receiver is a **single-user store**. Every active paired device credential authorizes writes to that whole store; the device mapping supports lifecycle and revocation but is not a multi-tenant data-isolation boundary. A hosted relay, shared receiver database, or mutually untrusted devices would require a new principal and namespace design before release.

## Delivery and recovery

The HTTP receiver gives each connection a 30-second socket read timeout for
the request line, headers, and body. A stalled request ends with HTTP 408
`{"error":"request_timeout"}` and closes the connection when a response can
be sent; an idle connection without a request is closed silently. The Python
`build_receiver_server` and `serve_receiver` APIs accept
`request_timeout_seconds` to adjust this timeout. It bounds stalled socket
reads, rather than total request processing time. Normal successful responses
retain their existing connection behavior; idle keep-alive reads also time out.

Unexpected request-handling exceptions return HTTP 500
`{"error":"internal_error"}` with `Connection: close`. Logs contain only the
exception type, without its message, traceback, credentials, or request data.
If a response has already started or the peer cannot receive it, the connection
closes without a second response. Existing validation, authentication, and
storage error mappings remain in place, and other connections keep serving.

Uploads enter a private ordered outbox. Direct is the default transport. Encrypted iCloud Mailbox is an explicit opt-in, Mac-only Beta, and a Direct failure never causes an automatic transport fallback.

For Direct, a successful HTTP response is required before the corresponding committed progress advances. For mailbox delivery, the app applies application-layer encryption and a sender signature before publishing to the user's iCloud container. The user-owned receiver decrypts and commits accepted batches before publishing an encrypted, receiver-signed ACK; the app advances committed progress only after validating a committed ACK. Failed or interrupted deliveries remain queued and retry in order on their selected transport.

Cursorless automatic reads may send a bounded recent window but do not silently consume the wider foreground backfill. Status surfaces expose cursor metadata and sync outcomes without exposing opaque cursor values.

### Sleep corrections

Sleep sessions are derived from child HealthKit samples, so the companion keeps a private, backup-excluded manifest and transition journal. It advances the opaque HealthKit sleep anchor only after the exact correction or deletion payload has been accepted by the receiver.

Each installation uses a separate sleep source key and a Keychain-backed ordered reset epoch. The receiver rejects older reset epochs and ignores an already-authoritative equal epoch, preventing delayed baselines from replacing newer state. Empty baselines never delete prior data until a non-empty authoritative baseline for that epoch arrives.

During a user-confirmed private-state reset, deletion occurs only after upload admission is closed, active transfers are drained, the connection generation is revalidated, and the durable clear intent is persisted. Launch recovery finishes an interrupted clear before automatic sync resumes.

## Intake context

Intake context carries logged intakes (what was taken, with its facts and links
to HealthKit samples) next to the base batch path. Its contract is
[intake-context-v1](reference/intake-context-v1.md); the read side is
[intake-evidence](reference/intake-evidence.md).

Trust boundaries:

- **Routes behind a flag.** `GET /v1/intake-context/capabilities` and
  `POST /v1/intake-context/batches` exist only when the receiver is built with
  `intake_context_enabled`. Start with
  `health-bridge receiver start --db .tmp/device.sqlite --enable-intake-context`
  to enable them for that process. The flag is off by default; when off, both
  paths answer 404 like any unknown path. It also applies with
  `--service-config` or mailbox options, enabling the HTTP routes without
  changing mailbox delivery. Each CLI start reports whether the routes are
  enabled. Generated mailbox service commands keep the default off.

### Enabling intake context

`health-bridge receiver intake-setup` does the database half of this in one
command and prints the rest: it registers the producer, issues its
intake token into a mode-0600 private file (never printed), and prints the
restart, smoke-check and secret-handling steps in order. Order matters — a token
can only be issued for a producer that is already registered. The registration
step is the idempotent one; issuing a token is not, so the command as a whole is
not repeatable.

```bash
health-bridge receiver intake-setup \
  --db .tmp/device.sqlite \
  --owner-id <owner> --producer-id nutrition-app \
  --writer-bundle-id dev.example.nutrition \
  --producer-label "Nutrition app" --token-label phone \
  --output-secret .private/intake-token.json \
  --url http://127.0.0.1:8765 \
  --start-option=--request-timeout=30

health-bridge receiver start --db .tmp/device.sqlite --enable-intake-context \
  --request-timeout 30

health-bridge receiver intake-smoke \
  --url http://127.0.0.1:8765 \
  --token-file .private/intake-token.json
```

The printed next steps quote their paths and carry the endpoint this run was
given, so the commands work for a path with spaces and for a receiver that does
not answer on the default host and port. `--url` supplies that endpoint and
`--start-option` repeats a receiver start option in the printed restart
command; without `--url` the smoke command prints `<receiver URL>` to fill in
rather than assuming one.

`intake-setup` refuses an existing `--output-secret` file and leaves it
untouched, because overwriting it would destroy a working credential the caller
cannot re-derive; the destination is claimed with an exclusive create, so two
concurrent runs cannot both be told the path is free and then overwrite each
other's secret. A claim whose permissions cannot be tightened is removed again,
so a filesystem that cannot keep the file owner-only fails one run instead of
blocking the next. `--rotate` is the explicit way to replace it, issuing a new
token into the file and revoking the token the file held so only one credential
for the producer stays usable; a rotation whose old token cannot be identified
from the file is refused rather than leaving that credential usable. `rotated`
is reported only when a previous token was really retired.

A rotation holds a mode-0600 lock file next to the secret for its whole run, so
two rotations cannot each activate a replacement and retire only the token that
was already there. A failed revocation exits non-zero instead of warning: the
replacement is active and written, so the rotation is incomplete until the
prefix named in the message is revoked by hand. The new secret stays in place,
because it is valid and the only copy of that token.

`--producer-label` and `--token-label` name the producer and its credential
separately, as the individual commands do; without `--token-label` the producer
label is reused. `--url` is validated before anything is written, so an endpoint
the printed smoke command could never use cannot leave an activated credential
behind. A service config passed as a start option replaces `--db` in the printed
restart command, because `receiver start` refuses both together.

The same work by hand, one command per step:

```bash
health-bridge receiver intake-register-producer \
  --db .tmp/device.sqlite \
  --owner-id <owner> --producer-id nutrition-app \
  --writer-bundle-id dev.example.nutrition --label "Nutrition app"

health-bridge receiver intake-create-token \
  --db .tmp/device.sqlite \
  --owner-id <owner> --producer-id nutrition-app --label phone \
  --output-secret .private/intake-token.json

health-bridge receiver start --db .tmp/device.sqlite --enable-intake-context \
  --request-timeout 30

health-bridge receiver intake-smoke \
  --url http://127.0.0.1:8765 \
  --token-file .private/intake-token.json
```

`intake-register-producer` is idempotent for identical details and fails closed
on a different writer bundle or label, so the producer identity a token is bound
to cannot drift after onboarding; the comparison runs inside the write
transaction and ignores `registered_at`, so a restored backup still matches.
`intake-create-token` stores only a hash and requires an explicit secret
destination: `--output-secret` writes the token to a mode-0600 private file and
prints only the prefix, refuses a destination that resolves to the database or
one of its sidecars, and drops the token if the write fails. Its producer check
and the token insert share one transaction, so an unknown or revoked producer
never receives a token. With `--output-secret` the row is inserted in a `pending`
state and activated only after the secret is on disk, with the producer
rechecked in that same statement, so neither a failed write nor a producer
revoked mid-write can leave an active credential behind even when the database
cannot be reached to revoke one. A pending row is distinct from a revoked one,
so `intake-revoke-token` or `intake-revoke-producer` applied while a secret
file is still being written retires that credential permanently instead of
being undone by the later activation. Any secret file a failed write or a
failed activation replaced is put back. `intake-list-producers` and
`intake-list-tokens` open the store read-only and never create or migrate it;
token rows report a `status` of `active`, `pending` or `revoked`, and
`intake-revoke-token` exits non-zero when no usable token carries the prefix.

`intake-revoke-producer` retires a producer and every active token it owns in one
transaction; `intake-reactivate-producer` clears the revocation but leaves those
tokens revoked, so a new token is required. Both exit non-zero for an unknown
producer or one already in the opposite state, and re-registering a revoked
producer is refused with a message naming `intake-reactivate-producer`.
`intake-smoke` checks the capabilities route with a token read from a private
file, prints only the status and capability fields, and reports a 404 as "the
intake routes are not enabled". A redirect is refused rather than followed so the
bearer token cannot reach another host, and a 200 that is not an intake
capabilities document (wrong schema, no supported version, missing capability
fields, or capability values an uploader cannot use) is reported as a failure
rather than a passing smoke check. The token must be a well-formed generated
token, `--url` must be an http(s) URL with a host that can actually be opened,
only a bounded capabilities document is read, and a connection that stalls,
hangs up or answers with an unparseable status line is reported as an
unreachable receiver rather than a traceback.
`--request-timeout` is a socket-read inactivity timeout in seconds (greater than
0, at most 300, default 30): it restarts on each blocking read, so it bounds how
long the receiver waits on a silent connection rather than total request time.
See
[Pairing and receiver setup](pairing.md#intake-producers-and-intake-tokens) for
the full command reference.
- **Intake-only tokens.** These routes accept only tokens stored in
  `intake_context_tokens` (prefix `hri_`, bound to an owner and a producer).
  Such a token cannot use `/v1/batches` (403 `wrong_token_type`), and a normal
  batch token cannot use the intake routes (also 403). An unknown token gets 401.
- **Owner binding.** The owner comes from the token, never from client input.
  The receiver stays a single-user store.
- **Rate limit.** `POST /v1/intake-context/batches` allows 60 batches per
  60 seconds per intake token, counted on a sliding window keyed by the token
  prefix. Over the limit, the receiver answers 429 `rate_limited` with a
  `Retry-After` header giving whole seconds until a slot frees (at least 1) and
  closes the connection; the body is neither read nor stored. Failed
  authentication is not counted, so a bad token cannot spend a producer's
  budget, and each token is limited independently of the others. Limits are
  set per server with `intake_rate_limit_count` and
  `intake_rate_limit_window_seconds`, and idle token keys are dropped so memory
  stays bounded.
- **Capabilities limit and caching.** `GET /v1/intake-context/capabilities`
  has its own fixed budget of 30 requests per 60 seconds per intake token,
  separate from the batch budget, so polling capabilities never blocks uploads.
  Over it the receiver answers 429 `rate_limited` with `Retry-After`, the same
  way as the batch route. A 200 carries `Cache-Control: private, max-age=300`
  and `Vary: Authorization`: a client may reuse it for five minutes for the
  same token, and shared caches must not store it.
- **Operation outcomes.** Every fully identifiable operation in a batch gets
  one result: `accepted`, `duplicate`, `stale_revision`, `domain_conflict`,
  `projection_conflict`, `retryable_failure` or `permanent_failure`. If an
  entry lacks an `operation_id` or is not an object, valid operations in the
  same batch may still commit, but the handler answers only 400
  `invalid_batch` because the result count does not match. The caller then
  cannot tell which operations committed; it should resend the whole batch,
  and committed operations come back as `duplicate`.
- **Evidence query.** A read-only query joins stored intakes with their linked
  samples. Each item carries the logged intake's own amount, unit, `value_state`
  and component code; the joined HealthKit sample's measurement value is
  withheld.
- **Query CLI.** `health-bridge query intake-evidence` prints that same page as
  one JSON document for a terminal. It opens an existing database read-only,
  never migrates it and never writes.
- **MCP.** The read-only tool `get_intake_evidence_v1` exposes that query to
  agents. `owner_id` is optional only when exactly one owner is registered;
  with none or several the tool returns an error. The tool cannot write.

## Agent boundary

MCP tools expose:

- redacted bridge status;
- supported and synced metric catalogs;
- bounded time-series observations;
- workouts, sleep summaries, and daily summaries;
- source provenance and missing-data caveats.

They do not expose raw SQL, pairing material, bearer tokens, token hashes, opaque cursor values, or clinical recommendations.

## Deployment boundary

- Continuous sync away from home requires a stable phone-reachable private HTTPS route to the user-owned receiver.
- Tailscale Serve is documented for people who already use Tailscale; an agent-assisted private HTTPS ingress is the provider-neutral alternative.
- The Direct transport is the default. Within Direct setup, LAN-only access is a deliberate local-only routing fallback rather than the primary away-from-home path.
- For private HTTPS routes, keep the receiver on loopback behind the proxy or tunnel. Route C deliberately uses a non-loopback LAN bind and must never be port-forwarded. Do not expose port `8765` or the pairing page directly to the public internet.
- Plain LAN HTTP exposes health payloads and the device credential to that network while in transit.
- SQLite is protected by owner-only filesystem permissions, not by an application-level database encryption layer.
- The project includes no telemetry, advertising, hosted relay, hidden cloud upload, or third-party AI call by default.
- The developer does not operate or have access to the user's receiver or iCloud container. The App Privacy “Data Not Collected” posture depends on preserving that boundary.

## Operational limits

`health-bridge setup` prepares the receiver command and private pairing material but does not silently install or enable an operating-system service. Users who explicitly select Encrypted iCloud Mailbox (Beta) may separately install its optional per-user macOS LaunchAgent. Hosted infrastructure remains outside the current scope.
