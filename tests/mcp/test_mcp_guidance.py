from __future__ import annotations

from subprocess import run
from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from health_bridge.mcp.server import dispatch_request
from health_bridge.mcp.tools import MCP_TOOL_DEFINITIONS
from tests.fixture_helpers import initialized_fixture_db

if TYPE_CHECKING:
    from pathlib import Path

    from health_bridge.mcp.types import JsonObject

EMPTY_WINDOW_NOTE = (
    "The requested window is empty: end_date is exclusive. "
    "For one day, set end_date to the next day."
)
UNKNOWN_CODE_HINT = "call list_synced_metrics for valid codes"
DB_HINT = (
    "Check the database path this server was started with "
    "(the healthrelay plugin reads it from ~/.config/healthrelay/db-path)."
)


class GuidanceModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True)


class Content(GuidanceModel):
    text: str


class CallResult(GuidanceModel):
    content: list[Content]


class CallResponse(GuidanceModel):
    result: CallResult


class Payload(GuidanceModel):
    missing_data_notes: list[str]


class ErrorBody(GuidanceModel):
    code: int
    message: str


class ErrorReply(GuidanceModel):
    error: ErrorBody


def _call(db_path: Path, name: str, arguments: JsonObject) -> JsonObject:
    return dispatch_request(
        db_path,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )


def _notes(db_path: Path, name: str, arguments: JsonObject) -> list[str]:
    response = CallResponse.model_validate(_call(db_path, name, arguments))
    return Payload.model_validate_json(
        response.result.content[0].text
    ).missing_data_notes


def _error(db_path: Path, name: str, arguments: JsonObject) -> ErrorBody:
    return ErrorReply.model_validate(_call(db_path, name, arguments)).error


@pytest.mark.parametrize(
    "tool", ["get_workouts", "get_sleep_summary", "get_daily_summary"]
)
def test_same_day_window_explains_exclusive_end_date(tool: str, tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)

    notes = _notes(
        db_path, tool, {"start_date": "2026-06-03", "end_date": "2026-06-03"}
    )

    assert EMPTY_WINDOW_NOTE in notes


@pytest.mark.parametrize(
    "tool", ["get_workouts", "get_sleep_summary", "get_daily_summary"]
)
def test_next_day_window_has_no_empty_window_note(tool: str, tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)

    notes = _notes(
        db_path, tool, {"start_date": "2026-06-03", "end_date": "2026-06-04"}
    )

    assert EMPTY_WINDOW_NOTE not in notes


def test_unknown_type_code_is_named_in_a_note(tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)

    notes = _notes(
        db_path,
        "get_timeseries",
        {
            "type_codes": ["step_count", "steps"],
            "start_time": "2026-06-01T00:00:00Z",
            "end_time": "2026-06-09T00:00:00Z",
        },
    )

    unknown = [note for note in notes if "step_count" in note]
    assert len(unknown) == 1
    assert UNKNOWN_CODE_HINT in unknown[0]
    assert "steps" not in unknown[0].replace(UNKNOWN_CODE_HINT, "")


@pytest.mark.parametrize(
    "code", ["steps", "weight", "energy", "body_mass", "active_energy"]
)
def test_known_and_legacy_type_codes_get_no_unknown_note(
    code: str, tmp_path: Path
) -> None:
    db_path = initialized_fixture_db(tmp_path)

    notes = _notes(
        db_path,
        "get_timeseries",
        {
            "type_codes": [code],
            "start_time": "2026-06-01T00:00:00Z",
            "end_time": "2026-06-09T00:00:00Z",
        },
    )

    assert not any(UNKNOWN_CODE_HINT in note for note in notes)


def test_bad_timestamp_error_names_field_and_format(tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)

    error = _error(
        db_path,
        "get_timeseries",
        {
            "type_codes": ["steps"],
            "start_time": "2026-06-01T00:00:00",
            "end_time": "2026-06-02T00:00:00Z",
        },
    )

    assert error.code == -32602
    assert error.message == (
        "Invalid arguments for get_timeseries: start_time: must look like "
        "2026-06-01T00:00:00Z (UTC, ending in Z)"
    )
    assert "2026-06-01T00:00:00'" not in error.message


def test_bad_date_error_names_field_without_echoing_value(tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)

    error = _error(
        db_path,
        "get_daily_summary",
        {"start_date": "June 1", "end_date": "2026-06-02"},
    )

    assert error.code == -32602
    assert error.message.startswith("Invalid arguments for get_daily_summary: ")
    assert "start_date: must be a date like 2026-06-01 (YYYY-MM-DD)" in error.message
    assert "June 1" not in error.message


def test_missing_and_unexpected_arguments_are_named(tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)

    missing = _error(db_path, "get_workouts", {"start_date": "2026-06-01"})
    extra = _error(db_path, "list_synced_metrics", {"bogus": "secret-value"})

    assert missing.code == -32602
    assert "end_date: is required" in missing.message
    assert extra.code == -32602
    assert "bogus: is not an accepted argument" in extra.message
    assert "secret-value" not in extra.message


def test_reversed_range_error_says_which_bound(tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)

    error = _error(
        db_path,
        "get_workouts",
        {"start_date": "2026-06-09", "end_date": "2026-06-01"},
    )

    assert error.code == -32602
    assert "start_date must not be after end_date" in error.message


def test_database_error_names_missing_file(tmp_path: Path) -> None:
    error = _error(tmp_path / "nope.sqlite", "get_bridge_status", {})

    assert error.code == -32000
    assert error.message == (
        f"HealthRelay database could not be read (not found). {DB_HINT}"
    )


def test_database_error_names_non_database_file(tmp_path: Path) -> None:
    db_path = tmp_path / "text.sqlite"
    _ = db_path.write_text("this is not a sqlite database, just text" * 20)

    error = _error(db_path, "get_bridge_status", {})

    assert error.code == -32000
    assert error.message == (
        "HealthRelay database could not be read (not a HealthRelay database). "
        f"{DB_HINT}"
    )


def test_database_error_names_locked_snapshot(tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)
    _ = db_path.with_name(f"{db_path.name}-wal").write_bytes(b"sentinel")

    error = _error(db_path, "get_bridge_status", {})

    assert error.code == -32000
    assert error.message == (
        f"HealthRelay database could not be read (locked). {DB_HINT}"
    )


def test_database_error_names_non_private_lock_files(tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)
    lock_files = sorted(db_path.parent.glob(f"{db_path.name}.*.lock"))
    assert lock_files, "the fixture database should have lock files"
    for lock_file in lock_files:
        lock_file.chmod(0o644)

    error = _error(db_path, "get_bridge_status", {})

    assert error.code == -32000
    assert error.message == (
        "HealthRelay database could not be read "
        "(lock files are not private: make them mode 0600). "
        f"{DB_HINT}"
    )


def test_stdio_does_not_answer_requests_without_an_id(tmp_path: Path) -> None:
    db_path = initialized_fixture_db(tmp_path)
    stdin = (
        '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{}}\n'
        '{"jsonrpc":"2.0","method":"tools/list"}\n'
        '{"jsonrpc":"2.0","method":"no/such/method"}\n'
        '{"jsonrpc":"2.0","id":7,"method":"tools/list"}\n'
    )

    result = run(
        ["uv", "run", "health-bridge", "mcp", "start", "--db", str(db_path)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    lines = result.stdout.splitlines()
    assert result.returncode == 0, result.stderr
    assert len(lines) == 1
    assert '"id":7' in lines[0]


def _description(name: str) -> str:
    return next(t.description for t in MCP_TOOL_DEFINITIONS if t.name == name)


@pytest.mark.parametrize(
    "tool", ["get_workouts", "get_sleep_summary", "get_daily_summary"]
)
def test_date_range_tools_document_exclusive_end_date(tool: str) -> None:
    description = _description(tool)

    assert "EXCLUSIVE" in description
    assert "YYYY-MM-DD" in description
    assert "UTC" in description


def test_timeseries_description_points_to_metric_catalog_and_format() -> None:
    description = _description("get_timeseries")

    assert "list_synced_metrics" in description
    assert "2026-06-01T00:00:00Z" in description
    assert "500" in description
    assert "truncated" in description


def test_supported_types_description_lists_categories() -> None:
    description = _description("list_supported_timeseries_types")

    for category in ("activity", "body", "heart", "provider_specific"):
        assert category in description


def test_every_description_leads_with_what_it_answers_and_states_read_only() -> None:
    for tool in MCP_TOOL_DEFINITIONS:
        assert not tool.description.startswith("Read-only")
        assert tool.description.count("no clinical interpretation") == 1
        assert tool.description.rstrip().endswith("no clinical interpretation.")


def test_row_cap_is_claimed_only_where_it_applies() -> None:
    assert "500" in _description("get_workouts")
    assert "500" not in _description("get_sleep_summary")
    assert "500" not in _description("get_daily_summary")


def test_timeseries_description_routes_sessions_to_their_own_tools() -> None:
    description = _description("get_timeseries")

    assert "workout" in description
    assert "sleep_analysis" in description
