"""Tests for the effective intake evidence query."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

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
    ["", "not-a-cursor", "e30", "W10", "WyJhIiwiYiIsMSwyXQ", "WyJhIiwiYiIsLTEsMCwwXQ"],
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
