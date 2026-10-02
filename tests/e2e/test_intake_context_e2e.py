"""End-to-end synthetic suite: HTTP routes, evidence query and MCP tool.

A real receiver serves both delivery routes. Samples travel through
/v1/batches, intake batches through /v1/intake-context/batches, and the
evidence is read back through the query function and the MCP tool.
"""

import copy
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from threading import Thread
from typing import cast, final
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from health_bridge.mcp.server import dispatch_request
from health_bridge.mcp.types import JsonObject
from health_bridge.queries.intake_evidence import (
    IntakeEvidenceItem,
    list_intake_evidence,
)
from health_bridge.receiver.intake_tokens import create_intake_token
from health_bridge.receiver.server import build_receiver_server, server_port
from health_bridge.receiver.tokens import create_receiver_token
from health_bridge.storage import initialize_database
from tests.intake_context.reference import seal

OWNER = "owner-e2e"
PRODUCER = "nutrition-app"
WRITER_BUNDLE = "com.example.healthrelay.nutrition"
EXPORTER_BUNDLE = "com.example.synthetic.healthbridge"
SOURCE_KEY = "synthetic.phone.alpha"
INSTALLATION = "507b8fbb-78d3-450c-a88f-487e90df92e6"
TOOL = "get_intake_evidence_v1"

INTAKE_1 = "e6677963-418c-4027-b563-551d8a531eed"
INTAKE_2 = "f7788a74-529d-4138-a674-662e9b642ffe"
WATER_1 = "2c932bd1-c46d-4e38-b481-e0d842fdd429"
CAFFEINE_1 = "3d043ce2-d57e-4f49-b592-f31e953e3540"
WATER_2 = "4e154df3-e68f-4a5a-86a3-042fa64f4651"

# (type code, HealthKit identifier, unit, sample uuid, value) per sample.
SAMPLES = (
    ("hydration", "HKQuantityTypeIdentifierDietaryWater", "mL", WATER_1, 500),
    (
        "dietary_caffeine",
        "HKQuantityTypeIdentifierDietaryCaffeine",
        "mg",
        CAFFEINE_1,
        95,
    ),
    ("hydration", "HKQuantityTypeIdentifierDietaryWater", "mL", WATER_2, 250),
)
EXPECTED_TOTALS = {"hydration": Decimal(750), "dietary_caffeine": Decimal(95)}
EXPECTED_AMOUNTS = {
    (INTAKE_1, "water"): ("500", "mL"),
    (INTAKE_1, "caffeine"): ("95", "mg"),
    (INTAKE_2, "water"): ("250", "mL"),
}


@contextmanager
def served(db: Path) -> Generator[str]:
    server = build_receiver_server(db, "127.0.0.1", 0, intake_context_enabled=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server_port(server)}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@final
class Stack:
    """A running receiver with one receiver token and one intake token."""

    def __init__(self, base: str, db: Path) -> None:
        self.base = base
        self.db = db
        self.receiver_token = create_receiver_token(db, label="e2e-batch").token
        self.intake_token = create_intake_token(
            db, owner_id=OWNER, producer_id=PRODUCER, label="e2e-intake"
        ).token

    def post(self, path: str, token: str, body: object) -> tuple[int, object]:
        request = Request(
            self.base + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:  # pyright: ignore[reportAny]
                return response.status, json.loads(response.read())  # pyright: ignore[reportAny]
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def send_samples(self) -> None:
        status, body = self.post("/v1/batches", self.receiver_token, sample_batch())
        assert status == 202, body

    def send_intake(self, batch: dict[str, object]) -> list[dict[str, object]]:
        status, body = self.post("/v1/intake-context/batches", self.intake_token, batch)
        assert status == 200, body
        return cast(
            "list[dict[str, object]]", cast("dict[str, object]", body)["results"]
        )


@contextmanager
def stack(tmp_path: Path) -> Generator[Stack]:
    db = tmp_path / "e2e.sqlite"
    initialize_database(db)
    with served(db) as base:
        yield Stack(base, db)


def sample_batch() -> dict[str, object]:
    types = {
        "hydration": ("Hydration", "mL"),
        "dietary_caffeine": ("Dietary Caffeine", "mg"),
    }
    return {
        "schema_id": "health_bridge.batch.v1",
        "schema_version": "1.0.0",
        "generated_at": "2026-09-30T18:00:00Z",
        "export_window": {
            "start_time": "2026-09-30T00:00:00Z",
            "end_time": "2026-10-01T00:00:00Z",
        },
        "sources": [
            {
                "source_key": SOURCE_KEY,
                "name": "Synthetic Phone Alpha",
                "kind": "phone",
                "bundle_id": EXPORTER_BUNDLE,
                "device_model": "SyntheticPhone1,1",
            }
        ],
        "health_types": [
            {
                "type_code": code,
                "display_name": name,
                "category": "other",
                "default_unit": unit,
                "sensitivity": "moderate",
                "aliases": [],
            }
            for code, (name, unit) in types.items()
        ],
        "samples": [
            {
                "client_record_id": (
                    f"hk-quantity-{code.replace('_', '-')}-{uuid.lower()}"
                ),
                "source_key": SOURCE_KEY,
                "type_code": code,
                "start_time": "2026-09-30T17:30:00Z",
                "end_time": "2026-09-30T17:30:00Z",
                "value": value,
                "unit": unit,
                "metadata": {
                    "healthkit_identifier": identifier,
                    "healthkit_source_bundle_id": WRITER_BUNDLE,
                    "healthkit_source_name": "Synthetic Nutrition App",
                    "sample_kind": "raw_quantity",
                },
            }
            for code, identifier, unit, uuid, value in SAMPLES
        ],
        "workouts": [],
        "sleep_sessions": [],
        "deleted_records": [],
        "sync": {
            "sync_window": {
                "start_time": "2026-09-30T00:00:00Z",
                "end_time": "2026-10-01T00:00:00Z",
            },
            "cursors": [
                {
                    "source_key": SOURCE_KEY,
                    "cursor_kind": "anchored_object_query",
                    "cursor_value": "synthetic-cursor-e2e-0001",
                }
            ],
        },
    }


def _fact(component: str, code: str, amount: str, unit: str) -> dict[str, object]:
    return {
        "component_id": component,
        "kind": "nutrient",
        "code": code,
        "amount": amount,
        "unit": unit,
        "value_state": "known",
        "aggregation_role": "context_only",
        "provenance": "user_confirmed",
    }


def _link(intake: str, component: str, identifier: str, uuid: str) -> dict[str, object]:
    return {
        "component_id": component,
        "healthkit_sample_uuid": uuid,
        "healthkit_type": identifier,
        "sync_identifier": f"intake:{intake}:{component}",
        "sync_version": 1,
        "disposition": "active",
    }


def _upsert(
    intake: str,
    name: str,
    facts: list[dict[str, object]],
    links: list[dict[str, object]],
    batch_number: int,
) -> dict[str, object]:
    return {
        "operation_id": f"00000000-0000-4000-8000-{batch_number:012d}",
        "operation": "upsert",
        "intake_id": intake,
        "revision": 1,
        "projection_sequence": 1,
        "occurred_at": "2026-09-30T12:30:00-05:00",
        "time_zone": "America/Chicago",
        "recorded_at": "2026-09-30T17:31:02Z",
        "category": "beverage",
        "display_name": name,
        "serving": {"amount": "1", "unit": "serving"},
        "facts": facts,
        "healthkit_links": links,
        "nutrition_completeness": "complete",
        "domain_facts_hash": "",
        "projection_hash": "",
        "client_payload_hash": "",
    }


def _envelope(batch_id: str, operations: list[dict[str, object]]) -> dict[str, object]:
    return seal(
        {
            "schema": "healthrelay.intake-context",
            "schema_version": "1.0",
            "batch_id": batch_id,
            "producer_id": PRODUCER,
            "writer_bundle_id": WRITER_BUNDLE,
            "installation_id": INSTALLATION,
            "operations": operations,
        }
    )


def intake_batch() -> dict[str, object]:
    water, caffeine, water_two = SAMPLES
    return _envelope(
        "0bda35fc-3eab-47ce-9e31-c684343dd8d7",
        [
            _upsert(
                INTAKE_1,
                "Coffee with water",
                [
                    _fact("water", "hydration", "500", "mL"),
                    _fact("caffeine", "dietary_caffeine", "95", "mg"),
                ],
                [
                    _link(INTAKE_1, "water", water[1], water[3]),
                    _link(INTAKE_1, "caffeine", caffeine[1], caffeine[3]),
                ],
                1,
            ),
            _upsert(
                INTAKE_2,
                "Water",
                [_fact("water", "hydration", "250", "mL")],
                [_link(INTAKE_2, "water", water_two[1], water_two[3])],
                2,
            ),
        ],
    )


def delete_batch(intake: str) -> dict[str, object]:
    operation: dict[str, object] = {
        "operation_id": "00000000-0000-4000-8000-0000000000d1",
        "operation": "delete",
        "intake_id": intake,
        "revision": 2,
        "deleted_at": "2026-09-30T18:05:00Z",
        "domain_facts_hash": "",
        "client_payload_hash": "",
    }
    return _envelope("5f0e8a2c-6d41-4b0e-9c3a-7a1d2b9e4f10", [operation])


def totals(db: Path) -> dict[str, Decimal]:
    with sqlite3.connect(db) as connection:
        rows = cast(
            "list[tuple[str, float]]",
            connection.execute(
                "select type_code, sum(value) from samples group by type_code"
            ).fetchall(),
        )
    connection.close()
    return {code: Decimal(str(total)) for code, total in rows}


def sample_count(db: Path) -> int:
    with sqlite3.connect(db) as connection:
        count = cast(
            "int", connection.execute("select count(*) from samples").fetchone()[0]
        )
    connection.close()
    return count


def evidence(db: Path) -> list[IntakeEvidenceItem]:
    with sqlite3.connect(db) as connection:
        page = list_intake_evidence(connection, owner_id=OWNER, limit=500)
    connection.close()
    return page.items


def tool_page(db: Path, arguments: JsonObject) -> dict[str, object]:
    response = cast(
        "dict[str, object]",
        dispatch_request(
            db,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": TOOL, "arguments": arguments},
            },
        ),
    )
    result = cast("dict[str, list[dict[str, str]]]", response["result"])
    return cast("dict[str, object]", json.loads(result["content"][0]["text"]))


def assert_complete_evidence(db: Path) -> None:
    items = evidence(db)
    assert len(items) == len(EXPECTED_AMOUNTS)
    assert {(i.intake_id, i.component_id): (i.amount, i.unit) for i in items} == (
        EXPECTED_AMOUNTS
    )
    assert all(i.link_status == "verified" and i.complete for i in items)
    assert all(i.producer_id == PRODUCER for i in items)
    assert all(i.writer_bundle_id == WRITER_BUNDLE for i in items)
    assert {i.sample_uuid for i in items} == {s[3] for s in SAMPLES}
    tool_items = cast("list[dict[str, object]]", tool_page(db, {"limit": 50})["items"])
    assert sorted(cast("str", t["link_status"]) for t in tool_items) == ["verified"] * 3


def test_samples_first_then_intake(tmp_path: Path) -> None:
    with stack(tmp_path) as live:
        live.send_samples()
        assert totals(live.db) == EXPECTED_TOTALS
        results = live.send_intake(intake_batch())
        assert [r["result"] for r in results] == ["accepted", "accepted"]
        assert totals(live.db) == EXPECTED_TOTALS
        assert sample_count(live.db) == len(SAMPLES)
        assert_complete_evidence(live.db)


def test_intake_first_then_samples(tmp_path: Path) -> None:
    with stack(tmp_path) as live:
        results = live.send_intake(intake_batch())
        assert [r["result"] for r in results] == ["accepted", "accepted"]
        waiting = evidence(live.db)
        assert len(waiting) == len(EXPECTED_AMOUNTS)
        assert all(i.link_status == "pending" and not i.complete for i in waiting)
        assert {(i.intake_id, i.component_id): (i.amount, i.unit) for i in waiting} == (
            EXPECTED_AMOUNTS
        )
        assert totals(live.db) == {}
        live.send_samples()
        assert totals(live.db) == EXPECTED_TOTALS
        assert sample_count(live.db) == len(SAMPLES)
        assert_complete_evidence(live.db)


def test_lost_ack_replay_is_duplicate(tmp_path: Path) -> None:
    batch = intake_batch()
    with stack(tmp_path) as live:
        live.send_samples()
        first = live.send_intake(copy.deepcopy(batch))
        assert [r["result"] for r in first] == ["accepted", "accepted"]
        before = evidence(live.db)
        replay = live.send_intake(copy.deepcopy(batch))
        assert [r["result"] for r in replay] == ["duplicate", "duplicate"]
        assert totals(live.db) == EXPECTED_TOTALS
        assert evidence(live.db) == before
        live.send_samples()
        assert totals(live.db) == EXPECTED_TOTALS
        assert sample_count(live.db) == len(SAMPLES)
        assert evidence(live.db) == before
        assert_complete_evidence(live.db)
        deleted = live.send_intake(delete_batch(INTAKE_1))
        assert [r["result"] for r in deleted] == ["accepted"]
        remaining = evidence(live.db)
        assert {i.intake_id for i in remaining} == {INTAKE_2}
        late = live.send_intake(copy.deepcopy(batch))
        by_operation = {r["operation_id"]: r["result"] for r in late}
        assert by_operation[batch["operations"][0]["operation_id"]] == "duplicate"  # pyright: ignore[reportIndexIssue]
        assert totals(live.db) == EXPECTED_TOTALS
        assert evidence(live.db) == remaining
        tool_items = cast(
            "list[dict[str, object]]", tool_page(live.db, {"limit": 50})["items"]
        )
        assert {t["intake_id"] for t in tool_items} == {INTAKE_2}


def test_deleted_intake_leaves_evidence(tmp_path: Path) -> None:
    upsert = intake_batch()
    with stack(tmp_path) as live:
        live.send_samples()
        _ = live.send_intake(copy.deepcopy(upsert))
        result = live.send_intake(delete_batch(INTAKE_1))
        assert [r["result"] for r in result] == ["accepted"]
        remaining = evidence(live.db)
        assert {i.intake_id for i in remaining} == {INTAKE_2}
        assert [(i.component_id, i.amount) for i in remaining] == [("water", "250")]
        tool_items = cast(
            "list[dict[str, object]]", tool_page(live.db, {"limit": 50})["items"]
        )
        assert {t["intake_id"] for t in tool_items} == {INTAKE_2}
        assert totals(live.db) == EXPECTED_TOTALS
        replay = live.send_intake(copy.deepcopy(upsert))
        assert replay[0]["result"] != "accepted"
        assert {i.intake_id for i in evidence(live.db)} == {INTAKE_2}
        assert totals(live.db) == EXPECTED_TOTALS


def test_evidence_through_mcp_tool(tmp_path: Path) -> None:
    with stack(tmp_path) as live:
        live.send_samples()
        _ = live.send_intake(intake_batch())
        direct = evidence(live.db)
        first = tool_page(live.db, {"limit": 2})
        first_items = cast("list[dict[str, object]]", first["items"])
        assert len(first_items) == 2
        cursor = first["next_cursor"]
        assert isinstance(cursor, str)
        second = tool_page(live.db, {"limit": 2, "cursor": cursor})
        second_items = cast("list[dict[str, object]]", second["items"])
        assert second["next_cursor"] is None
        pairs = [
            (t["intake_id"], t["component_id"]) for t in first_items + second_items
        ]
        assert len(pairs) == len(set(pairs)) == len(direct)
        assert pairs == [(i.intake_id, i.component_id) for i in direct]
        assert [t["link_status"] for t in first_items + second_items] == [
            i.link_status for i in direct
        ]
        assert [t["producer_id"] for t in first_items + second_items] == [
            i.producer_id for i in direct
        ]
