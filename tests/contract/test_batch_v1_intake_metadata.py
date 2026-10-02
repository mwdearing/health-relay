import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from health_bridge.contract.batch_v1 import HealthBridgeBatchV1

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
BASE = json.loads((FIXTURES / "health_bridge_batch_v1.synthetic.json").read_text())
UUID = "e6677963-418c-4027-b563-551d8a531eed"
GOOD = {
    "intake_id": UUID,
    "intake_component_id": "water",
    "sync_identifier": f"intake:{UUID}:water",
    "sync_version": "2",
}


def parse(meta: dict[str, str]) -> HealthBridgeBatchV1:
    batch = copy.deepcopy(BASE)
    batch["samples"][0]["metadata"] = meta
    return HealthBridgeBatchV1.model_validate_json(json.dumps(batch))


def test_all_four_keys_accepted_and_preserved() -> None:
    assert parse(GOOD).samples[0].metadata == GOOD


def test_unrelated_keys_stay_alongside() -> None:
    meta = {**GOOD, "aggregation": "daily_sum", "sync_window": "anchored"}
    assert parse(meta).samples[0].metadata == meta


def test_sync_pair_alone_accepted() -> None:
    parse({"sync_identifier": "abc", "sync_version": "1"})


def test_no_intake_keys_accepted() -> None:
    parse({"aggregation": "daily_sum"})


def test_sync_window_alone_accepted() -> None:
    parse({"sync_window": "anchored"})


@pytest.mark.parametrize(
    "meta",
    [
        {"intake_id": UUID},
        {"intake_component_id": "water"},
        {"sync_identifier": "abc"},
        {"sync_version": "1"},
        {"intake_id": UUID, "intake_component_id": "water", "sync_version": "1"},
    ],
)
def test_lone_halves_rejected(meta: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        parse(meta)


@pytest.mark.parametrize(
    "intake_id",
    [UUID.upper(), "not-a-uuid", UUID.replace("-", ""), "", f" {UUID}"],
)
def test_bad_intake_id_rejected(intake_id: str) -> None:
    with pytest.raises(ValidationError):
        parse({**GOOD, "intake_id": intake_id})


@pytest.mark.parametrize("slug", ["Water Bottle", "", "-water", "a" * 65, "wa/ter"])
def test_bad_component_slug_rejected(slug: str) -> None:
    with pytest.raises(ValidationError):
        parse({**GOOD, "intake_component_id": slug})


@pytest.mark.parametrize("slug", ["a", "a" * 64, "0x", "a.b_c-d"])
def test_good_component_slug_accepted(slug: str) -> None:
    parse({**GOOD, "intake_component_id": slug})


@pytest.mark.parametrize(
    "version", ["0", "-1", "+1", "1.5", "01", "", "abc", "9223372036854775808"]
)
def test_bad_sync_version_rejected(version: str) -> None:
    with pytest.raises(ValidationError):
        parse({**GOOD, "sync_version": version})


@pytest.mark.parametrize("version", ["1", "9223372036854775807"])
def test_good_sync_version_accepted(version: str) -> None:
    parse({**GOOD, "sync_version": version})


def test_sync_identifier_length_bounds() -> None:
    parse({**GOOD, "sync_identifier": "x" * 256})
    parse({**GOOD, "sync_identifier": "x"})
    for bad in ("", "x" * 257):
        with pytest.raises(ValidationError):
            parse({**GOOD, "sync_identifier": bad})


def test_unknown_intake_prefixed_key_rejected() -> None:
    with pytest.raises(ValidationError):
        parse({**GOOD, "intake_revision": "3"})
    with pytest.raises(ValidationError):
        parse({"intake_other": "x"})


def test_sync_version_without_identifier_rejected_even_with_intake_pair() -> None:
    with pytest.raises(ValidationError):
        parse({"intake_id": UUID, "intake_component_id": "water", "sync_version": "1"})


def test_new_fixture_valid_and_carries_all_keys() -> None:
    text = (
        FIXTURES / "health_bridge_batch_v1.intake_metadata.synthetic.json"
    ).read_text()
    batch = HealthBridgeBatchV1.model_validate_json(text)
    assert any(set(GOOD) <= set(s.metadata) for s in batch.samples)
