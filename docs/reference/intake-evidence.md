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
ordered by `(intake_id, producer, component position)`, then by
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
| `mismatch` | The claim conflicts with the stored data: a sample for that UUID is stored under any other quantity type (a copy under another type at another source counts too), its source bundle is not the registered writer (or is missing), another current component also actively claims the same sample, or the sample was deleted at a source under any candidate id (even if another source still holds a copy) and will never arrive. A conflict is reported even before the sample is stored. |

## Completeness

`complete` is `false` for every item of a component that has an active link
that is not `verified`. This is how a newer snapshot that replaces a sample
with a UUID that has not been exported yet shows as incomplete: the older
verified sample is superseded and is not reported. A component with no active
link is `complete` because nothing is awaited.

Resolution is bounded by the page: only the items on the page are resolved, and
`complete` comes from one short-circuiting probe per component on the page (it stops at the first link that is not verified), so cost does
not grow with the number of links a component holds.

## Pagination

`next_cursor` is opaque; pass it back unchanged. It is a position, not a
capability: every query is still scoped to `owner_id`, so a cursor from another
query can only change where the listing starts. Items are ordered by
`(intake_id, producer_id, component position, sample UUID)` and the cursor names
the last item by that key plus its component id, so a page boundary never repeats or skips an item,
including inside a component whose links change between requests. The last page
has `next_cursor = null`.

The cursor carries the component id but not a revision. If an intake's effective revision changes
between pages, the next page continues from the position after the cursor in the
new revision; components at or before that position are not emitted again. At the cursor's own position, the cursor's sample UUID applies only if the component there is the same one; if the revision put a different component at that position, the whole position is skipped. A
caller that needs one consistent revision lists that intake again with
`intake_id`. Cursors issued by earlier builds are rejected.

## MCP tool `get_intake_evidence_v1`

The same view is available to MCP clients as a read-only tool.

| Argument | Type | Default | Meaning |
| --- | --- | --- | --- |
| `owner_id` | string | the single registered owner | Intake owner to read. |
| `intake_id` | string | none | Limit the listing to one intake. |
| `cursor` | string | none | `next_cursor` from the previous page. |
| `limit` | integer | 100 | Items per page, 1 to 500. |

When `owner_id` is omitted, the tool uses the only owner with a registered
intake producer. With no registered owner it returns the error "no intake owner
registered"; with several it returns an error asking for `owner_id`.

A malformed cursor, a `limit` outside 1 to 500 (or not an integer), or an
unknown argument returns a JSON-RPC `-32602` error naming the tool and the
problem; the server keeps running. The result is the page as JSON: `items[]`
(each with `producer_id`, `link_status`, `complete` and the fields above) and
`next_cursor`. It carries metadata and identifiers only, no sample values.
