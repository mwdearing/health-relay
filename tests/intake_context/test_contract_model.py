"""Tests for the intake-context v1 pydantic contract model and digest calculator."""

# pyright: reportAny=false, reportExplicitAny=false

import copy
import importlib
import json
from pathlib import Path
from typing import Any, TypeAlias, cast, get_args

import pytest

from health_bridge.contract import intake_context_v1 as model
from health_bridge.contract.intake_context_v1 import (
    IntakeContextBatchV1,
    IntakeContextContractError,
    canonical_json,
    digest_mismatches,
    expected_digests,
    validate_batch,
)

JsonObject: TypeAlias = dict[str, Any]

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "intake_context"
reference = importlib.import_module("tests.intake_context.reference")

POSITIVE = (
    "valid_worked_example.json",
    "valid_delete.json",
    "valid_link_projection_seq2.json",
    "valid_proprietary_blend.json",
)
SCENARIOS = (
    "scenario_same_operation_different_content.json",
    "scenario_stale_revision.json",
)
NEGATIVE = {
    "invalid_compound_without_quantity_basis.json": "quantity_basis",
    "invalid_compound_wrong_role.json": "aggregation_role",
    "invalid_delete_with_facts.json": "facts",
    "invalid_duplicate_component_id.json": "duplicate_component_id",
    "invalid_duplicate_link.json": "duplicate_link",
    "invalid_duplicate_object_name.json": "duplicate_object_name",
    "invalid_extra_field.json": "extra_forbidden",
    "invalid_float_amount.json": "float_number",
    "invalid_float_projection_sequence.json": "float_number",
    "invalid_float_revision.json": "float_number",
    "invalid_float_sync_version.json": "float_number",
    "invalid_impossible_timestamp.json": "invalid_timestamp",
    "invalid_leap_second.json": "invalid_timestamp",
    "invalid_link_projection_duplicate_link.json": "duplicate_link",
    "invalid_link_projection_sequence_one.json": "projection_sequence",
    "invalid_link_to_compound.json": "link_to_non_nutrient",
    "invalid_link_type_mismatch.json": "healthkit_type_mismatch",
    "invalid_link_unknown_component.json": "unknown_link_component",
    "invalid_localtime_time_zone.json": "unknown_time_zone",
    "invalid_lone_surrogate.json": "lone_surrogate",
    "invalid_nutrient_blend_total_role.json": "aggregation_role",
    "invalid_nutrient_compound_measurement.json": "aggregation_role",
    "invalid_offset_zone_mismatch.json": "offset_zone_mismatch",
    "invalid_revision_above_int64.json": "revision",
    "invalid_sample_on_two_components.json": "sample_on_multiple_components",
    "invalid_sync_identity_on_two_samples.json": "sync_identity_on_multiple_samples",
    "invalid_unknown_major_version.json": "schema_version",
    "invalid_unknown_time_zone.json": "unknown_time_zone",
    "invalid_unknown_with_amount.json": "amount",
    "invalid_unsupported_minor_version.json": "schema_version",
    "invalid_upsert_projection_sequence_two.json": "projection_sequence",
}

INT64_MAX = 9223372036854775807
UUID_A = "2c932bd1-c46d-4e38-b481-e0d842fdd429"
UUID_B = "9a1f3c57-8e2d-4b60-a7c4-d5e0b1f28396"
WORKED_DOMAIN_HASH = (
    "sha256:93bb96b900c8d22d77630236eb60ec9c453e0627009d9fe01041ea3ef438c4f0"
)
WORKED_PROJECTION_HASH = (
    "sha256:8d5f54713418cd2f525dbf176e4d9e6c73241ded8ac5f9288db6e0179b8f71fd"
)
WORKED_CLIENT_HASH = (
    "sha256:873b7b15148917d14c17c36b074f8e4bb3fd1d0afea2652c8fb5319e54e81003"
)


def _text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _raw(name: str) -> JsonObject:
    return cast("JsonObject", json.loads(_text(name)))


def _worked() -> JsonObject:
    return _raw("valid_worked_example.json")


def _upsert(raw: JsonObject) -> JsonObject:
    return cast("JsonObject", raw["operations"][0])


def _link(
    component_id: str = "water",
    sample: str = UUID_A,
    *,
    sync: str = "intake:x:water",
    disposition: str = "active",
) -> JsonObject:
    return {
        "component_id": component_id,
        "healthkit_sample_uuid": sample,
        "healthkit_type": "HKQuantityTypeIdentifierDietaryWater",
        "sync_identifier": sync,
        "sync_version": 1,
        "disposition": disposition,
    }


def _batches() -> dict[str, str]:
    batches = {name: _text(name) for name in POSITIVE}
    for name in SCENARIOS:
        steps = json.loads(_text(name))["steps"]
        for index, step in enumerate(steps):
            batches[f"{name}#{index}"] = json.dumps(step["batch"])
    return batches


BATCHES = _batches()


def _reject(data: str | bytes | JsonObject) -> str:
    with pytest.raises(IntakeContextContractError) as caught:
        _ = validate_batch(data)
    return str(caught.value)


def _fact_with(**changes: object) -> JsonObject:
    raw = _worked()
    fact = cast("JsonObject", _upsert(raw)["facts"][0])
    for key, value in changes.items():
        if value is None:
            _ = fact.pop(key, None)
        else:
            fact[key] = value
    _upsert(raw)["facts"] = [fact]
    _upsert(raw)["healthkit_links"] = []
    return raw


@pytest.mark.parametrize("name", sorted(BATCHES))
def test_valid_batches_validate_and_round_trip(name: str) -> None:
    text = BATCHES[name]
    batch = validate_batch(text)
    assert isinstance(batch, IntakeContextBatchV1)
    assert batch.model_dump(mode="json", exclude_unset=True) == json.loads(text)
    assert validate_batch(json.loads(text)) == batch
    assert validate_batch(text.encode("utf-8")) == batch


@pytest.mark.parametrize("name", sorted(BATCHES))
def test_digests_match_fixture_and_reference(name: str) -> None:
    raw = cast("JsonObject", json.loads(BATCHES[name]))
    batch = validate_batch(raw)
    computed = expected_digests(batch)
    assert len(computed) == len(raw["operations"])
    for operation, digests in zip(raw["operations"], computed, strict=True):
        assert digests == reference.expected_hashes(raw, operation)
        assert {key: operation[key] for key in digests} == digests
    assert digest_mismatches(batch) == []


def test_worked_example_digest_values() -> None:
    digests = expected_digests(validate_batch(_text("valid_worked_example.json")))[0]
    assert digests == {
        "domain_facts_hash": WORKED_DOMAIN_HASH,
        "projection_hash": WORKED_PROJECTION_HASH,
        "client_payload_hash": WORKED_CLIENT_HASH,
    }


def test_digest_keys_per_operation_type() -> None:
    assert set(expected_digests(validate_batch(_text("valid_delete.json")))[0]) == {
        "domain_facts_hash",
        "client_payload_hash",
    }
    link_batch = validate_batch(_text("valid_link_projection_seq2.json"))
    assert set(expected_digests(link_batch)[0]) == {
        "projection_hash",
        "client_payload_hash",
    }
    assert set(expected_digests(validate_batch(_worked()))[0]) == {
        "domain_facts_hash",
        "projection_hash",
        "client_payload_hash",
    }


def test_fixture_set_is_covered() -> None:
    on_disk = {path.name for path in FIXTURE_DIR.glob("invalid_*.json")}
    assert on_disk == set(NEGATIVE)


@pytest.mark.parametrize(("name", "token"), sorted(NEGATIVE.items()))
def test_invalid_fixtures_are_rejected_for_the_stated_reason(
    name: str, token: str
) -> None:
    assert token in _reject(_text(name))


@pytest.mark.parametrize(
    ("name", "token"),
    [
        (name, token)
        for name, token in sorted(NEGATIVE.items())
        if not name.startswith(
            (
                "invalid_duplicate_object_name",
                "invalid_float_",
                "invalid_lone_surrogate",
            )
        )
    ],
)
def test_invalid_fixtures_are_rejected_as_parsed_dicts(name: str, token: str) -> None:
    assert token in _reject(cast("JsonObject", json.loads(_text(name))))


def test_error_type_is_a_value_error() -> None:
    assert issubclass(IntakeContextContractError, ValueError)
    assert "type=" in _reject(_text("invalid_extra_field.json"))


@pytest.mark.parametrize("text", ["[]", "null", "42", '"text"', ""])
def test_non_object_input_is_rejected(text: str) -> None:
    with pytest.raises(ValueError, match=r"type=\w+"):
        _ = validate_batch(text)


def test_non_utf8_bytes_are_rejected() -> None:
    with pytest.raises(ValueError, match="type="):
        _ = validate_batch(b"\xff\xfe{}")


def test_bytes_input_is_parsed_with_the_json_text_rules() -> None:
    assert "duplicate_object_name" in _reject(
        _text("invalid_duplicate_object_name.json").encode("utf-8")
    )


@pytest.mark.parametrize(
    ("spelling", "token"),
    [
        ("2.0", "float_number"),
        ("2e0", "float_number"),
        ("NaN", "non_finite_number"),
        ("Infinity", "non_finite_number"),
        ("-Infinity", "non_finite_number"),
    ],
)
def test_number_spellings_are_rejected_at_parse_time(spelling: str, token: str) -> None:
    text = json.dumps(_worked()).replace('"revision": 2', f'"revision": {spelling}')
    assert token in _reject(text)


@pytest.mark.parametrize("escape", ["\\ud800", "\\udfff", "\\ude00\\ud83d"])
def test_lone_surrogate_escape_in_a_string_or_name(escape: str) -> None:
    in_value = _text("valid_worked_example.json").replace(
        "Water with creatine", f"Water {escape}"
    )
    assert "lone_surrogate" in _reject(in_value)
    in_name = _text("valid_worked_example.json").replace(
        '"serving"', f'"serving{escape}"'
    )
    assert "lone_surrogate" in _reject(in_name)


def test_paired_surrogate_escape_is_accepted() -> None:
    text = _text("valid_worked_example.json").replace(
        "Water with creatine", "Water \\ud83d\\udca7"
    )
    batch = validate_batch(text)
    assert batch.operations[0].model_dump()["display_name"] == "Water \U0001f4a7"


def test_duplicate_object_name_anywhere() -> None:
    text = _text("valid_worked_example.json").replace(
        '"unit": "mL"\n      },', '"unit": "mL", "unit": "L"\n      },', 1
    )
    assert "duplicate_object_name" in _reject(text)


def test_float_inside_a_parsed_dict_is_rejected() -> None:
    raw = _worked()
    _upsert(raw)["revision"] = 2.0
    assert "float_number" in _reject(raw)


def test_lone_surrogate_inside_a_parsed_dict_is_rejected() -> None:
    raw = _worked()
    _upsert(raw)["display_name"] = "bad \ud800"
    assert "lone_surrogate" in _reject(raw)


@pytest.mark.parametrize("field", ["revision", "projection_sequence"])
def test_bool_is_not_an_integer(field: str) -> None:
    raw = _worked()
    _upsert(raw)[field] = True
    assert field in _reject(raw)


def test_bool_sync_version_is_rejected() -> None:
    raw = _worked()
    _upsert(raw)["healthkit_links"][0]["sync_version"] = True
    assert "sync_version" in _reject(raw)


@pytest.mark.parametrize(
    ("value", "accepted"),
    [(0, False), (1, True), (INT64_MAX, True), (INT64_MAX + 1, False), (-1, False)],
)
def test_integer_bounds(value: int, *, accepted: bool) -> None:
    raw = _worked()
    _upsert(raw)["revision"] = value
    _upsert(raw)["healthkit_links"][0]["sync_version"] = value
    if accepted:
        assert validate_batch(raw).operations[0].model_dump()["revision"] == value
    else:
        assert "revision" in _reject(raw)


@pytest.mark.parametrize("value", ["2", None, [2]])
def test_integers_are_not_coerced(value: object) -> None:
    raw = _worked()
    _upsert(raw)["revision"] = value
    assert "revision" in _reject(raw)


@pytest.mark.parametrize(
    "path",
    ["batch_id", "installation_id", "operation_id", "intake_id", "link"],
)
def test_uppercase_uuid_is_rejected(path: str) -> None:
    raw = _worked()
    if path == "link":
        _upsert(raw)["healthkit_links"][0]["healthkit_sample_uuid"] = UUID_A.upper()
    elif path in {"operation_id", "intake_id"}:
        _upsert(raw)[path] = _upsert(raw)[path].upper()
    else:
        raw[path] = cast("str", raw[path]).upper()
    assert "string_pattern_mismatch" in _reject(raw)


@pytest.mark.parametrize(
    "amount", ["5", "0", "0.5", "10.250", "123456789012345678901234567890.1"]
)
def test_decimal_string_accepted(amount: str) -> None:
    raw = _worked()
    _upsert(raw)["serving"]["amount"] = amount
    assert validate_batch(raw).model_dump(mode="json", exclude_unset=True) == raw


@pytest.mark.parametrize(
    "amount", ["", "05", "-1", "+1", "1.", ".5", "1e3", "1,5", "5\n", " 5", "\uff11"]
)
def test_decimal_string_rejected(amount: str) -> None:
    raw = _worked()
    _upsert(raw)["serving"]["amount"] = amount
    assert "amount" in _reject(raw)


def test_amount_must_be_a_string_never_a_number() -> None:
    raw = _worked()
    _upsert(raw)["serving"]["amount"] = 500
    assert "amount" in _reject(raw)


def test_amount_spelling_is_preserved_exactly() -> None:
    raw = _worked()
    _upsert(raw)["serving"]["amount"] = "500.0"
    dumped = validate_batch(raw).model_dump(mode="json", exclude_unset=True)
    assert dumped["operations"][0]["serving"]["amount"] == "500.0"


def test_enum_values() -> None:
    assert set(get_args(model.NutritionCompleteness)) == {
        "complete",
        "partial",
        "unknown",
    }
    assert set(get_args(model.Provenance)) == {
        "user_confirmed",
        "label_confirmed",
        "ocr_confirmed",
        "catalog_reference",
        "recipe_calculated",
        "estimated",
    }
    assert set(get_args(model.FactKind)) == {"nutrient", "compound", "blend"}
    assert set(get_args(model.ValueState)) == {
        "known",
        "unknown",
        "not_applicable",
        "below_reporting_threshold",
    }
    assert set(get_args(model.AggregationRole)) == {
        "context_only",
        "compound_measurement",
        "blend_total_only",
    }
    assert set(get_args(model.QuantityBasis)) == {
        "compound_mass",
        "active_nutrient_mass",
        "unknown",
    }
    assert set(get_args(model.Disposition)) == {"active", "superseded", "deleted"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nutrition_completeness", "full"),
        ("nutrition_completeness", "COMPLETE"),
        ("schema_version", "1.1"),
        ("schema_version", "1"),
        ("schema_version", "1.0.0"),
        ("schema", "healthrelay.other"),
    ],
)
def test_enum_and_constant_values_are_closed(field: str, value: str) -> None:
    raw = _worked()
    if field == "nutrition_completeness":
        _upsert(raw)[field] = value
    else:
        raw[field] = value
    assert field in _reject(raw)


@pytest.mark.parametrize(
    ("changes", "token"),
    [
        ({"kind": "mixture"}, "kind"),
        ({"value_state": "zero"}, "value_state"),
        ({"provenance": "guess"}, "provenance"),
        ({"aggregation_role": "total"}, "aggregation_role"),
        ({"quantity_basis": "mass"}, "quantity_basis"),
        ({"code": "dietary_water"}, "code"),
        ({"component_id": "Water"}, "component_id"),
    ],
)
def test_fact_enums_and_slugs_are_closed(
    changes: dict[str, object], token: str
) -> None:
    assert token in _reject(_fact_with(**changes))


@pytest.mark.parametrize(
    ("changes", "accepted", "token"),
    [
        ({}, True, ""),
        ({"amount": None}, False, "amount_required"),
        ({"unit": None}, False, "unit_required"),
        ({"value_state": "unknown"}, False, "amount_not_allowed"),
        ({"value_state": "unknown", "amount": None}, False, "unit_not_allowed"),
        ({"value_state": "unknown", "amount": None, "unit": None}, True, ""),
        ({"value_state": "not_applicable"}, False, "amount_not_allowed"),
        (
            {"value_state": "not_applicable", "amount": None, "unit": None},
            True,
            "",
        ),
        ({"value_state": "below_reporting_threshold"}, False, "amount_not_allowed"),
        ({"value_state": "below_reporting_threshold", "amount": None}, True, ""),
        (
            {"value_state": "below_reporting_threshold", "amount": None, "unit": None},
            True,
            "",
        ),
    ],
)
def test_value_state_matrix(
    changes: dict[str, object], *, accepted: bool, token: str
) -> None:
    raw = _fact_with(**changes)
    if accepted:
        assert validate_batch(raw).model_dump(mode="json", exclude_unset=True) == raw
    else:
        assert token in _reject(raw)


def test_unknown_is_never_zero() -> None:
    assert "amount" in _reject(_fact_with(value_state="unknown", amount="0"))


@pytest.mark.parametrize(
    ("changes", "accepted", "token"),
    [
        ({"kind": "compound"}, False, "quantity_basis_required"),
        (
            {
                "kind": "compound",
                "quantity_basis": "unknown",
                "aggregation_role": "compound_measurement",
            },
            True,
            "",
        ),
        (
            {
                "kind": "compound",
                "quantity_basis": "active_nutrient_mass",
                "aggregation_role": "context_only",
            },
            False,
            "aggregation_role_mismatch",
        ),
        ({"aggregation_role": "compound_measurement"}, False, "aggregation_role"),
        ({"aggregation_role": "blend_total_only"}, False, "aggregation_role"),
        ({"quantity_basis": "compound_mass"}, True, ""),
        (
            {
                "kind": "blend",
                "aggregation_role": "blend_total_only",
                "members": [{"label_name": "Proprietary"}],
            },
            True,
            "",
        ),
        (
            {"kind": "blend", "aggregation_role": "blend_total_only"},
            False,
            "members_required",
        ),
        (
            {
                "kind": "blend",
                "aggregation_role": "blend_total_only",
                "members": [],
            },
            False,
            "members",
        ),
        (
            {
                "kind": "blend",
                "aggregation_role": "context_only",
                "members": [{"label_name": "Proprietary"}],
            },
            False,
            "aggregation_role_mismatch",
        ),
        ({"members": [{"label_name": "Proprietary"}]}, False, "members_not_allowed"),
    ],
)
def test_kind_role_basis_and_members_rules(
    changes: dict[str, object], *, accepted: bool, token: str
) -> None:
    raw = _fact_with(**changes)
    if accepted:
        assert validate_batch(raw).model_dump(mode="json", exclude_unset=True) == raw
    else:
        assert token in _reject(raw)


@pytest.mark.parametrize(
    ("member", "accepted"),
    [
        ({"label_name": "A"}, True),
        ({"label_name": "A", "amount": "1", "unit": "g"}, True),
        ({"label_name": "A", "amount": "1"}, False),
        ({"label_name": "A", "unit": "g"}, False),
        ({"amount": "1", "unit": "g"}, False),
        ({"label_name": "A", "amount": 1, "unit": "g"}, False),
        ({"label_name": "A", "split": "1"}, False),
    ],
)
def test_blend_member_rules(member: JsonObject, *, accepted: bool) -> None:
    raw = _fact_with(
        kind="blend", aggregation_role="blend_total_only", members=[member]
    )
    if accepted:
        assert validate_batch(raw) is not None
    else:
        _ = _reject(raw)


def test_explicit_null_is_not_an_omitted_field() -> None:
    raw = _fact_with()
    cast("JsonObject", _upsert(raw)["facts"][0])["label_name"] = None
    assert "label_name" in _reject(raw)


def test_unit_must_be_printable_ascii() -> None:
    for unit in ("m L", "mL\n", "", "é", "x" * 33):
        assert "unit" in _reject(_fact_with(unit=unit))


def test_no_unknown_fields_anywhere() -> None:
    raw = _worked()
    _upsert(raw)["facts"][0]["extra"] = "x"
    assert "extra_forbidden" in _reject(raw)
    raw = _worked()
    raw["account_id"] = "someone"
    assert "extra_forbidden" in _reject(raw)
    raw = _worked()
    _upsert(raw)["healthkit_links"][0]["extra"] = "x"
    assert "extra_forbidden" in _reject(raw)


def test_batch_needs_at_least_one_operation() -> None:
    raw = _worked()
    raw["operations"] = []
    assert "operations" in _reject(raw)


def test_unknown_operation_name_is_rejected() -> None:
    raw = _worked()
    _upsert(raw)["operation"] = "merge"
    assert "operation" in _reject(raw)


def test_models_are_frozen() -> None:
    batch = validate_batch(_worked())
    with pytest.raises(ValueError, match="frozen"):
        batch.producer_id = "other"


def test_json_schema_has_closed_objects_and_no_number_type() -> None:
    def walk(node: object) -> None:
        if isinstance(node, dict):
            fields = cast("dict[str, object]", node)
            if "properties" in fields:
                assert fields.get("additionalProperties") is False
            assert fields.get("type") != "number"
            for value in fields.values():
                walk(value)
        elif isinstance(node, list):
            for value in cast("list[object]", node):
                walk(value)

    walk(IntakeContextBatchV1.model_json_schema())


def test_duplicate_component_id_in_upsert() -> None:
    raw = _worked()
    facts = _upsert(raw)["facts"]
    facts[1]["component_id"] = facts[0]["component_id"]
    assert "duplicate_component_id" in _reject(raw)


def test_link_must_name_a_fact_of_the_same_upsert() -> None:
    raw = _worked()
    _upsert(raw)["healthkit_links"][0]["component_id"] = "missing"
    assert "unknown_link_component" in _reject(raw)


def test_link_must_name_a_nutrient() -> None:
    raw = _worked()
    _upsert(raw)["healthkit_links"][0]["component_id"] = "creatine-monohydrate"
    assert "link_to_non_nutrient" in _reject(raw)


def test_link_projection_does_not_check_components_against_facts() -> None:
    raw = _raw("valid_link_projection_seq2.json")
    raw["operations"][0]["healthkit_links"][0]["component_id"] = "anything"
    assert validate_batch(raw) is not None


def test_duplicate_component_sample_pair_in_one_array() -> None:
    raw = _worked()
    links = _upsert(raw)["healthkit_links"]
    links.append(copy.deepcopy(links[0]) | {"sync_version": 9})
    assert "duplicate_link" in _reject(raw)


def test_same_sample_on_one_component_with_different_pairs_is_allowed() -> None:
    raw = _worked()
    links = _upsert(raw)["healthkit_links"]
    links.append(
        copy.deepcopy(links[0])
        | {"healthkit_sample_uuid": UUID_B, "disposition": "superseded"}
    )
    assert validate_batch(raw) is not None


def test_sample_active_on_two_components() -> None:
    raw = _worked()
    operation = _upsert(raw)
    operation["facts"].append(
        operation["facts"][0] | {"component_id": "water-two", "amount": "1"}
    )
    operation["healthkit_links"].append(
        _link("water-two", UUID_A, sync="intake:x:water-two")
    )
    assert "sample_on_multiple_components" in _reject(raw)


def test_inactive_sample_may_follow_a_history_across_components() -> None:
    base = _worked()
    base_op = _upsert(base)
    base_op["facts"].append(
        base_op["facts"][0] | {"component_id": "water-two", "amount": "1"}
    )
    base_op["healthkit_links"].append(
        _link("water-two", UUID_A, sync="intake:x:water-two", disposition="superseded")
    )
    assert validate_batch(base) is not None


def test_sync_identity_active_on_two_samples() -> None:
    raw = _worked()
    links = _upsert(raw)["healthkit_links"]
    links.append(copy.deepcopy(links[0]) | {"healthkit_sample_uuid": UUID_B})
    assert "sync_identity_on_multiple_samples" in _reject(raw)


def test_sync_identity_superseded_beside_active_is_allowed() -> None:
    raw = _worked()
    links = _upsert(raw)["healthkit_links"]
    links.append(
        copy.deepcopy(links[0])
        | {"healthkit_sample_uuid": UUID_B, "disposition": "superseded"}
    )
    assert validate_batch(raw) is not None


@pytest.mark.parametrize(
    ("code", "healthkit_type"),
    [
        ("hydration", "HKQuantityTypeIdentifierDietaryWater"),
        ("dietary_caffeine", "HKQuantityTypeIdentifierDietaryCaffeine"),
        ("dietary_vitamin_b6", "HKQuantityTypeIdentifierDietaryVitaminB6"),
        ("dietary_energy_consumed", "HKQuantityTypeIdentifierDietaryEnergyConsumed"),
    ],
)
def test_link_type_follows_the_fact_code(code: str, healthkit_type: str) -> None:
    raw = _worked()
    operation = _upsert(raw)
    operation["facts"][0]["code"] = code
    operation["healthkit_links"][0]["healthkit_type"] = healthkit_type
    assert validate_batch(raw) is not None
    operation["healthkit_links"][0]["healthkit_type"] += "X"
    assert "healthkit_type_mismatch" in _reject(raw)


def test_link_type_for_a_non_catalog_nutrient_code_is_a_mismatch() -> None:
    raw = _worked()
    _upsert(raw)["facts"][0]["code"] = "caffeine"
    assert "healthkit_type_mismatch" in _reject(raw)


@pytest.mark.parametrize(
    "zone", ["America/Chicago", "UTC", "Europe/Paris", "Asia/Kolkata"]
)
def test_real_time_zone_names_are_accepted(zone: str) -> None:
    raw = _worked()
    operation = _upsert(raw)
    operation["time_zone"] = zone
    operation["occurred_at"] = (
        "2026-09-30T17:30:00Z" if zone == "UTC" else operation["occurred_at"]
    )
    if zone in {"UTC", "America/Chicago"}:
        assert validate_batch(raw) is not None
    else:
        assert "offset_zone_mismatch" in _reject(raw)


@pytest.mark.parametrize(
    "zone",
    [
        "localtime",
        "posixrules",
        "Factory",
        "posix/America/Chicago",
        "right/America/Chicago",
        "Mars/Olympus",
        "america/chicago",
        "Chicago",
    ],
)
def test_pseudo_and_unknown_time_zones_are_rejected(zone: str) -> None:
    raw = _worked()
    _upsert(raw)["time_zone"] = zone
    assert "unknown_time_zone" in _reject(raw)


@pytest.mark.parametrize("zone", ["../etc/passwd", "", "America/Chicago\n", "/UTC"])
def test_malformed_time_zone_names_are_rejected(zone: str) -> None:
    raw = _worked()
    _upsert(raw)["time_zone"] = zone
    assert "time_zone" in _reject(raw)


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-02-30T12:00:00Z",
        "2026-13-01T12:00:00Z",
        "2026-09-30T24:00:00Z",
        "2026-09-30T12:60:00Z",
        "2026-09-30T23:59:60Z",
        "2026-09-30T12:00:00+24:00",
        "2026-09-30T12:00:00+05:60",
        "0000-01-01T00:00:00Z",
    ],
)
@pytest.mark.parametrize("field", ["occurred_at", "recorded_at"])
def test_unreal_timestamps_are_rejected(field: str, stamp: str) -> None:
    raw = _worked()
    _upsert(raw)[field] = stamp
    assert "invalid_timestamp" in _reject(raw)


def test_delete_timestamp_is_checked_too() -> None:
    raw = _raw("valid_delete.json")
    raw["operations"][0]["deleted_at"] = "2026-02-29T00:00:00Z"
    assert "invalid_timestamp" in _reject(raw)


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-09-30T12:30:00-05:00",
        "2026-09-30T12:30:00.123456789-05:00",
        "2028-02-29T00:00:00Z",
    ],
)
def test_real_timestamps_are_accepted_for_recorded_at(stamp: str) -> None:
    raw = _worked()
    _upsert(raw)["recorded_at"] = stamp
    assert validate_batch(raw) is not None


def test_timestamp_needs_an_explicit_offset() -> None:
    raw = _worked()
    _upsert(raw)["recorded_at"] = "2026-09-30T17:31:02"
    assert "recorded_at" in _reject(raw)


def test_offset_must_match_the_zone_at_that_instant() -> None:
    raw = _worked()
    _upsert(raw)["occurred_at"] = "2026-01-15T12:30:00-05:00"
    assert "offset_zone_mismatch" in _reject(raw)
    _upsert(raw)["occurred_at"] = "2026-01-15T12:30:00-06:00"
    assert validate_batch(raw) is not None


def test_equivalent_instant_with_a_different_offset_is_still_a_mismatch() -> None:
    raw = _worked()
    _upsert(raw)["occurred_at"] = "2026-09-30T17:30:00Z"
    assert "offset_zone_mismatch" in _reject(raw)


def test_canonical_json_spec_example() -> None:
    value = {"b": [2, 1], "a": {"d": "x", "c": None}}
    assert canonical_json(value) == b'{"a":{"c":null,"d":"x"},"b":[2,1]}'


def test_canonical_json_writes_raw_utf8() -> None:
    text = "é\u007f\u2028\U0001f4a7"
    assert canonical_json({"k": text}) == f'{{"k":"{text}"}}'.encode()
    assert canonical_json({"é": 1}) == '{"é":1}'.encode()


def test_canonical_json_escapes_only_what_json_requires() -> None:
    assert canonical_json({"k": "\x01\n"}) == b'{"k":"\\u0001\\n"}'
    assert canonical_json({"k": '\\"\b\f\n\r\t'}) == b'{"k":"\\\\\\"\\b\\f\\n\\r\\t"}'
    assert canonical_json({"k": "\x1f\x00"}) == b'{"k":"\\u001f\\u0000"}'
    assert canonical_json({"k": "/<>&'"}) == b'{"k":"/<>&\'"}'


def test_canonical_json_sorts_keys_by_code_point_at_every_depth() -> None:
    value = {"b": 1, "B": {"z": 1, "a": 2}, "é": 3, "\U0001f4a7": 4, "\uffff": 5}
    assert canonical_json(value) == (
        '{"B":{"a":2,"z":1},"b":1,"é":3,"\uffff":5,"\U0001f4a7":4}'.encode()
    )


def test_canonical_json_scalars_and_arrays() -> None:
    assert canonical_json([True, False, None, 0, -7, "1"]) == (
        b'[true,false,null,0,-7,"1"]'
    )
    assert canonical_json([[2, 1], {"b": 1}]) == b'[[2,1],{"b":1}]'


@pytest.mark.parametrize(
    ("value", "token"),
    [
        (1.5, "float_number"),
        ({"a": [2.0]}, "float_number"),
        (float("nan"), "float_number"),
        ("\ud800", "lone_surrogate"),
        ({"\udc00": 1}, "lone_surrogate"),
    ],
)
def test_canonical_json_rejects_floats_and_lone_surrogates(
    value: object, token: str
) -> None:
    with pytest.raises(IntakeContextContractError, match=token):
        _ = canonical_json(value)


def test_canonical_json_matches_the_reference() -> None:
    raw = _worked()
    assert canonical_json(raw) == reference.canonical_json(raw)


def test_projection_hash_ignores_link_order() -> None:
    raw = _raw("valid_link_projection_seq2.json")
    flipped = copy.deepcopy(raw)
    flipped["operations"][0]["healthkit_links"].reverse()
    first = expected_digests(validate_batch(raw))[0]
    second = expected_digests(validate_batch(flipped))[0]
    assert first["projection_hash"] == second["projection_hash"]
    assert first["client_payload_hash"] != second["client_payload_hash"]


def test_projection_hash_depends_on_the_links_and_the_sequence() -> None:
    raw = _raw("valid_link_projection_seq2.json")
    base = expected_digests(validate_batch(raw))[0]["projection_hash"]
    changed = copy.deepcopy(raw)
    changed["operations"][0]["healthkit_links"][0]["sync_version"] = 4
    assert expected_digests(validate_batch(changed))[0]["projection_hash"] != base
    later = copy.deepcopy(raw)
    later["operations"][0]["projection_sequence"] = 3
    assert expected_digests(validate_batch(later))[0]["projection_hash"] != base


def test_domain_facts_hash_ignores_installation_id_and_links() -> None:
    raw = _worked()
    other = copy.deepcopy(raw)
    other["installation_id"] = "11111111-2222-4333-8444-555555555555"
    _upsert(other)["healthkit_links"] = []
    _upsert(other)["operation_id"] = "11111111-2222-4333-8444-666666666666"
    first = expected_digests(validate_batch(raw))[0]
    second = expected_digests(validate_batch(other))[0]
    assert first["domain_facts_hash"] == second["domain_facts_hash"]
    assert first["projection_hash"] != second["projection_hash"]
    assert first["client_payload_hash"] != second["client_payload_hash"]


def test_domain_facts_hash_depends_on_the_producer() -> None:
    raw = _worked()
    other = copy.deepcopy(raw)
    other["producer_id"] = "another-app"
    first = expected_digests(validate_batch(raw))[0]
    second = expected_digests(validate_batch(other))[0]
    assert first["domain_facts_hash"] != second["domain_facts_hash"]
    assert first["projection_hash"] != second["projection_hash"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("writer_bundle_id", "com.example.other"),
        ("installation_id", "11111111-2222-4333-8444-555555555555"),
    ],
)
def test_only_client_payload_hash_changes_with_writer_or_installation(
    field: str, value: str
) -> None:
    raw = _worked()
    other = copy.deepcopy(raw)
    other[field] = value
    first = expected_digests(validate_batch(raw))[0]
    second = expected_digests(validate_batch(other))[0]
    assert first["client_payload_hash"] != second["client_payload_hash"]
    assert first["domain_facts_hash"] == second["domain_facts_hash"]
    assert first["projection_hash"] == second["projection_hash"]


def test_client_payload_hash_covers_the_digests_as_sent() -> None:
    raw = _worked()
    other = copy.deepcopy(raw)
    _upsert(other)["domain_facts_hash"] = "sha256:" + "0" * 64
    first = expected_digests(validate_batch(raw))[0]
    second = expected_digests(validate_batch(other))[0]
    assert first["domain_facts_hash"] == second["domain_facts_hash"]
    assert first["client_payload_hash"] != second["client_payload_hash"]


def test_digest_mismatches_for_a_tampered_display_name() -> None:
    raw = _worked()
    operation = _upsert(raw)
    operation["display_name"] += "x"
    mismatches = digest_mismatches(validate_batch(raw))
    assert (operation["operation_id"], "domain_facts_hash") in mismatches
    assert (operation["operation_id"], "client_payload_hash") in mismatches
    assert all(field != "projection_hash" for _, field in mismatches)


def test_digest_mismatches_for_a_tampered_link() -> None:
    raw = _raw("valid_link_projection_seq2.json")
    operation = raw["operations"][0]
    operation["healthkit_links"][0]["sync_version"] = 99
    mismatches = digest_mismatches(validate_batch(raw))
    assert (operation["operation_id"], "projection_hash") in mismatches
    assert (operation["operation_id"], "client_payload_hash") in mismatches
    assert len(mismatches) == 2


def test_digest_mismatches_for_a_tampered_digest_field() -> None:
    raw = _raw("valid_delete.json")
    operation = raw["operations"][0]
    operation["domain_facts_hash"] = "sha256:" + "f" * 64
    mismatches = digest_mismatches(validate_batch(raw))
    assert mismatches == [
        (operation["operation_id"], "domain_facts_hash"),
        (operation["operation_id"], "client_payload_hash"),
    ]


def test_digest_mismatches_for_a_changed_batch_writer() -> None:
    raw = _worked()
    raw["writer_bundle_id"] = "com.example.other"
    assert digest_mismatches(validate_batch(raw)) == [
        (_upsert(raw)["operation_id"], "client_payload_hash")
    ]


def test_digest_shape_is_enforced() -> None:
    for bad in ("sha256:" + "A" * 64, "sha256:abc", "md5:" + "0" * 32, "TBD"):
        raw = _worked()
        _upsert(raw)["client_payload_hash"] = bad
        assert "client_payload_hash" in _reject(raw)
