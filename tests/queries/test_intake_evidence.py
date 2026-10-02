"""Tests for the effective intake evidence query."""

import base64
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from health_bridge.queries import intake_evidence
from health_bridge.queries.intake_evidence import (
    IntakeEvidenceItem,
    InvalidIntakeEvidenceCursorError,
    InvalidIntakeEvidenceLimitError,
    exporter_client_record_id,
    list_intake_evidence,
)
from tests.queries.intake_evidence_builder import (
    OWNER,
    PRODUCER,
    WRITER_BUNDLE,
    fact,
    link,
    make_connection,
    put_projection,
    put_revision,
    put_sample,
    put_tombstone,
    register_producer_row,
)

INTAKE_A = "aaaaaaaa-0000-4000-8000-000000000001"
INTAKE_B = "bbbbbbbb-0000-4000-8000-000000000002"


def uid(number: int) -> str:
    return f"{number:08d}-0000-4000-8000-000000000000"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = make_connection(tmp_path / "receiver.sqlite")
    register_producer_row(connection)
    yield connection
    connection.close()


def evidence(
    conn: sqlite3.Connection,
    *,
    owner_id: str = OWNER,
    intake_id: str | None = None,
) -> list[IntakeEvidenceItem]:
    return list_intake_evidence(
        conn, owner_id=owner_id, limit=500, intake_id=intake_id
    ).items


def one(items: list[IntakeEvidenceItem]) -> IntakeEvidenceItem:
    assert len(items) == 1
    return items[0]


def water_intake(
    conn: sqlite3.Connection,
    *,
    sample: str | None,
    intake_id: str = INTAKE_A,
    revision: int = 1,
) -> None:
    _ = put_revision(
        conn,
        intake_id=intake_id,
        revision=revision,
        facts=[fact("water")],
        links=[link("water", sample)] if sample else [],
    )


def test_verified_when_id_type_and_bundle_match(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    water_intake(conn, sample=uid(1))
    item = one(evidence(conn))
    assert item.link_status == "verified"
    assert item.complete is True
    assert item.sample_uuid == uid(1)
    assert item.client_record_id == f"hk-quantity-hydration-{uid(1)}"
    assert item.healthkit_type == "HKQuantityTypeIdentifierDietaryWater"
    assert item.writer_bundle_id == WRITER_BUNDLE
    assert (item.intake_id, item.revision, item.component_id) == (INTAKE_A, 1, "water")
    assert (item.kind, item.code) == ("nutrient", "hydration")
    assert (item.amount, item.unit, item.value_state) == ("500", "mL", "known")


def test_pending_when_sample_is_not_stored_yet(conn: sqlite3.Connection) -> None:
    water_intake(conn, sample=uid(1))
    item = one(evidence(conn))
    assert item.link_status == "pending"
    assert item.complete is False
    assert item.sample_uuid == uid(1)
    assert item.client_record_id == f"hk-quantity-hydration-{uid(1)}"


def test_unlinked_when_component_has_no_active_link(conn: sqlite3.Connection) -> None:
    water_intake(conn, sample=None)
    item = one(evidence(conn))
    assert item.link_status == "unlinked"
    assert item.complete is True
    assert item.sample_uuid is None
    assert item.client_record_id is None
    assert item.healthkit_type is None


def test_superseded_and_deleted_links_are_unlinked(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact("water")],
        links=[link("water", uid(1), disposition="superseded")],
    )
    _ = put_revision(
        conn,
        intake_id=INTAKE_B,
        revision=1,
        facts=[fact("water")],
        links=[link("water", uid(1), disposition="deleted")],
    )
    statuses = {item.intake_id: item.link_status for item in evidence(conn)}
    assert statuses == {INTAKE_A: "unlinked", INTAKE_B: "unlinked"}


def test_compound_component_stays_unlinked(conn: sqlite3.Connection) -> None:
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[
            fact("water"),
            fact(
                "creatine",
                "creatine_monohydrate",
                kind="compound",
                amount="5",
                unit="g",
            ),
        ],
    )
    items = evidence(conn)
    assert [(i.component_id, i.kind, i.link_status) for i in items] == [
        ("water", "nutrient", "unlinked"),
        ("creatine", "compound", "unlinked"),
    ]


def test_type_mismatch_when_sample_is_stored_under_another_type(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1), type_code="dietary_caffeine", unit="mg")
    water_intake(conn, sample=uid(1))
    item = one(evidence(conn))
    assert item.link_status == "mismatch"
    assert item.complete is False
    assert item.client_record_id == f"hk-quantity-dietary-caffeine-{uid(1)}"


def test_type_mismatch_when_healthkit_identifier_disagrees(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    _ = conn.execute(
        "update samples set metadata_json = json_set(metadata_json, ?, ?)",
        ("$.healthkit_identifier", "HKQuantityTypeIdentifierDietaryCaffeine"),
    )
    conn.commit()
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "mismatch"


def test_bundle_mismatch(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1), bundle_id="dev.example.other")
    water_intake(conn, sample=uid(1))
    item = one(evidence(conn))
    assert item.link_status == "mismatch"
    assert item.complete is False
    assert item.client_record_id == f"hk-quantity-hydration-{uid(1)}"


def test_missing_source_bundle_is_a_mismatch(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1), bundle_id=None)
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "mismatch"


def test_exporting_app_bundle_is_not_the_writer(conn: sqlite3.Connection) -> None:
    # The sources row carries the exporting app; only the sample's own source
    # bundle can prove who wrote it.
    _ = put_sample(conn, sample_uuid=uid(1), bundle_id="dev.example.companion")
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "mismatch"


def test_second_source_with_matching_bundle_verifies(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1), bundle_id="dev.example.other")
    _ = put_sample(
        conn,
        sample_uuid=uid(1),
        source_key="apple_health.watch",
        bundle_id=WRITER_BUNDLE,
    )
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "verified"


def test_no_time_or_amount_join(conn: sqlite3.Connection) -> None:
    # Same time, amount and unit, different sample identity.
    _ = put_sample(
        conn,
        sample_uuid=uid(2),
        start_time="2026-10-01T12:00:00Z",
        value=500.0,
        unit="mL",
    )
    water_intake(conn, sample=uid(1))
    item = one(evidence(conn))
    assert item.link_status == "pending"
    assert item.sample_uuid == uid(1)


def test_no_join_on_a_foreign_client_record_id(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1), client_record_id="legacy-water-0001")
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "pending"


def test_uuid_inside_another_record_id_does_not_join(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1), client_record_id=f"workout-{uid(1)}")
    _ = put_sample(
        conn, sample_uuid=uid(3), client_record_id=f"hk-quantity-x-{uid(3)}-1"
    )
    water_intake(conn, sample=uid(1))
    water_intake(conn, sample=uid(3), intake_id=INTAKE_B)
    assert {i.link_status for i in evidence(conn)} == {"pending"}


def test_exporter_record_id_formula() -> None:
    assert (
        exporter_client_record_id("dietary_vitamin_b6", uid(7).upper())
        == f"hk-quantity-dietary-vitamin-b6-{uid(7)}"
    )
    assert (
        exporter_client_record_id("hydration", uid(7))
        == f"hk-quantity-hydration-{uid(7)}"
    )


def test_newest_revision_wins(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    _ = put_sample(conn, sample_uuid=uid(2), type_code="dietary_caffeine", unit="mg")
    water_intake(conn, sample=uid(1), revision=1)
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=2,
        facts=[fact("coffee", "dietary_caffeine", amount="95", unit="mg")],
        links=[link("coffee", uid(2), "dietary_caffeine")],
    )
    item = one(evidence(conn))
    assert (item.revision, item.component_id, item.code) == (
        2,
        "coffee",
        "dietary_caffeine",
    )
    assert item.link_status == "verified"


def test_tombstoned_intake_is_excluded(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    water_intake(conn, sample=uid(1), intake_id=INTAKE_A)
    water_intake(conn, sample=uid(1), intake_id=INTAKE_B)
    put_tombstone(conn, intake_id=INTAKE_A, revision=2)
    assert [i.intake_id for i in evidence(conn)] == [INTAKE_B]
    assert evidence(conn, intake_id=INTAKE_A) == []


def test_tombstone_without_state_row_still_excludes(conn: sqlite3.Connection) -> None:
    water_intake(conn, sample=None)
    put_tombstone(conn, intake_id=INTAKE_A, revision=2)
    _ = conn.execute("update intake_state set deleted = 0")
    conn.commit()
    assert evidence(conn) == []


def test_pending_replacement_marks_component_incomplete(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).complete is True
    # HealthKit replaced the sample; the new UUID has not been exported yet.
    put_projection(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        sequence=2,
        links=[
            link("water", uid(1), disposition="superseded", sync_version=1),
            link("water", uid(9), sync_version=2),
        ],
    )
    item = one(evidence(conn))
    assert item.sample_uuid == uid(9)
    assert item.link_status == "pending"
    assert item.complete is False


def test_replacement_becomes_complete_once_stored(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    water_intake(conn, sample=uid(1))
    put_projection(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        sequence=2,
        links=[link("water", uid(9), sync_version=2)],
    )
    _ = put_sample(conn, sample_uuid=uid(9))
    item = one(evidence(conn))
    assert (item.sample_uuid, item.link_status, item.complete) == (
        uid(9),
        "verified",
        True,
    )


def test_newer_snapshot_without_links_is_unlinked(conn: sqlite3.Connection) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    water_intake(conn, sample=uid(1))
    put_projection(conn, intake_id=INTAKE_A, revision=1, sequence=2, links=[])
    item = one(evidence(conn))
    assert (item.link_status, item.complete) == ("unlinked", True)


def test_incomplete_flag_covers_every_item_of_the_component(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact("water")],
        links=[link("water", uid(1)), link("water", uid(2), sync_version=1)],
    )
    items = evidence(conn)
    assert [(i.sample_uuid, i.link_status, i.complete) for i in items] == [
        (uid(1), "verified", False),
        (uid(2), "pending", False),
    ]


def test_items_are_ordered_by_intake_revision_and_position(
    conn: sqlite3.Connection,
) -> None:
    _ = put_revision(conn, intake_id=INTAKE_B, revision=1, facts=[fact("water")])
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[
            fact("z-last", "dietary_sodium", amount="1", unit="mg"),
            fact("a-first", "dietary_caffeine", amount="2", unit="mg"),
        ],
    )
    assert [(i.intake_id, i.component_id) for i in evidence(conn)] == [
        (INTAKE_A, "z-last"),
        (INTAKE_A, "a-first"),
        (INTAKE_B, "water"),
    ]


def many_components(conn: sqlite3.Connection, intakes: int, per_intake: int) -> int:
    total = 0
    for number in range(intakes):
        intake_id = f"{number:08d}-1111-4111-8111-111111111111"
        facts = [
            fact(f"c{index}", "dietary_caffeine", amount="1", unit="mg")
            for index in range(per_intake)
        ]
        _ = put_revision(conn, intake_id=intake_id, revision=1, facts=facts)
        total += per_intake
    return total


@pytest.mark.parametrize("limit", [1, 2, 3, 5, 7, 100])
def test_pagination_covers_everything_exactly_once(
    conn: sqlite3.Connection, limit: int
) -> None:
    total = many_components(conn, intakes=3, per_intake=3)
    seen: list[tuple[str, str]] = []
    cursor: str | None = None
    pages = 0
    while True:
        page = list_intake_evidence(conn, owner_id=OWNER, cursor=cursor, limit=limit)
        assert len(page.items) <= limit
        seen += [(i.intake_id, i.component_id) for i in page.items]
        pages += 1
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == total
    assert len(set(seen)) == total
    assert seen == sorted(seen, key=lambda pair: (pair[0], seen.index(pair)))
    assert pages == -(-total // limit)


def test_pagination_splits_a_component_with_several_links(
    conn: sqlite3.Connection,
) -> None:
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact("water")],
        links=[link("water", uid(n), sync_version=1) for n in (1, 2, 3)],
    )
    _ = put_revision(conn, intake_id=INTAKE_B, revision=1, facts=[fact("water")])
    first = list_intake_evidence(conn, owner_id=OWNER, limit=2)
    second = list_intake_evidence(
        conn, owner_id=OWNER, cursor=first.next_cursor, limit=2
    )
    assert [i.sample_uuid for i in first.items] == [uid(1), uid(2)]
    assert [i.sample_uuid for i in second.items] == [uid(3), None]
    assert second.next_cursor is None


def test_cursor_is_stable_when_new_data_is_added_after_it(
    conn: sqlite3.Connection,
) -> None:
    _ = many_components(conn, intakes=2, per_intake=2)
    first = list_intake_evidence(conn, owner_id=OWNER, limit=2)
    _ = put_revision(
        conn,
        intake_id="ffffffff-1111-4111-8111-111111111111",
        revision=1,
        facts=[fact("water")],
    )
    second = list_intake_evidence(
        conn, owner_id=OWNER, cursor=first.next_cursor, limit=2
    )
    again = list_intake_evidence(
        conn, owner_id=OWNER, cursor=first.next_cursor, limit=2
    )
    assert second == again
    assert [i.intake_id for i in second.items] == [
        "00000001-1111-4111-8111-111111111111"
    ] * 2


def test_last_page_has_no_cursor_and_empty_store_is_empty(
    conn: sqlite3.Connection,
) -> None:
    empty = list_intake_evidence(conn, owner_id=OWNER)
    assert (empty.items, empty.next_cursor) == ([], None)
    _ = many_components(conn, intakes=1, per_intake=2)
    exact = list_intake_evidence(conn, owner_id=OWNER, limit=2)
    assert len(exact.items) == 2
    assert exact.next_cursor is None


@pytest.mark.parametrize("limit", [0, -1, 501, 10_000])
def test_limit_out_of_range_is_rejected(conn: sqlite3.Connection, limit: int) -> None:
    with pytest.raises(InvalidIntakeEvidenceLimitError):
        _ = list_intake_evidence(conn, owner_id=OWNER, limit=limit)


@pytest.mark.parametrize("limit", [1, 500])
def test_limit_bounds_are_accepted(conn: sqlite3.Connection, limit: int) -> None:
    assert list_intake_evidence(conn, owner_id=OWNER, limit=limit).items == []


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "not-a-cursor",
        "e30",
        "W10",
        "WyJhIiwiYiIsMSwyXQ",
        "WyJhIiwiYiIsLTEsMCwwXQ",
        # The earlier five-element shape: [str, str, int, int, str].
        "WyJhIiwiYiIsMSwwLCJ4Il0",
        # Four elements with a boolean position.
        "WyJhIiwiYiIsdHJ1ZSwieCJd",
        "WyJhIiwiYiIsLTEsIngiXQ",
    ],
)
def test_malformed_cursor_is_rejected(conn: sqlite3.Connection, cursor: str) -> None:
    with pytest.raises(InvalidIntakeEvidenceCursorError):
        _ = list_intake_evidence(conn, owner_id=OWNER, cursor=cursor)


def test_cursor_is_opaque_urlsafe_text(conn: sqlite3.Connection) -> None:
    _ = many_components(conn, intakes=1, per_intake=3)
    cursor = list_intake_evidence(conn, owner_id=OWNER, limit=1).next_cursor
    assert cursor is not None
    assert all(c.isalnum() or c in "-_" for c in cursor)
    assert INTAKE_A not in cursor


def test_owner_scoping(conn: sqlite3.Connection) -> None:
    register_producer_row(conn, owner_id="owner-2")
    _ = put_sample(conn, sample_uuid=uid(1))
    water_intake(conn, sample=uid(1))
    _ = put_revision(
        conn,
        intake_id=INTAKE_B,
        revision=1,
        facts=[fact("water")],
        owner_id="owner-2",
    )
    assert [i.intake_id for i in evidence(conn)] == [INTAKE_A]
    assert [i.intake_id for i in evidence(conn, owner_id="owner-2")] == [INTAKE_B]
    assert evidence(conn, owner_id="owner-3") == []
    assert evidence(conn, intake_id=INTAKE_B) == []


def test_owner_tombstone_does_not_leak_to_another_owner(
    conn: sqlite3.Connection,
) -> None:
    register_producer_row(conn, owner_id="owner-2")
    water_intake(conn, sample=None)
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact("water")],
        owner_id="owner-2",
    )
    put_tombstone(conn, intake_id=INTAKE_A, revision=2, owner_id="owner-2")
    assert [i.intake_id for i in evidence(conn)] == [INTAKE_A]
    assert evidence(conn, owner_id="owner-2") == []


def test_intake_id_filter(conn: sqlite3.Connection) -> None:
    water_intake(conn, sample=None, intake_id=INTAKE_A)
    water_intake(conn, sample=None, intake_id=INTAKE_B)
    assert [i.intake_id for i in evidence(conn, intake_id=INTAKE_B)] == [INTAKE_B]
    assert evidence(conn, intake_id="cccccccc-0000-4000-8000-000000000003") == []


def test_writer_bundle_comes_from_the_registered_producer(
    conn: sqlite3.Connection,
) -> None:
    register_producer_row(
        conn, producer_id="other-app", writer_bundle_id="dev.example.other"
    )
    _ = put_sample(conn, sample_uuid=uid(1), bundle_id="dev.example.other")
    _ = put_revision(
        conn,
        intake_id=INTAKE_B,
        revision=1,
        facts=[fact("water")],
        links=[link("water", uid(1))],
        producer_id="other-app",
    )
    item = one(evidence(conn))
    assert (item.writer_bundle_id, item.link_status) == (
        "dev.example.other",
        "verified",
    )
    assert PRODUCER != "other-app"


def test_read_only_connection_is_enough(tmp_path: Path) -> None:
    path = tmp_path / "ro.sqlite"
    writer = make_connection(path)
    register_producer_row(writer)
    _ = put_sample(writer, sample_uuid=uid(1))
    water_intake(writer, sample=uid(1))
    writer.close()
    reader = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert one(evidence(reader)).link_status == "verified"
    finally:
        reader.close()


def test_module_documents_the_formula() -> None:
    doc = intake_evidence.exporter_client_record_id.__doc__ or ""
    assert "hk-quantity-" in doc


def test_exact_record_id_under_the_wrong_stored_type_is_a_mismatch(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(
        conn,
        sample_uuid=uid(1),
        type_code="dietary_caffeine",
        client_record_id=exporter_client_record_id("hydration", uid(1)),
    )
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "mismatch"


def test_items_name_their_producer(conn: sqlite3.Connection) -> None:
    register_producer_row(
        conn, producer_id="other-app", writer_bundle_id="dev.example.other"
    )
    _ = put_sample(conn, sample_uuid=uid(1))
    _ = put_sample(conn, sample_uuid=uid(2), bundle_id="dev.example.other")
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact("water")],
        links=[link("water", uid(1))],
    )
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact("water")],
        links=[link("water", uid(2))],
        producer_id="other-app",
    )
    assert [(i.producer_id, i.link_status) for i in evidence(conn)] == [
        ("nutrition-app", "verified"),
        ("other-app", "verified"),
    ]


def test_sample_claimed_by_two_components_is_not_verified_for_either(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    water_intake(conn, sample=uid(1), intake_id=INTAKE_A)
    water_intake(conn, sample=uid(1), intake_id=INTAKE_B)
    items = evidence(conn)
    assert [(i.link_status, i.complete) for i in items] == [
        ("mismatch", False),
        ("mismatch", False),
    ]


def test_conflict_ends_when_the_other_claim_is_retired(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    water_intake(conn, sample=uid(1), intake_id=INTAKE_A)
    water_intake(conn, sample=uid(1), intake_id=INTAKE_B)
    put_projection(conn, intake_id=INTAKE_B, revision=1, sequence=2, links=[])
    assert [(i.intake_id, i.link_status) for i in evidence(conn)] == [
        (INTAKE_A, "verified"),
        (INTAKE_B, "unlinked"),
    ]


def test_sample_deleted_at_the_source_is_not_pending(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(5))
    _ = conn.execute(
        """insert into deleted_records
        (source_id, record_family, client_record_id, deleted_at)
        values ((select min(source_id) from sources), 'sample', ?, ?)""",
        (exporter_client_record_id("hydration", uid(1)), "2026-10-01T13:00:00Z"),
    )
    conn.commit()
    water_intake(conn, sample=uid(1))
    item = one(evidence(conn))
    assert (item.link_status, item.complete) == ("mismatch", False)


def test_cursor_inside_a_component_survives_a_link_inserted_before_it(
    conn: sqlite3.Connection,
) -> None:
    put_revision_links = [link("water", uid(n)) for n in (1, 3)]
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact("water")],
        links=put_revision_links,
    )
    first = list_intake_evidence(conn, owner_id=OWNER, limit=1)
    assert [i.sample_uuid for i in first.items] == [uid(1)]
    put_projection(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        sequence=2,
        links=[link("water", uid(n)) for n in (0, 1, 3)],
    )
    second = list_intake_evidence(
        conn, owner_id=OWNER, cursor=first.next_cursor, limit=5
    )
    assert [i.sample_uuid for i in second.items] == [uid(3)]


def test_conflict_is_visible_before_the_sample_is_stored(
    conn: sqlite3.Connection,
) -> None:
    water_intake(conn, sample=uid(1), intake_id=INTAKE_A)
    water_intake(conn, sample=uid(1), intake_id=INTAKE_B)
    assert [i.link_status for i in evidence(conn)] == ["mismatch", "mismatch"]


def test_deletion_overrides_a_surviving_copy_from_another_source(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    _ = put_sample(conn, sample_uuid=uid(1), source_key="apple_health.watch")
    _ = conn.execute(
        """insert into deleted_records
        (source_id, record_family, client_record_id, deleted_at)
        values ((select min(source_id) from sources), 'sample', ?, ?)""",
        (exporter_client_record_id("hydration", uid(1)), "2026-10-01T13:00:00Z"),
    )
    conn.commit()
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "mismatch"


def test_sample_lookup_uses_the_unique_index_not_a_table_scan(
    conn: sqlite3.Connection,
) -> None:
    pairs = json.dumps([["hydration", exporter_client_record_id("hydration", uid(1))]])
    rows = cast(
        "list[tuple[int, int, int, str]]",
        conn.execute(
            "explain query plan " + intake_evidence.SAMPLES_SQL, {"pairs": pairs}
        ).fetchall(),
    )
    plan = " ".join(row[3] for row in rows)
    assert "SCAN samples" not in plan
    assert "SEARCH samples" in plan


def component_ids(items: list[IntakeEvidenceItem]) -> list[str]:
    return [i.component_id for i in items]


def test_cursor_does_not_advance_with_the_revision(
    conn: sqlite3.Connection,
) -> None:
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact(f"c{n}") for n in range(3)],
    )
    first = list_intake_evidence(conn, owner_id=OWNER, limit=2)
    assert component_ids(first.items) == ["c0", "c1"]
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=2,
        facts=[fact(f"c{n}") for n in range(4)],
    )
    second = list_intake_evidence(
        conn, owner_id=OWNER, cursor=first.next_cursor, limit=5
    )
    assert component_ids(second.items) == ["c2", "c3"]
    assert {i.revision for i in second.items} == {2}


def test_cursor_survives_a_revision_that_drops_a_component(
    conn: sqlite3.Connection,
) -> None:
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=1,
        facts=[fact(f"c{n}") for n in range(3)],
    )
    first = list_intake_evidence(conn, owner_id=OWNER, limit=2)
    _ = put_revision(
        conn,
        intake_id=INTAKE_A,
        revision=2,
        facts=[fact("c0"), fact("c1"), fact("c3")],
    )
    second = list_intake_evidence(
        conn, owner_id=OWNER, cursor=first.next_cursor, limit=5
    )
    assert component_ids(second.items) == ["c3"]


def test_cursor_has_four_elements(conn: sqlite3.Connection) -> None:
    _ = many_components(conn, intakes=1, per_intake=3)
    cursor = list_intake_evidence(conn, owner_id=OWNER, limit=1).next_cursor
    assert cursor is not None
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    assert len(cast("list[object]", json.loads(raw))) == 4


def test_sample_stored_under_a_non_dietary_type_is_a_mismatch(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1), type_code="heart_rate", unit="count/min")
    water_intake(conn, sample=uid(1))
    item = one(evidence(conn))
    assert item.link_status == "mismatch"
    assert item.client_record_id == f"hk-quantity-heart-rate-{uid(1)}"


def test_tombstone_under_another_type_id_is_a_mismatch(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(5), type_code="dietary_caffeine", unit="mg")
    add_tombstone(conn, exporter_client_record_id("dietary_caffeine", uid(1)))
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "mismatch"


def test_exact_row_plus_copy_under_another_type_is_a_mismatch(
    conn: sqlite3.Connection,
) -> None:
    _ = put_sample(conn, sample_uuid=uid(1))
    _ = put_sample(
        conn,
        sample_uuid=uid(1),
        type_code="dietary_caffeine",
        unit="mg",
        source_key="apple_health.watch",
    )
    water_intake(conn, sample=uid(1))
    assert one(evidence(conn)).link_status == "mismatch"


def add_tombstone(conn: sqlite3.Connection, client_record_id: str) -> None:
    _ = conn.execute(
        """insert into deleted_records
        (source_id, record_family, client_record_id, deleted_at)
        values ((select min(source_id) from sources), 'sample', ?, ?)""",
        (client_record_id, "2026-10-01T13:00:00Z"),
    )
    conn.commit()


def traced(
    conn: sqlite3.Connection, intake_links: int, tmp_intake: str, offset: int = 0
) -> tuple[list[str], list[IntakeEvidenceItem]]:
    _ = put_revision(
        conn,
        intake_id=tmp_intake,
        revision=1,
        facts=[fact("water")],
        links=[link("water", uid(n + 1 + offset)) for n in range(intake_links)],
    )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        page = list_intake_evidence(conn, owner_id=OWNER, limit=1, intake_id=tmp_intake)
    finally:
        conn.set_trace_callback(None)
    return statements, page.items


def test_page_limit_bounds_the_work_regardless_of_link_count(
    conn: sqlite3.Connection,
) -> None:
    small, small_items = traced(conn, 6, INTAKE_A)
    large, large_items = traced(conn, 60, INTAKE_B, offset=100)
    for items, first in ((small_items, uid(1)), (large_items, uid(101))):
        assert [i.sample_uuid for i in items] == [first]
        assert items[0].link_status == "pending"
        assert items[0].complete is False
    marker = "from candidate"
    assert sum(marker in s for s in small) <= 2
    assert sum(marker in s for s in large) <= 2
    assert len(large) == len(small)


def test_completeness_matches_per_link_statuses(conn: sqlite3.Connection) -> None:
    other = "dev.example.other"
    _ = put_sample(conn, sample_uuid=uid(1))  # verified
    # uid(2) pending: no sample.
    _ = put_sample(conn, sample_uuid=uid(3), type_code="dietary_caffeine", unit="mg")
    _ = put_sample(conn, sample_uuid=uid(4), bundle_id=other)
    _ = put_sample(conn, sample_uuid=uid(5))
    _ = conn.execute(
        """update samples set metadata_json = json_set(metadata_json, ?, ?)
        where client_record_id = ?""",
        (
            "$.healthkit_identifier",
            "HKQuantityTypeIdentifierDietaryCaffeine",
            exporter_client_record_id("hydration", uid(5)),
        ),
    )
    _ = put_sample(conn, sample_uuid=uid(6))  # contested
    _ = put_sample(conn, sample_uuid=uid(7))  # deleted
    add_tombstone(conn, exporter_client_record_id("hydration", uid(7)))
    _ = put_sample(conn, sample_uuid=uid(8), type_code="heart_rate", unit="count/min")
    add_tombstone(conn, exporter_client_record_id("dietary_sodium", uid(9)))
    _ = put_sample(conn, sample_uuid=uid(10))
    _ = put_sample(
        conn,
        sample_uuid=uid(10),
        type_code="dietary_caffeine",
        unit="mg",
        source_key="apple_health.watch",
    )
    _ = put_sample(conn, sample_uuid=uid(11), bundle_id=other)
    _ = put_sample(
        conn,
        sample_uuid=uid(11),
        source_key="apple_health.watch",
        bundle_id=WRITER_BUNDLE,
    )
    _ = put_sample(conn, sample_uuid=uid(12))
    _ = put_sample(conn, sample_uuid=uid(14), bundle_id=other)
    _ = put_sample(conn, sample_uuid=uid(15))
    _ = put_sample(conn, sample_uuid=uid(16))
    scenarios = {
        "00000001-0000-4000-8000-000000000000": [uid(1)],
        "00000002-0000-4000-8000-000000000000": [uid(2)],
        "00000003-0000-4000-8000-000000000000": [uid(3)],
        "00000004-0000-4000-8000-000000000000": [uid(4)],
        "00000005-0000-4000-8000-000000000000": [uid(5)],
        "00000006-0000-4000-8000-000000000000": [uid(6)],
        "00000007-0000-4000-8000-000000000000": [uid(7)],
        "00000008-0000-4000-8000-000000000000": [uid(8)],
        "00000009-0000-4000-8000-000000000000": [uid(9)],
        "0000000a-0000-4000-8000-000000000000": [uid(10)],
        "0000000b-0000-4000-8000-000000000000": [uid(11)],
        "0000000c-0000-4000-8000-000000000000": [uid(12), uid(13), uid(14)],
        "0000000d-0000-4000-8000-000000000000": [uid(15), uid(16)],
        "0000000e-0000-4000-8000-000000000000": [],
    }
    for intake_id, samples in scenarios.items():
        _ = put_revision(
            conn,
            intake_id=intake_id,
            revision=1,
            facts=[fact("water")],
            links=[link("water", sample) for sample in samples],
        )
    water_intake(conn, sample=uid(6), intake_id=INTAKE_B)
    items = evidence(conn)
    by_component: dict[tuple[str, str], list[IntakeEvidenceItem]] = {}
    for item in items:
        by_component.setdefault((item.intake_id, item.component_id), []).append(item)
    assert len(by_component) == len(scenarios) + 1
    for group in by_component.values():
        expected = all(i.link_status in {"verified", "unlinked"} for i in group)
        assert {i.complete for i in group} == {expected}
    statuses = {
        i.intake_id: i.link_status
        for i in items
        if len(scenarios.get(i.intake_id, [])) == 1
    }
    assert statuses["00000001-0000-4000-8000-000000000000"] == "verified"
    assert statuses["0000000b-0000-4000-8000-000000000000"] == "verified"
    assert statuses["00000002-0000-4000-8000-000000000000"] == "pending"
    assert statuses["0000000a-0000-4000-8000-000000000000"] == "mismatch"


def test_completeness_aggregate_uses_the_unique_indexes(
    conn: sqlite3.Connection,
) -> None:
    params = {
        "revision_row_id": 1,
        "component_id": "water",
        "code": "hydration",
        "id_prefix": "hk-quantity-hydration-",
        "writer_bundle_id": WRITER_BUNDLE,
        "owner_id": OWNER,
        "producer_id": PRODUCER,
        "intake_id": INTAKE_A,
    }
    rows = cast(
        "list[tuple[int, int, int, str]]",
        conn.execute(
            "explain query plan " + intake_evidence.UNVERIFIED_LINK_COUNT_SQL,
            params,
        ).fetchall(),
    )
    plan = " ".join(row[3] for row in rows)
    for scan in (
        "SCAN exact",
        "SCAN other",
        "SCAN deleted",
        "SCAN samples",
        "SCAN deleted_records",
    ):
        assert scan not in plan
