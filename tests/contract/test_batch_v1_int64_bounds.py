"""Contract bounds for batch integers stored in SQLite INTEGER columns (RELAY-S10)."""

import json
from pathlib import Path
from typing import TypeAlias, cast

import pytest
from pydantic import ValidationError

from health_bridge.contract import HealthBridgeBatchV1

MEDICATION_FIXTURE = Path("fixtures/health_bridge_batch_v1.medication.synthetic.json")
ECG_FIXTURE = Path("fixtures/health_bridge_batch_v1.electrocardiogram.synthetic.json")
JsonObject: TypeAlias = dict[str, object]
INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)


def _payload(path: Path) -> JsonObject:
    return cast("JsonObject", json.loads(path.read_text(encoding="utf-8")))


def _with_status_raw(value: int) -> str:
    payload = _payload(MEDICATION_FIXTURE)
    events = cast("list[JsonObject]", payload["medication_dose_events"])
    events[0]["status_raw"] = value
    return json.dumps(payload)


def _with_voltage_count_only(value: int) -> str:
    payload = _payload(ECG_FIXTURE)
    records = cast("list[JsonObject]", payload["electrocardiograms"])
    record = dict(records[0])
    _ = record.pop("voltages_microvolts", None)
    record["voltage_count"] = value
    payload["electrocardiograms"] = [record]
    return json.dumps(payload)


@pytest.mark.parametrize("value", [INT64_MIN, 0, INT64_MAX])
def test_status_raw_accepts_the_int64_range(value: int) -> None:
    batch = HealthBridgeBatchV1.model_validate_json(_with_status_raw(value))

    assert batch.medication_dose_events[0].status_raw == value


@pytest.mark.parametrize("value", [INT64_MIN - 1, INT64_MAX + 1, 2**64])
def test_status_raw_rejects_values_outside_the_int64_range(value: int) -> None:
    with pytest.raises(ValidationError):
        _ = HealthBridgeBatchV1.model_validate_json(_with_status_raw(value))


@pytest.mark.parametrize("value", [INT64_MAX + 1, 2**64])
def test_voltage_count_rejects_values_outside_the_int64_range(value: int) -> None:
    with pytest.raises(ValidationError):
        _ = HealthBridgeBatchV1.model_validate_json(_with_voltage_count_only(value))


def test_voltage_count_accepts_int64_max_without_voltages() -> None:
    batch = HealthBridgeBatchV1.model_validate_json(_with_voltage_count_only(INT64_MAX))

    assert batch.electrocardiograms[0].voltage_count == INT64_MAX
