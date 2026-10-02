# Intake evidence query

`list_intake_evidence` (in `health_bridge.queries.intake_evidence`) is a
read-only view of what the receiver can verify about each intake component. It
answers one question per component: is the HealthKit sample the producer
claims for it stored here, and is it the right kind of sample from the right
writer?

It reads the tables behind [`healthrelay.intake-context`](intake-context-v1.md)
and the stored measurement samples. It never writes.

## Call

```python
list_intake_evidence(
    connection,
    *,
    owner_id: str,
    cursor: str | None = None,
    limit: int = 100,        # 1 to 500
    intake_id: str | None = None,
) -> IntakeEvidencePage      # items: list[IntakeEvidenceItem], next_cursor: str | None
```

Every query is scoped to `owner_id`. `limit` outside 1 to 500 and a cursor that
was not issued by this query raise `ValueError` subclasses
(`InvalidIntakeEvidenceLimitError`, `InvalidIntakeEvidenceCursorError`).

## Which facts and links count

- **Effective revision.** The newest accepted revision of each intake. Older
  revisions never appear.
- **Deleted intakes.** An intake with a tombstone is excluded entirely.
- **Current claim.** The newest projection snapshot of the effective revision.
  Only its `active` links count; `superseded` and `deleted` links are audit
  history and never produce evidence.

Each fact (component) of the effective revision yields one item per active
link. A component with no active link yields one `unlinked` item. Items are
ordered by `(intake_id, producer, revision, component position)`, then by
sample UUID when a component has several active links. Two producers may use
the same `intake_id`, so `producer_id` is part of every item.

## Item fields

`intake_id`, `producer_id`, `revision`, `component_id`, `kind`, `code`, `amount`, `unit` and
`value_state` come from the stored fact; `amount` stays the decimal string the
producer sent. `sample_uuid` and `healthkit_type` are the link's claim.
`writer_bundle_id` is the bundle registered for the producer.
`client_record_id` is the stored sample's id when one was found, or the id the
sample will have once it arrives (`pending`); it is `null` when unlinked.

## The join rule

A link is `verified` only by exact joins, never by time, name or amount:

1. **Identity.** A stored sample whose `client_record_id` equals the id the iOS
   exporter gives a quantity sample for that HealthKit UUID:

   ```text
   hk-quantity-<type code with "_" replaced by "-">-<lowercase sample UUID>
   ```

   For example `hydration` and UUID `…` give `hk-quantity-hydration-…`, and
   `dietary_vitamin_b6` gives `hk-quantity-dietary-vitamin-b6-…`. This is the
   exporter's `clientRecordID(for:typeCode:)` in
   `GenericQuantitySyncBatchFactory.swift`; the type code is the component's
   `code`.
2. **Quantity type.** The stored sample's `type_code` equals the component
   `code`, and, when the sample metadata carries `healthkit_identifier`, it
   equals the link's `healthkit_type`.
3. **Writer.** The sample's `healthkit_source_bundle_id` metadata (the bundle
   that wrote the sample in HealthKit) equals the producer's registered
   `writer_bundle_id`. The `bundle_id` of the exporting source is the
   companion app, not the writer, and is not used.

## Link statuses

| Status | Meaning |
| --- | --- |
| `verified` | All three joins hold. |
| `pending` | No stored sample has that id yet. The export may not have arrived. |
| `unlinked` | The component has no active link in its current snapshot. |
| `mismatch` | The claim conflicts with the stored data: a sample for that UUID is stored under another quantity type, its source bundle is not the registered writer (or is missing), another current component also actively claims the same sample, or the sample was deleted at the source and will never arrive. |

## Completeness

`complete` is `false` for every item of a component that has an active link
that is not `verified`. This is how a newer snapshot that replaces a sample
with a UUID that has not been exported yet shows as incomplete: the older
verified sample is superseded and is not reported. A component with no active
link is `complete` because nothing is awaited.

## Pagination

`next_cursor` is opaque; pass it back unchanged. Items are ordered by `(intake_id, producer_id, revision, component position,
sample UUID)` and the cursor names the last item by that key, so a page
boundary never repeats or skips an item, including inside a component whose
links change between requests. The last page has `next_cursor = null`.
