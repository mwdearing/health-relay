"""Self-tests for the synthetic intake-evidence database builder."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Final, cast

import pytest

from health_bridge.storage.intake_context import (
    read_intake_state,
    read_revision,
    read_tombstone,
)
from tests.queries.intake_evidence_builder import (
    OWNER,
    PRODUCER,
    exporter_client_record_id,
    fact,
    hk_identifier,
    link,
    make_connection,
    put_projection,
    put_revision,
    put_sample,
    put_tombstone,
    register_producer_row,
)

INTAKE_ID = "11111111-1111-4111-8111-111111111111"
SAMPLE_UUID = "33333333-3333-4333-8333-333333333333"
SAMPLE_QUERY: Final = """
select s.client_record_id, s.metadata_json, src.bundle_id
from samples s join sources src using (source_id) where s.sample_id = ?
"""


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(make_connection(tmp_path / "builder.sqlite")) as connection:
        register_producer_row(connection)
        yield connection


def test_identifier_helpers() -> None:
    assert hk_identifier("hydration") == "HKQuantityTypeIdentifierDietaryWater"
    assert (
        hk_identifier("dietary_vitamin_b6")
        == "HKQuantityTypeIdentifierDietaryVitaminB6"
    )
    assert (
        exporter_client_record_id("dietary_caffeine", "ABC")
        == "hk-quantity-dietary-caffeine-abc"
    )


def test_revision_is_readable_with_links_and_state(conn: sqlite3.Connection) -> None:
    links = [link("water", SAMPLE_UUID)]
    row_id = put_revision(
        conn, intake_id=INTAKE_ID, revision=1, facts=[fact("water")], links=links
    )

    stored = read_revision(
        conn, owner_id=OWNER, producer_id=PRODUCER, intake_id=INTAKE_ID, revision=1
    )
    state = read_intake_state(
        conn, owner_id=OWNER, producer_id=PRODUCER, intake_id=INTAKE_ID
    )

    assert stored is not None
    assert stored.revision_row_id == row_id
    assert stored.record.links == tuple(links)
    assert state is not None
    assert (state.current_revision, state.current_projection_sequence) == (1, 1)
    assert not state.deleted


def test_state_never_moves_backwards(conn: sqlite3.Connection) -> None:
    _ = put_revision(conn, intake_id=INTAKE_ID, revision=2, facts=[fact("water")])
    _ = put_revision(conn, intake_id=INTAKE_ID, revision=1, facts=[fact("water")])

    state = read_intake_state(
        conn, owner_id=OWNER, producer_id=PRODUCER, intake_id=INTAKE_ID
    )
    assert state is not None
    assert state.current_revision == 2


def test_compound_fact_has_basis_and_role() -> None:
    compound = fact("caf", "dietary_caffeine", kind="compound", unit="mg")

    assert compound.quantity_basis == "compound_mass"
    assert compound.aggregation_role == "compound_measurement"


def test_projection_adds_snapshot(conn: sqlite3.Connection) -> None:
    _ = put_revision(
        conn,
        intake_id=INTAKE_ID,
        revision=1,
        facts=[fact("water")],
        links=[link("water", SAMPLE_UUID)],
    )
    put_projection(
        conn,
        intake_id=INTAKE_ID,
        revision=1,
        sequence=2,
        links=[link("water", SAMPLE_UUID, disposition="deleted")],
    )

    stored = read_revision(
        conn, owner_id=OWNER, producer_id=PRODUCER, intake_id=INTAKE_ID, revision=1
    )
    state = read_intake_state(
        conn, owner_id=OWNER, producer_id=PRODUCER, intake_id=INTAKE_ID
    )
    assert stored is not None
    assert [p.projection_sequence for p in stored.projections] == [1, 2]
    assert state is not None
    assert state.current_projection_sequence == 2


def test_tombstone_marks_state_deleted(conn: sqlite3.Connection) -> None:
    _ = put_revision(conn, intake_id=INTAKE_ID, revision=1, facts=[fact("water")])
    put_tombstone(conn, intake_id=INTAKE_ID, revision=2)

    tombstone = read_tombstone(
        conn, owner_id=OWNER, producer_id=PRODUCER, intake_id=INTAKE_ID
    )
    state = read_intake_state(
        conn, owner_id=OWNER, producer_id=PRODUCER, intake_id=INTAKE_ID
    )
    assert tombstone is not None
    assert tombstone.revision == 2
    assert state is not None
    assert state.deleted
    assert state.current_revision == 2


def test_sample_stores_exporter_source_and_writer_metadata(
    conn: sqlite3.Connection,
) -> None:
    sample_id = put_sample(conn, sample_uuid=SAMPLE_UUID)

    row = cast(
        "tuple[str, str, str]",
        conn.execute(SAMPLE_QUERY, (sample_id,)).fetchone(),
    )
    metadata = cast("dict[str, str]", json.loads(row[1]))
    assert row[0] == f"hk-quantity-hydration-{SAMPLE_UUID}"
    assert row[2] == "dev.example.companion"
    assert metadata["healthkit_source_bundle_id"] == "dev.example.nutrition"
    assert metadata["healthkit_identifier"] == "HKQuantityTypeIdentifierDietaryWater"


def test_unknown_bundle_sample_has_no_bundle_key(conn: sqlite3.Connection) -> None:
    sample_id = put_sample(conn, sample_uuid=SAMPLE_UUID, bundle_id=None)

    row = cast(
        "tuple[str]",
        conn.execute(
            "select metadata_json from samples where sample_id = ?", (sample_id,)
        ).fetchone(),
    )
    assert "healthkit_source_bundle_id" not in cast(
        "dict[str, str]", json.loads(row[0])
    )
