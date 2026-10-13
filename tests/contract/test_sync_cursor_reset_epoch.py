"""Contract bounds for ordered sleep baseline reset cursors (RELAY-S11)."""

import json
from pathlib import Path
from typing import TypeAlias, cast

import pytest
from pydantic import ValidationError

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.contract.batch_v1 import SyncCursor

MEDICATION_FIXTURE = Path("fixtures/health_bridge_batch_v1.medication.synthetic.json")
RESET_KIND = "anchored_sleep_baseline_reset"
JsonObject: TypeAlias = dict[str, object]


def _cursor(value: str, kind: str = RESET_KIND) -> JsonObject:
    return {
        "source_key": "synthetic.phone.alpha",
        "cursor_kind": kind,
        "cursor_value": value,
    }


def _batch_with_cursor(cursor: JsonObject) -> str:
    payload = cast(
        "JsonObject", json.loads(MEDICATION_FIXTURE.read_text(encoding="utf-8"))
    )
    sync = cast("JsonObject", payload["sync"])
    sync["cursors"] = [cursor]
    return json.dumps(payload)


@pytest.mark.parametrize("value", ["v2:1", "v2:42", "v2:9223372036854775807"])
def test_reset_epoch_accepts_bounded_ordered_values(value: str) -> None:
    cursor = SyncCursor.model_validate(_cursor(value))

    assert cursor.cursor_value == value


@pytest.mark.parametrize(
    "value",
    [
        "v2:" + "1" * 4301,
        "v2:" + "1" * 4300,
        "v2:" + "9" * 19,
        "v2:9223372036854775808",
        "v2:0",
        "v2:01",
        "v2:abc",
    ],
    ids=[
        "4301-digits",
        "4300-digits",
        "19-nines",
        "int64-overflow",
        "zero",
        "leading-zero",
        "text",
    ],
)
def test_reset_epoch_rejects_unbounded_or_invalid_ordered_values(value: str) -> None:
    with pytest.raises(ValidationError):
        _ = SyncCursor.model_validate(_cursor(value))


def test_other_cursor_kinds_keep_free_form_values() -> None:
    cursor = SyncCursor.model_validate(_cursor("v2:" + "1" * 4301, kind="other"))

    assert cursor.cursor_value == "v2:" + "1" * 4301


def test_batch_with_unbounded_reset_epoch_is_a_validation_error() -> None:
    body = _batch_with_cursor(_cursor("v2:" + "1" * 4301))

    with pytest.raises(ValidationError):
        _ = HealthBridgeBatchV1.model_validate_json(body)
