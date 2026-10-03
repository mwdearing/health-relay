"""Tests for the read-only `health-bridge query intake-evidence` command."""

import hashlib
import json
from pathlib import Path
from typing import Final, cast

import pytest
from typer.testing import CliRunner, Result

from health_bridge import cli_query
from health_bridge.cli import app
from health_bridge.mcp.server import dispatch_request
from health_bridge.mcp.types import JsonObject, JsonValue
from tests.queries import intake_evidence_builder as builder

TOOL: Final = "get_intake_evidence_v1"
SECOND_OWNER: Final = "owner-2"
SAMPLE_IDS: Final = tuple(
    f"{number:08d}-0000-4000-8000-000000000000" for number in range(1, 6)
)
INTAKE_IDS: Final = tuple(
    f"aaaaaaaa-0000-4000-8000-00000000000{number}" for number in range(1, 6)
)


RUNNER: Final = CliRunner()


def build_db(path: Path, *, second_owner: bool = False) -> Path:
    connection = builder.make_connection(path)
    builder.register_producer_row(connection)
    for index in range(len(INTAKE_IDS)):
        _ = builder.put_sample(connection, sample_uuid=SAMPLE_IDS[index])
        _ = builder.put_revision(
            connection,
            intake_id=INTAKE_IDS[index],
            revision=1,
            facts=[builder.fact("c1")],
            links=[builder.link("c1", SAMPLE_IDS[index])],
        )
    if second_owner:
        builder.register_producer_row(connection, owner_id=SECOND_OWNER)
    connection.close()
    return path


def run_query(*args: str) -> Result:
    return RUNNER.invoke(app, ["query", "intake-evidence", *args])


def document(result: Result) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(result.stdout))


def items(result: Result) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", document(result)["items"])


def mcp_payload(db_path: Path, arguments: JsonValue = None) -> str:
    params: JsonObject = {"name": TOOL}
    if arguments is not None:
        params["arguments"] = arguments
    response = cast(
        "dict[str, object]",
        dispatch_request(
            db_path,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": params,
            },
        ),
    )
    result = cast("dict[str, list[dict[str, str]]]", response["result"])
    return result["content"][0]["text"]


def mcp_document(db_path: Path, arguments: JsonValue = None) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(mcp_payload(db_path, arguments)))


def test_default_owner_lists_every_item(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")

    # When
    result = run_query("--db", str(db_path))

    # Then
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    body = document(result)
    assert len(cast("list[object]", body["items"])) == 5
    assert body["next_cursor"] is None
    assert [item["intake_id"] for item in items(result)] == list(INTAKE_IDS)
    assert all(item["link_status"] == "verified" for item in items(result))


def test_explicit_owner_selects_one_of_two_owners(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite", second_owner=True)

    # When
    owned_result = run_query("--db", str(db_path), "--owner-id", builder.OWNER)
    other_result = run_query("--db", str(db_path), "--owner-id", SECOND_OWNER)

    # Then
    assert owned_result.exit_code == 0, owned_result.output
    assert len(items(owned_result)) == 5
    assert items(other_result) == []
    assert document(owned_result) == mcp_document(
        db_path,
        {"owner_id": builder.OWNER},
    )


def test_several_owners_ask_for_owner_id(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite", second_owner=True)

    # When
    result = run_query("--db", str(db_path))

    # Then
    assert result.exit_code == 1
    assert "owner" in result.stderr.lower()
    assert "--owner-id" in result.stderr
    assert "Traceback" not in result.output
    assert len(result.stderr.strip().splitlines()) == 1


def test_no_registered_owner_is_reported(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    connection = builder.make_connection(db_path)
    connection.close()

    # When
    result = run_query("--db", str(db_path))

    # Then
    assert result.exit_code == 1
    assert "no intake owner" in result.stderr.lower()
    assert "Traceback" not in result.output
    assert result.stdout == ""


def test_intake_id_limits_the_listing_to_one_intake(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")

    # When
    result = run_query("--db", str(db_path), "--intake-id", INTAKE_IDS[2])

    # Then
    assert result.exit_code == 0, result.output
    assert [item["intake_id"] for item in items(result)] == [INTAKE_IDS[2]]
    assert document(result) == mcp_document(db_path, {"intake_id": INTAKE_IDS[2]})


def test_cursor_continues_the_listing(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")

    # When
    first = run_query("--db", str(db_path), "--limit", "2")
    cursor = document(first)["next_cursor"]
    second = run_query("--db", str(db_path), "--limit", "2", "--cursor", str(cursor))

    # Then
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert isinstance(cursor, str)
    assert len(items(first)) == 2
    assert len(items(second)) == 2
    every_item = items(run_query("--db", str(db_path)))
    assert items(first) + items(second) == every_item[:4]
    assert document(second) == mcp_document(
        db_path,
        {"limit": 2, "cursor": cursor},
    )


def test_all_follows_every_page(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")

    # When
    result = run_query("--db", str(db_path), "--all", "--limit", "2")

    # Then
    assert result.exit_code == 0, result.output
    body = document(result)
    assert len(cast("list[object]", body["items"])) == 5
    assert body["next_cursor"] is None
    assert body["items"] == document(run_query("--db", str(db_path)))["items"]


def test_all_stops_at_the_page_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")
    monkeypatch.setattr(cli_query, "MAX_INTAKE_EVIDENCE_PAGES", 2)

    # When
    result = run_query("--db", str(db_path), "--all", "--limit", "2")

    # Then
    assert result.exit_code == 1
    assert "--all" in result.stderr
    assert "--intake-id" in result.stderr
    assert result.stdout == ""
    assert "Traceback" not in result.output


def test_all_rejects_a_cursor(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")
    first = run_query("--db", str(db_path), "--limit", "2")
    cursor = document(first)["next_cursor"]

    # When
    result = run_query("--db", str(db_path), "--all", "--cursor", str(cursor))

    # Then
    assert result.exit_code == 1
    assert "--all" in result.stderr
    assert "--cursor" in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1


def test_invalid_cursor_exits_with_one_line(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")

    # When
    result = run_query("--db", str(db_path), "--cursor", "not-a-cursor")

    # Then
    assert result.exit_code == 1
    assert "cursor" in result.stderr.lower()
    assert "Traceback" not in result.output
    assert len(result.stderr.strip().splitlines()) == 1
    assert result.stdout == ""


def test_limit_below_the_minimum_exits_with_one_line(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")

    # When
    result = run_query("--db", str(db_path), "--limit", "0")

    # Then
    assert result.exit_code == 1
    assert "limit" in result.stderr.lower()
    assert "Traceback" not in result.output
    assert len(result.stderr.strip().splitlines()) == 1


def test_limit_above_the_maximum_exits_with_one_line(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")

    # When
    result = run_query("--db", str(db_path), "--limit", "501")

    # Then
    assert result.exit_code == 1
    assert "limit" in result.stderr.lower()
    assert "Traceback" not in result.output
    assert len(result.stderr.strip().splitlines()) == 1


def test_missing_database_is_not_created(tmp_path: Path) -> None:
    # Given
    missing = tmp_path / "absent" / "receiver.sqlite"

    # When
    result = run_query("--db", str(missing))

    # Then
    assert result.exit_code == 1
    assert not missing.exists()
    assert not missing.parent.exists()
    assert "Traceback" not in result.output
    assert len(result.stderr.strip().splitlines()) == 1


def test_query_leaves_the_database_unchanged(tmp_path: Path) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    entries_before = sorted(entry.name for entry in tmp_path.iterdir())

    # When
    result = run_query("--db", str(db_path), "--all")

    # Then
    assert result.exit_code == 0, result.output
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    assert sorted(entry.name for entry in tmp_path.iterdir()) == entries_before


@pytest.mark.parametrize(
    ("cli_args", "tool_args"),
    [
        ((), {}),
        (("--owner-id", builder.OWNER, "--limit", "2"), {"limit": 2}),
        (("--intake-id", INTAKE_IDS[3]), {"intake_id": INTAKE_IDS[3]}),
        (
            ("--owner-id", builder.OWNER, "--intake-id", INTAKE_IDS[0], "--limit", "3"),
            {"owner_id": builder.OWNER, "intake_id": INTAKE_IDS[0], "limit": 3},
        ),
    ],
)
def test_cli_output_matches_the_mcp_tool_output(
    tmp_path: Path,
    cli_args: tuple[str, ...],
    tool_args: JsonValue,
) -> None:
    # Given
    db_path = build_db(tmp_path / "receiver.sqlite")

    # When
    result = run_query("--db", str(db_path), *cli_args)

    # Then
    assert result.exit_code == 0, result.output
    assert document(result) == mcp_document(db_path, tool_args)
    assert result.stdout.strip() == mcp_payload(db_path, tool_args)
