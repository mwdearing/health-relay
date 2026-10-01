# healthrelay.intake-context v1 Contract

`healthrelay.intake-context` is a proposed batch shape for intake context: what
a person ate or drank, as recorded by a registered producer app, including
components that HealthKit has no quantity type for (for example a creatine
supplement stirred into water). It lives beside the measurement contract and
does not change it.

Status: proposed extension. The current `health_bridge.batch.v1` schema does
not accept any of this, and its strict metric allowlist, source validation,
record validation and device source binding stay intact. Intake context is sent
by a registered producer identity with its own narrowly scoped credential. It
never impersonates an `apple_health.*` source in the measurement API.

The machine-readable contract is
[`schemas/healthrelay.intake-context.v1.schema.json`](../../schemas/healthrelay.intake-context.v1.schema.json)
(JSON Schema draft 2020-12). Shared fixtures live in
`tests/fixtures/intake_context/`, and `tests/intake_context/reference.py` is an
executable reference for everything in the hashing sections below. Every UUID,
bundle ID and sample in the fixtures is synthetic test data.

## API surface

The receiver exposes two versioned routes:

- `GET /v1/intake-context/capabilities` reports the supported major and minor
  schema versions, the maximum payload size, the maximum operation count, the
  authentication requirements and the supported features.
- `POST /v1/intake-context/batches` accepts one batch and returns one result per
  operation.

The receiver derives the owner binding from its authenticated configuration. A
client-supplied account ID never selects a database tenant, and the schema has
no field for one. The current single-user receiver stays single-user.

## Versioning

- `schema` must be `healthrelay.intake-context`.
- `schema_version` is `"<major>.<minor>"`. This schema describes exactly
  `"1.0"`. Any other value, including `"1.1"`, `"2.0"`, `"1"` and `"1.0.0"`,
  fails the schema. Consumers must reject an unknown major version before
  reading anything else, and a minor they do not support, with a
  `permanent_failure` result. The supported versions are reported by the
  capabilities route.
- All objects are closed, so a minor can only be accepted by a consumer that
  knows its schema. A later `1.x` may add optional fields only when a consumer
  of an earlier minor could safely retain them without changing any total. Each
  such change adds the minor to this schema and updates this document in the
  same change.
- Hashes are computed over the object exactly as sent. A consumer that supports
  a minor but does not interpret an additive field in it still includes the
  field when it recomputes a hash, and retains it with the stored operation.
- Anything else, including a new required field, a changed meaning or a
  removed value, is a new major version.

## Batch shape

| Field | Meaning |
| --- | --- |
| `schema` | Constant `healthrelay.intake-context`. |
| `schema_version` | Version string described above. |
| `batch_id` | Delivery identifier for the batch. Transport only. |
| `producer_id` | Registered producer, for example `nutrition-app`. |
| `writer_bundle_id` | Bundle identifier the producer expects to write HealthKit samples as. The receiver checks it against the registered writer. |
| `installation_id` | App installation that sent the batch. Provenance only. |
| `operations` | One or more operations, each `upsert`, `delete` or `link_projection`. |

All objects are closed: an unknown property anywhere is a schema failure.
UUIDs are lowercase canonical text, so a HealthKit sample UUID must be
lowercased before it is sent.

**JSON text.** A duplicate object name anywhere in the payload makes it
invalid, as does a number written with a decimal point or an exponent (`2.0`,
`2e0`), `NaN`, an infinity, or an unpaired surrogate escape such as `\ud800` in a
string or an object name. A receiver rejects all of these while parsing, before
schema validation and before any hash is computed, with a `permanent_failure`
result.

**Rules beyond the schema.** JSON Schema cannot express the following, so the
receiver checks them and `tests/intake_context/reference.py` implements them.
Each makes the operation a `permanent_failure`:

- `component_id` is unique among the facts of one `upsert`.
- Every link's `component_id` in an `upsert` names a fact of that same
  `upsert`, and that fact is a `nutrient`. Compounds and blends have no
  HealthKit quantity type and travel in the context only.
- A `(component_id, healthkit_sample_uuid)` pair appears at most once in one
  `healthkit_links` array, in an `upsert` and in a `link_projection`.
- One `healthkit_sample_uuid` is `active` on at most one component in a
  `healthkit_links` array; a sample is never counted for two components.
- One sync identity (`component_id`, `healthkit_type`, `sync_identifier`) is
  `active` on at most one sample UUID; HealthKit replaces, never duplicates, the
  object a sync identifier names.
- A nutrient's `code` is a HealthRelay catalog code (`hydration` or
  `dietary_<name>`, for example `dietary_caffeine`), and a link's
  `healthkit_type` is the type of the linked fact's `code`: `hydration`
  is `HKQuantityTypeIdentifierDietaryWater`, and every `dietary_<name>` code is
  `HKQuantityTypeIdentifierDietary<Name>` with each part capitalised
  (`dietary_vitamin_b6` is `HKQuantityTypeIdentifierDietaryVitaminB6`).
- `time_zone` is a name in the IANA time zone database. Host-local or
  implementation-specific keys (`localtime`, `posixrules`, `Factory`, and the
  `posix/` and `right/` copies) are rejected: they do not name one zone on every
  receiver.
- Every timestamp is a real RFC 3339 instant: a calendar date that exists, an
  hour from 0 to 23, minutes and seconds from 0 to 59, and an in-range offset. A
  leap second (`:60`) is rejected; the producing platforms cannot represent one.
- The offset of `occurred_at` is the offset of `time_zone` at that instant, so
  the local wall clock and the instant agree for every consumer.

## Identity and scope

An intake is identified by the receiver owner, the registered producer and the
`intake_id`. The installation ID does not take part in that identity, so an
intake restored onto a new installation is the same intake and not a new event.

`(receiver owner, producer, intake_id, revision)` identifies an immutable set of
domain facts. The receiver rejects different facts at that identity even when
the sender supplies a new `operation_id`, and it does not let a restored store
or a concurrent client overwrite an accepted revision through a new delivery
identifier.

## Operations

### `upsert`

Records the facts of one intake revision and the first link snapshot for it.
Every field below is required.

| Field | Meaning |
| --- | --- |
| `operation_id` | Delivery identity of this operation. |
| `operation` | `upsert`. |
| `intake_id` | Stable intake identity. |
| `revision` | Integer, at least 1. Monotonic per intake. |
| `projection_sequence` | Always `1`: an upsert opens the projection lifecycle of its revision. See "Revisions and projection lifecycle". |
| `occurred_at` | RFC 3339 date-time with an explicit offset or `Z`. |
| `time_zone` | Name in the IANA time zone database, for example `America/Chicago`. |
| `recorded_at` | RFC 3339 date-time at which the producer recorded it. |
| `category` | Lowercase slug such as `beverage`, `food` or `supplement`. |
| `display_name` | Human-readable name, 1 to 200 characters. |
| `serving` | `{ "amount": <decimal string>, "unit": <string> }`. |
| `facts` | Non-empty array of facts (below). |
| `healthkit_links` | Complete link snapshot, possibly empty. |
| `nutrition_completeness` | `complete`, `partial` or `unknown`. Describes the source data for this intake, not the day. |
| `domain_facts_hash`, `projection_hash`, `client_payload_hash` | Digests defined below. |

### `delete`

A minimal deletion carries the same producer and intake identity, a higher
`revision`, `operation: "delete"`, an `operation_id`, `deleted_at` and the
digests. It carries no food details, no facts and no links. The receiver stores
a tombstone so that a delayed older upsert cannot resurrect the intake.

Required fields: `operation_id`, `operation`, `intake_id`, `revision`,
`deleted_at`, `domain_facts_hash`, `client_payload_hash`.

### `link_projection`

Reports a change in HealthKit links without rewriting nutrition facts. A later
HealthKit save may reveal a sample UUID after the facts were already accepted.
It carries `intake_id`, the target intake `revision`, a `projection_sequence`
of 2 or more (sequence 1 belongs to the revision's upsert),
the complete `healthkit_links` snapshot, its own `operation_id`, and the
`projection_hash` and `client_payload_hash` digests. It has no `facts` and no
`domain_facts_hash`. Its facts are not in the operation, so the receiver
checks that every link's `component_id` names a fact of the stored target
revision. A link naming another component is a `permanent_failure`. This is a
receiver check; the reference validator sees only the operation and cannot
enforce it.

## Facts

Each fact describes one component of the intake.

| Field | Meaning |
| --- | --- |
| `component_id` | Slug, unique within the operation. Links refer to it. |
| `kind` | `nutrient`, `compound` or `blend`. |
| `code` | Slug naming the component, for example `hydration`, `caffeine` or `creatine_monohydrate`. Water is always `hydration`; `dietary_water` is rejected. |
| `label_name` | Optional name as printed on the label. |
| `amount`, `unit` | Decimal string and unit. Present only as the value state allows. |
| `value_state` | `known`, `unknown`, `not_applicable` or `below_reporting_threshold`. |
| `quantity_basis` | `compound_mass`, `active_nutrient_mass` or `unknown`. Required for a compound, optional otherwise. |
| `aggregation_role` | `context_only`, `compound_measurement` or `blend_total_only`. A compound uses `compound_measurement`, a blend uses `blend_total_only`, and a nutrient uses `context_only`. |
| `provenance` | `user_confirmed`, `label_confirmed`, `ocr_confirmed` (a label read by OCR and confirmed by the user), `catalog_reference`, `recipe_calculated` (derived from a recipe's ingredients) or `estimated`. |
| `members` | Blends only. Non-empty array of blend members. |

**Decimal strings.** Every amount is a decimal string matching
`^(0|[1-9][0-9]*)(\.[0-9]+)?$`. It is never a JSON number, so binary
floating-point drift cannot enter the contract. The string is hashed exactly as
written, so `"5"` and `"5.0"` are different content; a sender must keep the
spelling stable across retries. Only `revision`, `projection_sequence` and
`sync_version` are integers, and they are written without a decimal point or
exponent. They range from 1 to 9223372036854775807 (signed 64-bit), the range the
receiver's SQLite `INTEGER` columns can store.

**Patterns.** Identifier, digest, timestamp, decimal and unit patterns must match the
whole string. Units are printable ASCII (`[!-~]`), because ECMA-262 and Python
disagree on which characters `\S` excludes. Python's `re` lets `$` match before a final newline, so every
anchored pattern ends in `(?!\n)$`, which rejects `"value\n"` in both Python and
ECMA-262 validators.

**Value state.** Unknown is never zero.

- `known` requires both `amount` and `unit`.
- `unknown` and `not_applicable` forbid `amount` and `unit`.
- `below_reporting_threshold` forbids `amount`; `unit` is optional.

An unknown energy value is simply a fact with `value_state: "unknown"` or no
energy fact at all. It is never treated as zero.

**Blends.** A proprietary blend is one fact with `kind: "blend"`, the label's
total `amount` and `unit`, `aggregation_role: "blend_total_only"` and a
`members` array. Each member has a `label_name` and, only when the label
discloses it, an `amount` with a `unit`. Member amounts are never invented and
never split evenly from the total. Only the blend total counts in any total.

**Compounds.** A compound has no HealthKit quantity type. It is recorded with
`kind: "compound"`, a `quantity_basis` (`compound_mass` for the mass of the
compound itself, `active_nutrient_mass` for the mass of the active nutrient) and
`aggregation_role: "compound_measurement"`. Both are required, and no other
kind may use `compound_measurement`.

## HealthKit links

| Field | Meaning |
| --- | --- |
| `component_id` | The fact the sample belongs to. |
| `healthkit_sample_uuid` | Lowercase UUID of the saved sample. |
| `healthkit_type` | Quantity type identifier, `HKQuantityTypeIdentifier...`. |
| `sync_identifier` | Sync identifier the producer saved the sample with. |
| `sync_version` | Integer, at least 1. |
| `disposition` | `active`, `superseded` or `deleted`. |

A link is a claim that the receiver verifies, not proof. The receiver joins a
component to an accepted measurement by exact sample UUID, then checks the
quantity type and the actual source bundle against the registered writer. The
sync identifier and version can be checked only once the HealthKit exporter
forwards the sample's sync metadata, which the current measurement lane does not
carry; until then they are recorded from the link and not verified. It never joins on name, timestamp or amount
alone, and a copied metadata string is not proof of source ownership. Superseded
and deleted links are kept for audit and never for resurrection. A sample is not
attributed to more than one active component without a flagged conflict.

## Revisions and projection lifecycle

- `revision` is monotonic per intake. A newer revision replaces the facts of an
  older one and retires the older projections from active totals.
- `projection_sequence` is monotonic within one `(intake_id, revision)`. The
  `upsert` for a revision always carries sequence 1 (the schema enforces it) and each later link-only change to
  the same revision uses the next integer, without rewriting facts. A
  `link_projection` always carries the complete link snapshot, never a delta.
- Equal sequence with an equal `projection_hash` is a duplicate. Equal sequence
  with a different `projection_hash` is a conflict. A lower sequence cannot
  replace newer state.
- A `link_projection` targets the intake's current accepted revision. A target
  revision that has not been accepted yet is a retryable failure, and an older
  one is stale.
- A tombstone rejects stale upserts and stale link operations for that intake.
- A `delete` must carry a higher revision than any accepted revision.

## Canonical JSON

Hashes are taken over canonical JSON bytes, so two implementations in any
language must produce the same bytes for the same value.

1. Object keys are sorted by Unicode code point (not locale order), at every
   depth. Keys are not renamed. A duplicate object name is invalid input, so
   there is never a duplicate to resolve.
2. There is no whitespace. Separators are `,` between members and `:` between a
   key and its value.
3. Strings are encoded as UTF-8 with no ASCII escaping. Only the escapes JSON
   requires are used: `\"`, `\\`, `\b`, `\f`, `\n`, `\r`, `\t`, and `\u00xx`
   with lowercase hex for any other control character below U+0020. Every other
   character, including non-ASCII text, U+007F and U+2028, is written as its raw
   UTF-8 bytes. A string containing an unpaired surrogate has no UTF-8
   encoding and is invalid input; it is rejected before canonicalisation.
4. There are no floating-point numbers, `NaN` or infinities anywhere. Decimals
   are strings. Integers are written in plain decimal digits with a leading `-`
   only when negative, no leading zeros, no `+`, and no fraction or exponent.
5. Arrays keep their order. Order inside `facts` and `members` is therefore part
   of the content.
6. `true`, `false` and `null` are written in lowercase.

The digest string is `sha256:` followed by the lowercase hexadecimal SHA-256 of
those bytes, 64 hex characters. Anything that is not exactly that shape fails
the schema, including placeholder text.

For example, `{"b":[2,1],"a":{"d":"x","c":null}}` canonicalizes to
`{"a":{"c":null,"d":"x"},"b":[2,1]}`.

## Hashes

There are three digests. Each one includes the stable producer scope through
`producer_id`, so the same intake ID from a different producer never collides.
The server recomputes every digest it needs from the received operation and
rejects an operation whose supplied digest does not match.

Compute them in this order, because `client_payload_hash` covers the other two.

### `domain_facts_hash`

Present on `upsert` and `delete`. The digest of one object:

- `producer_id` taken from the batch, plus
- every operation field **except** `operation_id`, `projection_sequence`,
  `healthkit_links`, `domain_facts_hash`, `projection_hash` and
  `client_payload_hash`.

For an upsert that covers `operation`, `intake_id`, `revision`, `occurred_at`,
`time_zone`, `recorded_at`, `category`, `display_name`, `serving`, `facts` and
`nutrition_completeness`. For a delete it covers `operation`, `intake_id`,
`revision` and `deleted_at`. Transport links and delivery fields are excluded,
and `installation_id` never enters it.

Use: it identifies the immutable facts at `(owner, producer, intake_id,
revision)`. The same facts arriving again, under any `operation_id`, are safe to
replay. Different facts at the same identity are a domain conflict.

### `projection_hash`

Present on `upsert` and `link_projection`. The digest of one object:

```json
{
  "producer_id": "<batch producer_id>",
  "intake_id": "<operation intake_id>",
  "revision": 2,
  "projection_sequence": 1,
  "healthkit_links": [ "... links sorted by (component_id, healthkit_sample_uuid) ..." ]
}
```

The links are sorted by `component_id`, then by `healthkit_sample_uuid`, both
compared by code point, so the digest does not depend on the order in which the
sender listed them. A `(component_id, healthkit_sample_uuid)` pair appears at
most once, so the order is total; an array with a repeated pair is rejected
before hashing. `installation_id` never enters it.

Use: it identifies one complete link snapshot of one revision at one sequence.
It decides duplicate versus projection conflict at an equal sequence.

### `client_payload_hash`

Present on every operation. The digest of one object:

```json
{
  "producer_id": "<batch producer_id>",
  "writer_bundle_id": "<batch writer_bundle_id>",
  "installation_id": "<batch installation_id>",
  "schema_version": "<batch schema_version>",
  "operation": "<the operation object without its own client_payload_hash>"
}
```

The nested operation includes the other two digests and every field as sent,
including additive fields from a later minor version.

Use: it identifies the delivered content of one `operation_id`. The same
`operation_id` with the same `client_payload_hash` is a duplicate. The same
`operation_id` with a different `client_payload_hash` is a conflict, never a
duplicate success. It is the only digest that changes when only the installation
or the asserted HealthKit writer (`writer_bundle_id`) changes, so a replay under a
different writer is a conflict, not a duplicate.

### Worked values

For `valid_worked_example.json` (a 500 mL water drink with 5 g of creatine
monohydrate, revision 2, projection sequence 1) the digests are:

```text
domain_facts_hash   sha256:93bb96b900c8d22d77630236eb60ec9c453e0627009d9fe01041ea3ef438c4f0
projection_hash     sha256:8d5f54713418cd2f525dbf176e4d9e6c73241ded8ac5f9288db6e0179b8f71fd
client_payload_hash sha256:873b7b15148917d14c17c36b074f8e4bb3fd1d0afea2652c8fb5319e54e81003
```

The canonical bytes hashed for `projection_hash` there are:

```text
{"healthkit_links":[{"component_id":"water","disposition":"active","healthkit_sample_uuid":"2c932bd1-c46d-4e38-b481-e0d842fdd429","healthkit_type":"HKQuantityTypeIdentifierDietaryWater","sync_identifier":"intake:e6677963-418c-4027-b563-551d8a531eed:water","sync_version":2}],"intake_id":"e6677963-418c-4027-b563-551d8a531eed","producer_id":"nutrition-app","projection_sequence":1,"revision":2}
```

## Operation outcomes

The receiver returns one result for every operation in the batch. The result is
one of:

| Result | Meaning |
| --- | --- |
| `accepted` | The operation is durably committed, not merely queued in memory. |
| `duplicate` | The same content was already committed. Nothing changes. |
| `stale_revision` | A newer revision, or a tombstone, already supersedes it. The result reports the server's current revision and nothing about unrelated records. |
| `domain_conflict` | Different facts at an identity that already has facts, or a reused `operation_id` with different content on an `upsert` or `delete`. |
| `projection_conflict` | A different link snapshot at an equal sequence, or a reused `operation_id` with different content on a `link_projection`. |
| `retryable_failure` | The operation could not be committed now. Retry the identical payload. |
| `permanent_failure` | The operation is invalid, including an unknown major version, a schema failure or a digest that does not match. Retrying the same payload will not help. |

An `accepted` result carries the accepted intake revision, the accepted
projection sequence and a server cursor. A `stale_revision` result carries the
server's current revision.

Operations in one batch are applied in array order, and each operation sees
the effects of the operations before it in the same batch: an `upsert` followed
by a `link_projection` for that same new revision is valid. Each operation still
gets its own result; a failure does not undo earlier accepted operations.

Receivers evaluate an operation roughly in this order: schema and major version,
digest recomputation, the `operation_id` receipt (same `client_payload_hash` is a
duplicate, a different one is a conflict), the tombstone, then the revision and
projection rules above.

## Fixtures

| File | Purpose |
| --- | --- |
| `valid_worked_example.json` | The water and creatine monohydrate upsert. |
| `valid_delete.json` | A minimal delete at a higher revision. |
| `valid_link_projection_seq2.json` | A link-only change at sequence 2 for revision 2. |
| `valid_proprietary_blend.json` | A blend with undisclosed members and an unknown energy value. |
| `scenario_same_operation_different_content.json` | Reusing an `operation_id` with different content is a `domain_conflict`. |
| `scenario_stale_revision.json` | An older revision delivered after a newer one is `stale_revision`. |
| `invalid_*.json` | Each is rejected for one reason. Schema failures: unknown major or unsupported minor version, a float amount, an unknown property, a delete carrying facts, an unknown value with an amount, an upsert with a sequence other than 1, a link projection with sequence 1, a revision above the signed 64-bit range, a compound without a quantity basis or with another role, a nutrient with the compound role. Failures of the rules beyond the schema: a duplicate component ID, a duplicate link, a link to a component outside the upsert, a link to a compound fact, one sample active on two components, a HealthKit type that does not match the fact's code, an `occurred_at` offset that is not the time zone's offset, one sync identity active on two samples, a nutrient with the blend role, an unknown time zone, the host-local `localtime` zone, an impossible timestamp, a leap second. Failures at parse time: a duplicate object name, a float spelling of `revision`, `projection_sequence` or `sync_version`, an unpaired surrogate. |

A scenario is `{ "description": ..., "steps": [ { "batch": ..., "expect": [ {
"operation_id": ..., "result": ... } ] } ] }`, with one `expect` entry per
operation in the step's batch. Receiver implementations replay the steps in
order against an empty store and compare each result.
