"""Tests for the get_intake_evidence_v1 MCP tool."""

import json
from pathlib import Path
from typing import cast

import pytest

from health_bridge.mcp.server import dispatch_request
from health_bridge.mcp.tools import MCP_TOOL_DEFINITIONS
from health_bridge.mcp.types import JsonObject, JsonValue
from tests.queries.intake_evidence_builder import (
    OWNER,
    fact,
    link,
    make_connection,
    put_revision,
    put_sample,
    register_producer_row,
)

TOOL = "get_intake_evidence_v1"
INTAKE_A = "aaaaaaaa-0000-4000-8000-000000000001"
INTAKE_B = "bbbbbbbb-0000-4000-8000-000000000002"
SAMPLE = "00000001-0000-4000-8000-000000000000"


def build_db(
    tmp_path: Path,
    *,
    owners: tuple[str, ...] = (OWNER,),
    components: int = 3,
) -> Path:
    path = tmp_path / "receiver.sqlite"
    connection = make_connection(path)
    for owner in owners:
        register_producer_row(connection, owner_id=owner)
    if owners:
        _ = put_sample(connection, sample_uuid=SAMPLE)
        _ = put_revision(
            connection,
            intake_id=INTAKE_A,
            revision=1,
            facts=[fact(f"c{n}") for n in range(components)],
            links=[link("c0", SAMPLE)],
            owner_id=owners[0],
        )
        _ = put_revision(
            connection,
            intake_id=INTAKE_B,
            revision=1,
            facts=[fact("water")],
            owner_id=owners[0],
        )
    connection.close()
    return path


def call(db_path: Path, arguments: JsonValue = None) -> dict[str, object]:
    params: JsonObject = {"name": TOOL}
    if arguments is not None:
        params["arguments"] = arguments
    return cast(
        "dict[str, object]",
        dispatch_request(
            db_path,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        ),
    )


def page(response: dict[str, object]) -> dict[str, object]:
    result = cast("dict[str, list[dict[str, str]]]", response["result"])
    return cast("dict[str, object]", json.loads(result["content"][0]["text"]))


def error(response: dict[str, object]) -> tuple[int, str]:
    payload = cast("dict[str, object]", response["error"])
    return cast("int", payload["code"]), cast("str", payload["message"])


def test_definition_shape() -> None:
    definition = MCP_TOOL_DEFINITIONS[-1]
    assert definition.name == TOOL
    assert "sql" not in definition.description.lower()
    assert not definition.description.startswith("Read-only")
    assert "read-only" in definition.description.lower()
    schema = definition.input_schema
    assert schema["additionalProperties"] is False
    assert "required" not in schema
    properties = cast("dict[str, dict[str, object]]", schema["properties"])
    assert {k: v["type"] for k, v in properties.items()} == {
        "owner_id": "string",
        "intake_id": "string",
        "cursor": "string",
        "limit": "integer",
    }
    assert (
        properties["limit"]["minimum"],
        properties["limit"]["maximum"],
        properties["limit"]["default"],
    ) == (1, 500, 100)


def test_single_owner_is_the_default(tmp_path: Path) -> None:
    body = page(call(build_db(tmp_path)))
    items = cast("list[dict[str, object]]", body["items"])
    assert len(items) == 4
    first = items[0]
    assert first["producer_id"] == "nutrition-app"
    assert first["link_status"] == "verified"
    assert first["complete"] is True
    assert "next_cursor" in body
    assert body["next_cursor"] is None


def test_explicit_owner(tmp_path: Path) -> None:
    db = build_db(tmp_path, owners=(OWNER, "owner-2"))
    assert len(cast("list[object]", page(call(db, {"owner_id": OWNER}))["items"])) == 4
    assert page(call(db, {"owner_id": "owner-2"}))["items"] == []


def test_no_owner_registered(tmp_path: Path) -> None:
    code, message = error(call(build_db(tmp_path, owners=())))
    assert code == -32602
    assert "no intake owner registered" in message


def test_two_owners_require_owner_id(tmp_path: Path) -> None:
    code, message = error(call(build_db(tmp_path, owners=(OWNER, "owner-2"))))
    assert code == -32602
    assert "owner_id" in message


def test_invalid_cursor(tmp_path: Path) -> None:
    code, message = error(call(build_db(tmp_path), {"cursor": "nope"}))
    assert code == -32602
    assert TOOL in message
    assert "cursor" in message


@pytest.mark.parametrize("limit", [0, 501, "5", True])
def test_invalid_limit(tmp_path: Path, limit: JsonValue) -> None:
    code, message = error(call(build_db(tmp_path), {"limit": limit}))
    assert code == -32602
    assert TOOL in message


def test_unknown_argument(tmp_path: Path) -> None:
    code, message = error(call(build_db(tmp_path), {"unexpected": 1}))
    assert code == -32602
    assert "unexpected: is not an accepted argument" in message


def test_pagination_round_trip(tmp_path: Path) -> None:
    db = build_db(tmp_path)
    seen: list[tuple[str, str]] = []
    cursor: JsonValue = None
    for _ in range(10):
        arguments: JsonObject = {"limit": 1}
        if cursor is not None:
            arguments["cursor"] = cursor
        body = page(call(db, arguments))
        items = cast("list[dict[str, str]]", body["items"])
        assert len(items) == 1
        seen.append((items[0]["intake_id"], items[0]["component_id"]))
        cursor = cast("str | None", body["next_cursor"])
        if cursor is None:
            break
    assert seen == [
        (INTAKE_A, "c0"),
        (INTAKE_A, "c1"),
        (INTAKE_A, "c2"),
        (INTAKE_B, "water"),
    ]
    assert cursor is None


def test_intake_filter(tmp_path: Path) -> None:
    body = page(call(build_db(tmp_path), {"intake_id": INTAKE_B}))
    items = cast("list[dict[str, str]]", body["items"])
    assert [i["intake_id"] for i in items] == [INTAKE_B]
