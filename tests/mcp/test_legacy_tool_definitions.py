"""The original MCP tool definitions must stay byte-identical."""

import json
from pathlib import Path
from typing import Final, cast

from health_bridge.mcp.tools import (
    MCP_TOOL_DEFINITIONS,
    TOOL_ARGUMENT_MODELS,
    TOOL_CALLERS,
    ToolDefinition,
)

SNAPSHOT_PATH: Final = (
    Path(__file__).parent / "fixtures" / "legacy_tool_definitions.json"
)
OPTIONAL_TOOL_NAMES: Final = frozenset({"get_intake_evidence_v1"})


def _snapshot() -> list[dict[str, object]]:
    return cast(
        "list[dict[str, object]]",
        json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")),
    )


def _legacy_names() -> list[str]:
    return [cast("str", entry["name"]) for entry in _snapshot()]


def _serialise(definitions: list[ToolDefinition]) -> str:
    payload = [
        {
            "name": definition.name,
            "description": definition.description,
            "input_schema": definition.input_schema,
        }
        for definition in definitions
    ]
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_legacy_definitions_match_snapshot_bytes() -> None:
    names = _legacy_names()
    by_name = {definition.name: definition for definition in MCP_TOOL_DEFINITIONS}
    legacy = [by_name[name] for name in names]

    assert len(names) == 9
    assert _serialise(legacy) == SNAPSHOT_PATH.read_text(encoding="utf-8")


def test_legacy_tools_are_registered() -> None:
    defined = {definition.name for definition in MCP_TOOL_DEFINITIONS}
    for name in _legacy_names():
        assert name in defined
        assert name in TOOL_CALLERS
        assert name in TOOL_ARGUMENT_MODELS


def test_tool_names_are_legacy_plus_optional_evidence_tool() -> None:
    names = {definition.name for definition in MCP_TOOL_DEFINITIONS}
    legacy = set(_legacy_names())

    assert legacy <= names
    assert names - legacy <= OPTIONAL_TOOL_NAMES
