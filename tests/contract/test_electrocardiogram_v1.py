"""Contract tests for the optional `electrocardiograms` array (HealthRelay fork)."""

import json
from pathlib import Path
from typing import TypeAlias, cast

import pytest
from pydantic import ValidationError

from health_bridge.contract import HealthBridgeBatchV1

FIXTURE_PATH = Path("fixtures/health_bridge_batch_v1.electrocardiogram.synthetic.json")
JsonObject: TypeAlias = dict[str, object]


def _fixture() -> JsonObject:
    return cast("JsonObject", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _ecg(**overrides: object) -> JsonObject:
    records = cast("list[JsonObject]", _fixture()["electrocardiograms"])
    record = dict(records[0])
    record.update(overrides)
    return record


def test_fixture_electrocardiogram_parses_with_summary_and_voltages() -> None:
    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(_fixture()))

    assert len(batch.electrocardiograms) == 1
    ecg = batch.electrocardiograms[0]
    assert ecg.classification == "sinus_rhythm"
    assert ecg.symptoms_status == "none"
    assert ecg.voltage_count == 8
    assert len(ecg.voltages_microvolts) == 8


def test_batch_without_electrocardiograms_key_still_parses() -> None:
    payload = _fixture()
    del payload["electrocardiograms"]

    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))

    assert batch.electrocardiograms == ()


def test_summary_only_electrocardiogram_is_accepted() -> None:
    payload = _fixture()
    record = _ecg(voltage_count=15360)
    del record["voltages_microvolts"]
    payload["electrocardiograms"] = [record]

    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))

    assert batch.electrocardiograms[0].voltages_microvolts == ()
    assert batch.electrocardiograms[0].voltage_count == 15360


@pytest.mark.parametrize(
    "overrides",
    [
        {"classification": "normal"},
        {"symptoms_status": "maybe"},
        {"start_time": "2026-06-04T07:16:00Z"},
        {"voltage_count": 7},
        {"average_heart_rate_bpm": -1},
        {"sampling_frequency_hz": None},
        {"voltages_microvolts": [1.0, "x"]},
        {"unexpected": True},
    ],
)
def test_electrocardiogram_rejects_invalid_records(overrides: JsonObject) -> None:
    payload = _fixture()
    payload["electrocardiograms"] = [_ecg(**overrides)]

    with pytest.raises(ValidationError):
        _ = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))


def test_deleted_record_accepts_electrocardiogram_family() -> None:
    payload = _fixture()
    payload["deleted_records"] = [
        {
            "record_family": "electrocardiogram",
            "source_key": "synthetic.watch.bravo",
            "client_record_id": "synthetic-ecg-20260604",
            "deleted_at": "2026-06-05T00:00:00Z",
        },
    ]

    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))

    assert batch.deleted_records[0].record_family == "electrocardiogram"
