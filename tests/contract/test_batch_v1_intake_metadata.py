import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from health_bridge.contract.batch_v1 import HealthBridgeBatchV1

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
BASE = cast(
    "dict[str, object]",
    json.loads((FIXTURES / "health_bridge_batch_v1.synthetic.json").read_text()),
)
UUID = "e6677963-418c-4027-b563-551d8a531eed"
GOOD = {
    "intake_id": UUID,
    "intake_component_id": "water",
    "sync_identifier": f"intake:{UUID}:water",
    "sync_version": "2",
}


def parse(meta: dict[str, str]) -> HealthBridgeBatchV1:
    batch = copy.deepcopy(BASE)
    samples = cast("list[dict[str, object]]", batch["samples"])
    samples[0]["metadata"] = meta
    return HealthBridgeBatchV1.model_validate_json(json.dumps(batch))


def test_all_four_keys_accepted_and_preserved() -> None:
    assert parse(GOOD).samples[0].metadata == GOOD


def test_unrelated_keys_stay_alongside() -> None:
    meta = {**GOOD, "aggregation": "daily_sum", "sync_window": "anchored"}
    assert parse(meta).samples[0].metadata == meta


def test_sync_pair_alone_accepted() -> None:
    _ = parse({"sync_identifier": "abc", "sync_version": "1"})


def test_no_intake_keys_accepted() -> None:
    _ = parse({"aggregation": "daily_sum"})


def test_sync_window_alone_accepted() -> None:
    _ = parse({"sync_window": "anchored"})


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
        _ = parse(meta)


@pytest.mark.parametrize(
    "intake_id",
    [UUID.upper(), "not-a-uuid", UUID.replace("-", ""), "", f" {UUID}"],
)
def test_bad_intake_id_rejected(intake_id: str) -> None:
    with pytest.raises(ValidationError):
        _ = parse({**GOOD, "intake_id": intake_id})


@pytest.mark.parametrize("slug", ["Water Bottle", "", "-water", "a" * 65, "wa/ter"])
def test_bad_component_slug_rejected(slug: str) -> None:
    with pytest.raises(ValidationError):
        _ = parse({**GOOD, "intake_component_id": slug})


@pytest.mark.parametrize("slug", ["a", "a" * 64, "0x", "a.b_c-d"])
def test_good_component_slug_accepted(slug: str) -> None:
    _ = parse({**GOOD, "intake_component_id": slug})


@pytest.mark.parametrize(
    "version", ["0", "-1", "+1", "1.5", "01", "", "abc", "9223372036854775808"]
)
def test_bad_sync_version_rejected(version: str) -> None:
    with pytest.raises(ValidationError):
        _ = parse({**GOOD, "sync_version": version})


@pytest.mark.parametrize("version", ["1", "9223372036854775807"])
def test_good_sync_version_accepted(version: str) -> None:
    _ = parse({**GOOD, "sync_version": version})


def test_sync_identifier_length_bounds() -> None:
    _ = parse({**GOOD, "sync_identifier": "x" * 256})
    _ = parse({**GOOD, "sync_identifier": "x"})
    for bad in ("", "x" * 257):
        with pytest.raises(ValidationError):
            _ = parse({**GOOD, "sync_identifier": bad})


def test_unknown_intake_prefixed_key_rejected() -> None:
    with pytest.raises(ValidationError):
        _ = parse({**GOOD, "intake_revision": "3"})
    with pytest.raises(ValidationError):
        _ = parse({"intake_other": "x"})


def test_sync_version_without_identifier_rejected_even_with_intake_pair() -> None:
    with pytest.raises(ValidationError):
        _ = parse(
            {"intake_id": UUID, "intake_component_id": "water", "sync_version": "1"}
        )


def test_new_fixture_valid_and_carries_all_keys() -> None:
    text = (
        FIXTURES / "health_bridge_batch_v1.intake_metadata.synthetic.json"
    ).read_text()
    batch = HealthBridgeBatchV1.model_validate_json(text)
    assert any(set(GOOD) <= set(s.metadata) for s in batch.samples)


SCHEMA = cast(
    "dict[str, object]",
    json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas"
            / "health_bridge.batch.v1.schema.json"
        ).read_text()
    ),
)
VALIDATOR = Draft202012Validator(SCHEMA)
is_valid = cast("Callable[[object], bool]", VALIDATOR.is_valid)


def schema_valid(meta: dict[str, str]) -> bool:
    batch = copy.deepcopy(BASE)
    samples = cast("list[dict[str, object]]", batch["samples"])
    samples[0]["metadata"] = meta
    return is_valid(batch)


def test_schema_accepts_intake_fixture() -> None:
    batch = cast(
        "dict[str, object]",
        json.loads(
            (
                FIXTURES / "health_bridge_batch_v1.intake_metadata.synthetic.json"
            ).read_text()
        ),
    )
    assert is_valid(batch)


def test_schema_accepts_good_and_unrelated_metadata() -> None:
    assert schema_valid(GOOD)
    assert schema_valid({"sync_window": "anchored"})
    assert schema_valid({"sync_identifier": "abc", "sync_version": "1"})
    assert schema_valid({**GOOD, "aggregation": "daily_sum"})


@pytest.mark.parametrize(
    "meta",
    [
        {"intake_id": UUID},
        {"intake_component_id": "water"},
        {"sync_identifier": "abc"},
        {"sync_version": "1"},
        {**GOOD, "intake_revision": "3"},
        {"intake_other": "x"},
        {**GOOD, "intake_id": UUID.upper()},
        {**GOOD, "intake_component_id": "-water"},
        {**GOOD, "sync_version": "01"},
        {**GOOD, "sync_version": "0"},
        {**GOOD, "sync_identifier": "x" * 257},
        {**GOOD, "sync_identifier": ""},
    ],
)
def test_schema_rejects_invalid_intake_metadata(meta: dict[str, str]) -> None:
    assert not schema_valid(meta)


@pytest.mark.parametrize(
    "meta",
    [
        {**GOOD, "intake_id": UUID + "\n"},
        {**GOOD, "intake_component_id": "water\n"},
        {**GOOD, "sync_version": "2\n"},
    ],
)
def test_schema_rejects_trailing_newline(meta: dict[str, str]) -> None:
    assert not schema_valid(meta)


# ---------------------------------------------------------------------------
# Parity: schema verdict == receiver verdict
# ---------------------------------------------------------------------------
from health_bridge.contract.batch_v1 import validate_intake_metadata  # noqa: E402


def _mismatch_msg(s: bool, r: bool, meta: dict[str, str]) -> str:
    return (
        f"mismatch for {meta!r}: "
        f"schema={'accepts' if s else 'rejects'}, "
        f"receiver={'accepts' if r else 'rejects'}"
    )


def _check(meta: dict[str, str], expected: bool) -> None:
    s = schema_valid(meta)
    try:
        validate_intake_metadata(meta)
        r = True
    except Exception:  # noqa: BLE001
        r = False
    assert s == r is expected, _mismatch_msg(s, r, meta)


def test_newline_key_intake_id_rejected() -> None:
    """Key 'intake_id\\n' must be rejected by both schema and receiver."""
    _check({"intake_id\n": UUID}, expected=False)


def test_newline_key_intake_component_id_rejected() -> None:
    """Key 'intake_component_id\\n' must be rejected by both."""
    _check({"intake_component_id\n": "water"}, expected=False)


def test_newline_key_next_to_valid_pair_rejected() -> None:
    """A valid pair plus 'intake_id\\n' must be rejected."""
    _check(
        {"intake_id": UUID, "intake_component_id": "c1", "intake_id\n": UUID},
        expected=False,
    )


def test_non_reserved_intake_key_rejected() -> None:
    """Unknown 'intake_idx' must be rejected by both."""
    _check({"intake_idx": "3"}, expected=False)


def test_bare_intake_prefix_rejected() -> None:
    """Bare 'intake_' key must be rejected by both."""
    _check({"intake_": "x"}, expected=False)


def test_valid_pair_accepted_by_both() -> None:
    """A valid full pair must be accepted by both."""
    _check({**GOOD}, expected=True)


def test_uppercase_prefix_not_reserved() -> None:
    """'Intake_id' (uppercase) is not a reserved intake_ key."""
    _check({"Intake_id": "x"}, expected=True)
