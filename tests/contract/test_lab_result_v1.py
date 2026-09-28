"""Contract tests for the optional `lab_results` array (HealthRelay fork).

Lab results come from the on-device Apple Health export.zip importer (not HealthKit
live sync -- ECG and medications already cover that), so there is no cursor and no
per-object HealthKit authorization concern here, only the FHIR Observation shape.
"""

import json
from pathlib import Path
from typing import TypeAlias, cast

import pytest
from pydantic import ValidationError

from health_bridge.contract import HealthBridgeBatchV1

FIXTURE_PATH = Path("fixtures/health_bridge_batch_v1.lab_result.synthetic.json")
JsonObject: TypeAlias = dict[str, object]


def _fixture() -> JsonObject:
    return cast("JsonObject", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _lab(**overrides: object) -> JsonObject:
    records = cast("list[JsonObject]", _fixture()["lab_results"])
    record = dict(records[0])
    record.update(overrides)
    return record


def test_fixture_lab_result_parses_with_numeric_value_and_range() -> None:
    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(_fixture()))

    assert len(batch.lab_results) == 1
    lab = batch.lab_results[0]
    assert lab.loinc == "2951-2"
    assert lab.name == "Sodium"
    assert lab.effective_date == "2026-06-04"
    assert lab.value_num == 140.0
    assert lab.unit == "mmol/L"
    assert lab.ref_low == 136.0
    assert lab.ref_high == 145.0
    assert lab.value_text is None


def test_batch_without_lab_results_key_still_parses() -> None:
    payload = _fixture()
    del payload["lab_results"]

    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))

    assert batch.lab_results == ()


def test_text_only_lab_result_is_accepted() -> None:
    payload = _fixture()
    record = _lab(value_text="Negative", ref_text="Negative")
    for key in ("value_num", "unit", "ref_low", "ref_high"):
        _ = record.pop(key, None)
    payload["lab_results"] = [record]

    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))

    assert batch.lab_results[0].value_text == "Negative"
    assert batch.lab_results[0].value_num is None


def test_lab_result_without_loinc_is_accepted() -> None:
    payload = _fixture()
    record = _lab()
    del record["loinc"]
    payload["lab_results"] = [record]

    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))

    assert batch.lab_results[0].loinc is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"effective_date": ""},
        {"value_num": None},
        {"unexpected": True},
    ],
)
def test_lab_result_rejects_invalid_records(overrides: JsonObject) -> None:
    payload = _fixture()
    record = _lab(**overrides)
    payload["lab_results"] = [record]

    with pytest.raises(ValidationError):
        _ = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))


def test_deleted_record_accepts_lab_result_family() -> None:
    payload = _fixture()
    payload["deleted_records"] = [
        {
            "record_family": "lab_result",
            "source_key": "apple_health.export",
            "client_record_id": "hk-labobs-0123456789abcdef",
            "deleted_at": "2026-06-05T00:00:00Z",
        },
    ]

    batch = HealthBridgeBatchV1.model_validate_json(json.dumps(payload))

    assert batch.deleted_records[0].record_family == "lab_result"
