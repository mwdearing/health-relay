"""Contract tests for the intake-context v1 schema, fixtures and hashing."""

import copy
import json
import re
from collections.abc import Callable, Iterable, Iterator
from typing import TypeAlias, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from tests.intake_context import reference
from tests.intake_context.reference import (
    FIXTURE_DIR,
    SCHEMA_PATH,
    ContractError,
    canonical_json,
    digest,
    expected_hashes,
    load_json,
    semantic_errors,
)

JsonObject: TypeAlias = dict[str, object]

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
    "invalid_unknown_major_version.json": "schema_version",
    "invalid_float_amount.json": "amount",
    "invalid_extra_field.json": "additionalProperties",
    "invalid_delete_with_facts.json": "facts",
    "invalid_unknown_with_amount.json": "amount",
    "invalid_unsupported_minor_version.json": "schema_version",
    "invalid_upsert_projection_sequence_two.json": "projection_sequence",
    "invalid_compound_without_quantity_basis.json": "quantity_basis",
    "invalid_compound_wrong_role.json": "aggregation_role",
    "invalid_nutrient_compound_measurement.json": "aggregation_role",
    "invalid_revision_above_int64.json": "maximum",
    "invalid_link_projection_sequence_one.json": "projection_sequence",
}
SEMANTIC_NEGATIVE = {
    "invalid_duplicate_component_id.json": "duplicate_component_id",
    "invalid_duplicate_link.json": "duplicate_link",
    "invalid_link_projection_duplicate_link.json": "duplicate_link",
    "invalid_link_unknown_component.json": "unknown_link_component",
    "invalid_unknown_time_zone.json": "unknown_time_zone",
    "invalid_impossible_timestamp.json": "invalid_timestamp",
    "invalid_localtime_time_zone.json": "unknown_time_zone",
    "invalid_leap_second.json": "invalid_timestamp",
    "invalid_link_to_compound.json": "link_to_non_nutrient",
    "invalid_sample_on_two_components.json": "sample_on_multiple_components",
    "invalid_link_type_mismatch.json": "healthkit_type_mismatch",
    "invalid_offset_zone_mismatch.json": "offset_zone_mismatch",
}
UNPARSEABLE_NEGATIVE = {
    "invalid_duplicate_object_name.json": "duplicate object name",
    "invalid_float_revision.json": "float",
    "invalid_float_projection_sequence.json": "float",
    "invalid_float_sync_version.json": "float",
    "invalid_lone_surrogate.json": "lone_surrogate",
}
RESULTS = {
    "accepted",
    "duplicate",
    "stale_revision",
    "domain_conflict",
    "projection_conflict",
    "retryable_failure",
    "permanent_failure",
}


def _load(name: str) -> JsonObject:
    text = (FIXTURE_DIR / name).read_text(encoding="utf-8")
    return cast("JsonObject", load_json(text))


def _schema() -> JsonObject:
    return cast("JsonObject", json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _validator() -> Draft202012Validator:
    return Draft202012Validator(_schema())


def _flatten(error: ValidationError) -> Iterator[ValidationError]:
    yield error
    for child in error.context or ():
        yield from _flatten(child)


def _errors(batch: JsonObject) -> list[ValidationError]:
    iter_errors = cast(
        "Callable[[JsonObject], Iterable[ValidationError]]",
        _validator().iter_errors,
    )
    found = iter_errors(batch)
    return [leaf for error in found for leaf in _flatten(error)]


def _operations(batch: JsonObject) -> list[JsonObject]:
    return cast("list[JsonObject]", batch["operations"])


def _first_fact(batch: JsonObject) -> JsonObject:
    return cast("list[JsonObject]", _operations(batch)[0]["facts"])[0]


def _example() -> JsonObject:
    return copy.deepcopy(_load("valid_worked_example.json"))


def _fails_on(batch: JsonObject, key: str) -> bool:
    return any(
        error.validator == key
        or key in [str(part) for part in error.absolute_path]
        or key in error.message
        for error in _errors(batch)
    )


def test_schema_is_draft_2020_12_and_well_formed() -> None:
    schema = _schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_schema_id_is_not_a_live_url() -> None:
    schema_id = cast("str", _schema()["$id"])

    assert not schema_id.startswith(("http://", "https://"))


def _objects_missing_closed_properties(node: object, path: str) -> Iterator[str]:
    if isinstance(node, dict):
        mapping = cast("JsonObject", node)
        closed = mapping.get("additionalProperties") is False
        if (
            mapping.get("type") == "object"
            and not closed
            and path != "/$defs/operation"
        ):
            yield path
        for key, value in mapping.items():
            yield from _objects_missing_closed_properties(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(cast("list[object]", node)):
            yield from _objects_missing_closed_properties(value, f"{path}/{index}")


def test_every_object_in_the_schema_rejects_unknown_properties() -> None:
    open_objects = list(_objects_missing_closed_properties(_schema(), ""))

    assert open_objects == []


@pytest.mark.parametrize("name", POSITIVE)
def test_positive_fixture_validates(name: str) -> None:
    assert _errors(_load(name)) == []


@pytest.mark.parametrize("name", POSITIVE)
def test_positive_fixture_passes_the_semantic_rules(name: str) -> None:
    assert semantic_errors(_load(name)) == []


@pytest.mark.parametrize("name", POSITIVE)
def test_positive_fixture_hashes_are_the_reference_hashes(name: str) -> None:
    batch = _load(name)

    for operation in _operations(batch):
        for key, value in expected_hashes(batch, operation).items():
            assert operation[key] == value, f"{name} {key}"


@pytest.mark.parametrize("name", [*POSITIVE, *SCENARIOS])
def test_fixtures_carry_no_placeholder_digests(name: str) -> None:
    assert "<" not in (FIXTURE_DIR / name).read_text(encoding="utf-8")


def test_positive_fixtures_cover_every_operation_kind() -> None:
    kinds = {
        operation["operation"]
        for name in POSITIVE
        for operation in _operations(_load(name))
    }

    assert kinds == {"upsert", "delete", "link_projection"}


def test_worked_example_matches_the_documented_shape() -> None:
    operation = _operations(_load("valid_worked_example.json"))[0]
    facts = cast("list[JsonObject]", operation["facts"])

    assert operation["revision"] == 2
    assert operation["projection_sequence"] == 1
    assert [fact["code"] for fact in facts] == ["hydration", "creatine_monohydrate"]


def test_link_projection_fixture_is_sequence_two_without_facts() -> None:
    operation = _operations(_load("valid_link_projection_seq2.json"))[0]

    assert operation["operation"] == "link_projection"
    assert operation["projection_sequence"] == 2
    assert "facts" not in operation
    assert operation["healthkit_links"]


def test_delete_fixture_carries_no_food_details() -> None:
    operation = _operations(_load("valid_delete.json"))[0]

    assert operation["operation"] == "delete"
    assert set(operation) == {
        "operation_id",
        "operation",
        "intake_id",
        "revision",
        "deleted_at",
        "domain_facts_hash",
        "client_payload_hash",
    }


def test_blend_fixture_never_invents_member_amounts() -> None:
    operation = _operations(_load("valid_proprietary_blend.json"))[0]
    facts = cast("list[JsonObject]", operation["facts"])
    blends = [fact for fact in facts if fact["kind"] == "blend"]

    assert blends
    assert blends[0]["aggregation_role"] == "blend_total_only"
    for member in cast("list[JsonObject]", blends[0]["members"]):
        assert "amount" not in member
        assert "unit" not in member


@pytest.mark.parametrize(("name", "key"), NEGATIVE.items())
def test_negative_fixture_fails_for_the_stated_reason(name: str, key: str) -> None:
    batch = _load(name)

    assert _errors(batch)
    assert _fails_on(batch, key)


@pytest.mark.parametrize(("name", "code"), SEMANTIC_NEGATIVE.items())
def test_semantic_negative_fixture_is_schema_valid_but_breaks_a_rule(
    name: str, code: str
) -> None:
    batch = _load(name)

    assert _errors(batch) == []
    assert any(error.startswith(code) for error in semantic_errors(batch))


@pytest.mark.parametrize(("name", "reason"), UNPARSEABLE_NEGATIVE.items())
def test_unparseable_negative_fixture_is_rejected_by_the_loader(
    name: str, reason: str
) -> None:
    text = (FIXTURE_DIR / name).read_text(encoding="utf-8")

    with pytest.raises(ContractError, match=reason):
        _ = load_json(text)


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario_shape_and_batches(name: str) -> None:
    scenario = _load(name)
    steps = cast("list[JsonObject]", scenario["steps"])

    assert isinstance(scenario["description"], str)
    assert len(steps) >= 2
    for step in steps:
        batch = cast("JsonObject", step["batch"])
        expect = cast("list[JsonObject]", step["expect"])
        assert _errors(batch) == []
        assert semantic_errors(batch) == []
        assert len(expect) == len(_operations(batch))
        for operation, entry in zip(_operations(batch), expect, strict=True):
            assert entry["operation_id"] == operation["operation_id"]
            assert entry["result"] in RESULTS
            for key, value in expected_hashes(batch, operation).items():
                assert operation[key] == value


def test_same_operation_id_scenario_expects_a_conflict_not_a_duplicate() -> None:
    steps = cast(
        "list[JsonObject]",
        _load("scenario_same_operation_different_content.json")["steps"],
    )
    sent = [
        operation
        for step in steps
        for operation in _operations(cast("JsonObject", step["batch"]))
    ]
    results = [cast("list[JsonObject]", step["expect"])[0]["result"] for step in steps]

    assert sent[0]["operation_id"] == sent[1]["operation_id"]
    assert sent[0]["client_payload_hash"] != sent[1]["client_payload_hash"]
    assert results[0] == "accepted"
    assert results[1] == "domain_conflict"


def test_stale_revision_scenario_delivers_an_older_revision_last() -> None:
    steps = cast("list[JsonObject]", _load("scenario_stale_revision.json")["steps"])
    revisions = [
        _operations(cast("JsonObject", step["batch"]))[0]["revision"] for step in steps
    ]
    results = [cast("list[JsonObject]", step["expect"])[0]["result"] for step in steps]

    assert revisions == [3, 2]
    assert results == ["accepted", "stale_revision"]


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    assert canonical_json({"b": [2, 1], "a": {"d": "x", "c": None}}) == (
        b'{"a":{"c":null,"d":"x"},"b":[2,1]}'
    )


def test_canonical_json_keeps_utf8_and_only_mandatory_escapes() -> None:
    value = {"k": 'caf\u00e9 "\u00b5"\n\\ \u001f \U0001f600 \u007f'}

    assert (
        canonical_json(value)
        == ('{"k":"caf\u00e9 \\"\u00b5\\"\\n\\\\ \\u001f \U0001f600 \u007f"}').encode()
    )


def test_canonical_json_orders_keys_by_code_point() -> None:
    assert canonical_json({"\u00e9": 1, "z": 2, "Z": 3}) == (
        '{"Z":3,"z":2,"\u00e9":1}'.encode()
    )


def test_canonical_json_refuses_floats_and_nan() -> None:
    with pytest.raises(ValueError, match="float"):
        _ = canonical_json({"amount": 1.5})
    with pytest.raises(ValueError, match="float"):
        _ = canonical_json({"amount": float("nan")})


def test_digest_format_is_sha256_prefix_and_lowercase_hex() -> None:
    assert digest({}) == (
        "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest({"a": 1}))


def test_installation_id_is_provenance_only() -> None:
    batch = _example()
    other = copy.deepcopy(batch)
    other["installation_id"] = "11111111-2222-4333-8444-555555555555"

    first = expected_hashes(batch, _operations(batch)[0])
    second = expected_hashes(other, _operations(other)[0])

    assert first["domain_facts_hash"] == second["domain_facts_hash"]
    assert first["projection_hash"] == second["projection_hash"]
    assert first["client_payload_hash"] != second["client_payload_hash"]


def test_producer_scope_changes_every_hash() -> None:
    batch = _example()
    other = copy.deepcopy(batch)
    other["producer_id"] = "another-producer"

    first = expected_hashes(batch, _operations(batch)[0])
    second = expected_hashes(other, _operations(other)[0])

    assert all(first[key] != second[key] for key in first)


def test_link_changes_move_the_projection_hash_but_not_the_facts_hash() -> None:
    batch = _example()
    operation = _operations(batch)[0]
    before = expected_hashes(batch, operation)
    links = cast("list[JsonObject]", operation["healthkit_links"])
    links[0]["disposition"] = "superseded"
    operation["projection_sequence"] = 2
    after = expected_hashes(batch, operation)

    assert before["domain_facts_hash"] == after["domain_facts_hash"]
    assert before["projection_hash"] != after["projection_hash"]


def test_projection_hash_ignores_link_order() -> None:
    batch = _example()
    operation = _operations(batch)[0]
    links = cast("list[JsonObject]", operation["healthkit_links"])
    second = copy.deepcopy(links[0])
    second["component_id"] = "zinc"
    second["healthkit_sample_uuid"] = "3d043ce2-d57f-4f59-91f3-e9f1a53e540a"
    operation["healthkit_links"] = [links[0], second]
    forward = expected_hashes(batch, operation)["projection_hash"]
    operation["healthkit_links"] = [second, links[0]]
    backward = expected_hashes(batch, operation)["projection_hash"]

    assert forward == backward


def test_fact_order_is_significant_to_the_facts_hash() -> None:
    batch = _example()
    operation = _operations(batch)[0]
    before = expected_hashes(batch, operation)["domain_facts_hash"]
    facts = cast("list[JsonObject]", operation["facts"])
    operation["facts"] = list(reversed(facts))

    assert expected_hashes(batch, operation)["domain_facts_hash"] != before


def test_delete_has_no_projection_hash_and_link_projection_no_facts_hash() -> None:
    delete = _load("valid_delete.json")
    link = _load("valid_link_projection_seq2.json")

    assert set(expected_hashes(delete, _operations(delete)[0])) == {
        "domain_facts_hash",
        "client_payload_hash",
    }
    assert set(expected_hashes(link, _operations(link)[0])) == {
        "projection_hash",
        "client_payload_hash",
    }


def test_the_supported_minor_is_accepted() -> None:
    batch = _example()
    batch["schema_version"] = "1.0"

    assert _errors(batch) == []


@pytest.mark.parametrize(
    "version", ["0.9", "1.1", "1.7", "1.12", "2.0", "1", "1.0.0", "1.", "01.0", "v1.0"]
)
def test_other_schema_versions_are_rejected(version: str) -> None:
    batch = _example()
    batch["schema_version"] = version

    assert _fails_on(batch, "schema_version")


def test_wrong_schema_name_is_rejected() -> None:
    batch = _example()
    batch["schema"] = "health_bridge.batch.v1"

    assert _fails_on(batch, "schema")


def test_unknown_operation_is_rejected() -> None:
    batch = _example()
    _operations(batch)[0]["operation"] = "merge"

    assert _errors(batch)


def test_batch_needs_at_least_one_operation() -> None:
    batch = _example()
    batch["operations"] = []

    assert _fails_on(batch, "operations")


def test_upsert_needs_a_non_empty_facts_array() -> None:
    batch = _example()
    _operations(batch)[0]["facts"] = []

    assert _fails_on(batch, "facts")


@pytest.mark.parametrize(
    "field",
    [
        "operation_id",
        "intake_id",
        "revision",
        "projection_sequence",
        "occurred_at",
        "time_zone",
        "recorded_at",
        "category",
        "display_name",
        "serving",
        "facts",
        "healthkit_links",
        "nutrition_completeness",
        "domain_facts_hash",
        "projection_hash",
        "client_payload_hash",
    ],
)
def test_every_upsert_field_is_required(field: str) -> None:
    batch = _example()
    del _operations(batch)[0][field]

    assert _fails_on(batch, field)


@pytest.mark.parametrize("field", ["revision", "projection_sequence"])
@pytest.mark.parametrize("value", [0, -1, 1.5, "2"])
def test_revision_and_sequence_are_positive_integers(field: str, value: object) -> None:
    batch = _example()
    _operations(batch)[0][field] = value

    assert _fails_on(batch, field)


@pytest.mark.parametrize("value", [2.5, "2", None, 0])
def test_sync_version_must_be_an_integer(value: object) -> None:
    batch = _example()
    links = cast("list[JsonObject]", _operations(batch)[0]["healthkit_links"])
    links[0]["sync_version"] = value

    assert _fails_on(batch, "sync_version")


@pytest.mark.parametrize("amount", ["5.", ".5", "05", "-1", "1e3", "1,5", " 5", "NaN"])
def test_amounts_must_be_plain_decimal_strings(amount: str) -> None:
    batch = _example()
    _first_fact(batch)["amount"] = amount

    assert _fails_on(batch, "amount")


@pytest.mark.parametrize("amount", ["0", "5", "0.25", "10.50", "1200"])
def test_plain_decimal_strings_are_accepted(amount: str) -> None:
    batch = _example()
    _first_fact(batch)["amount"] = amount

    assert _errors(batch) == []


@pytest.mark.parametrize("state", ["unknown", "not_applicable"])
def test_unknown_and_not_applicable_forbid_amount_and_unit(state: str) -> None:
    batch = _example()
    fact = _first_fact(batch)
    fact["value_state"] = state

    assert _fails_on(batch, "amount")
    del fact["amount"]
    assert _errors(batch)
    del fact["unit"]
    assert _errors(batch) == []


def test_known_requires_amount_and_unit() -> None:
    for key in ("amount", "unit"):
        batch = _example()
        del _first_fact(batch)[key]

        assert _fails_on(batch, key)


def test_below_reporting_threshold_forbids_amount() -> None:
    batch = _example()
    fact = _first_fact(batch)
    fact["value_state"] = "below_reporting_threshold"

    assert _fails_on(batch, "amount")
    del fact["amount"]
    assert _errors(batch) == []


def test_unknown_value_state_is_rejected() -> None:
    batch = _example()
    _first_fact(batch)["value_state"] = "zero"

    assert _fails_on(batch, "value_state")


def test_water_code_cannot_be_dietary_water() -> None:
    batch = _example()
    _first_fact(batch)["code"] = "dietary_water"

    assert _fails_on(batch, "code")


def test_delete_requires_deleted_at_and_hashes() -> None:
    for field in ("deleted_at", "domain_facts_hash", "client_payload_hash"):
        batch = copy.deepcopy(_load("valid_delete.json"))
        del _operations(batch)[0][field]

        assert _fails_on(batch, field)


def test_delete_rejects_links_and_projection_fields() -> None:
    extras: tuple[tuple[str, object], ...] = (
        ("healthkit_links", []),
        ("projection_sequence", 1),
        ("projection_hash", "sha256:" + "0" * 64),
    )
    for field, value in extras:
        batch = copy.deepcopy(_load("valid_delete.json"))
        _operations(batch)[0][field] = value

        assert _fails_on(batch, field)


def test_link_projection_rejects_facts_and_domain_hash() -> None:
    extras: tuple[tuple[str, object], ...] = (
        ("facts", []),
        ("domain_facts_hash", "sha256:" + "0" * 64),
        ("display_name", "Water"),
    )
    for field, value in extras:
        batch = copy.deepcopy(_load("valid_link_projection_seq2.json"))
        _operations(batch)[0][field] = value

        assert _fails_on(batch, field)


def test_link_projection_may_clear_every_link() -> None:
    batch = copy.deepcopy(_load("valid_link_projection_seq2.json"))
    _operations(batch)[0]["healthkit_links"] = []

    assert _errors(batch) == []


@pytest.mark.parametrize("disposition", ["active", "superseded", "deleted"])
def test_link_dispositions_are_accepted(disposition: str) -> None:
    batch = _example()
    links = cast("list[JsonObject]", _operations(batch)[0]["healthkit_links"])
    links[0]["disposition"] = disposition

    assert _errors(batch) == []


def test_unknown_link_disposition_is_rejected() -> None:
    batch = _example()
    links = cast("list[JsonObject]", _operations(batch)[0]["healthkit_links"])
    links[0]["disposition"] = "pending"

    assert _fails_on(batch, "disposition")


@pytest.mark.parametrize("digest_value", ["sha256:<digest>", "sha256:ABC", "md5:00"])
def test_hash_fields_must_be_lowercase_sha256_digests(digest_value: str) -> None:
    batch = _example()
    _operations(batch)[0]["client_payload_hash"] = digest_value

    assert _fails_on(batch, "client_payload_hash")


def test_identifiers_must_be_lowercase_uuids() -> None:
    batch = _example()
    _operations(batch)[0]["intake_id"] = "E6677963-418C-4027-B563-551D8A531EED"

    assert _fails_on(batch, "intake_id")


def test_timestamps_must_be_rfc_3339_date_times() -> None:
    batch = _example()
    _operations(batch)[0]["occurred_at"] = "2026-09-30 12:30:00"

    assert _fails_on(batch, "occurred_at")


def test_blend_members_reject_unknown_properties() -> None:
    batch = _load("valid_proprietary_blend.json")
    fact = next(
        item
        for item in cast("list[JsonObject]", _operations(batch)[0]["facts"])
        if item["kind"] == "blend"
    )
    members = cast("list[JsonObject]", fact["members"])
    members[0]["share"] = "0.5"

    assert _fails_on(batch, "additionalProperties")


def test_members_belong_to_blends_only() -> None:
    batch = _example()
    _first_fact(batch)["members"] = [{"label_name": "Extract"}]

    assert _fails_on(batch, "members")


def test_blend_requires_members() -> None:
    batch = copy.deepcopy(_load("valid_proprietary_blend.json"))
    fact = next(
        item
        for item in cast("list[JsonObject]", _operations(batch)[0]["facts"])
        if item["kind"] == "blend"
    )
    del fact["members"]

    assert _fails_on(batch, "members")


def test_upsert_projection_sequence_is_exactly_one() -> None:
    batch = _example()
    _operations(batch)[0]["projection_sequence"] = 2

    assert _fails_on(batch, "projection_sequence")


def test_link_projection_sequence_may_exceed_one() -> None:
    operation = _operations(_load("valid_link_projection_seq2.json"))[0]

    assert operation["projection_sequence"] == 2


def test_compound_must_state_quantity_basis_and_measurement_role() -> None:
    compound = cast("list[JsonObject]", _operations(_example())[0]["facts"])[1]

    assert compound["kind"] == "compound"
    for key, value in (("quantity_basis", None), ("aggregation_role", "context_only")):
        batch = _example()
        fact = cast("list[JsonObject]", _operations(batch)[0]["facts"])[1]
        if value is None:
            del fact[key]
        else:
            fact[key] = value

        assert _fails_on(batch, key)


@pytest.mark.parametrize("role", ["compound_measurement"])
def test_nutrient_cannot_use_the_compound_measurement_role(role: str) -> None:
    batch = _example()
    _first_fact(batch)["aggregation_role"] = role

    assert _fails_on(batch, "aggregation_role")


def test_duplicate_component_ids_are_a_semantic_error() -> None:
    batch = _example()
    facts = cast("list[JsonObject]", _operations(batch)[0]["facts"])
    facts[1]["component_id"] = facts[0]["component_id"]

    assert _errors(batch) == []
    assert any(e.startswith("duplicate_component_id") for e in semantic_errors(batch))


def test_link_to_a_component_outside_the_upsert_is_a_semantic_error() -> None:
    batch = _example()
    links = cast("list[JsonObject]", _operations(batch)[0]["healthkit_links"])
    links[0]["component_id"] = "not-in-this-upsert"

    assert any(e.startswith("unknown_link_component") for e in semantic_errors(batch))


def test_link_projection_links_are_not_resolved_against_facts() -> None:
    batch = _load("valid_link_projection_seq2.json")

    assert semantic_errors(batch) == []


def test_projection_hash_refuses_duplicate_links() -> None:
    batch = _example()
    operation = _operations(batch)[0]
    links = cast("list[JsonObject]", operation["healthkit_links"])
    operation["healthkit_links"] = [links[0], copy.deepcopy(links[0])]

    with pytest.raises(ContractError, match="duplicate"):
        _ = expected_hashes(batch, operation)


def test_same_component_may_link_different_samples() -> None:
    batch = _example()
    operation = _operations(batch)[0]
    links = cast("list[JsonObject]", operation["healthkit_links"])
    second = copy.deepcopy(links[0])
    second["healthkit_sample_uuid"] = "3d043ce2-d57f-4f59-91f3-e9f1a53e540a"
    operation["healthkit_links"] = [links[0], second]

    assert semantic_errors(batch) == []
    forward = expected_hashes(batch, operation)["projection_hash"]
    operation["healthkit_links"] = [second, links[0]]
    assert expected_hashes(batch, operation)["projection_hash"] == forward


@pytest.mark.parametrize(
    ("zone", "occurred_at"),
    [
        ("America/Chicago", "2026-09-30T12:30:00-05:00"),
        ("Europe/Berlin", "2026-09-30T19:30:00+02:00"),
        ("Asia/Kolkata", "2026-09-30T23:00:00+05:30"),
        ("UTC", "2026-09-30T17:30:00Z"),
    ],
)
def test_iana_time_zones_are_accepted(zone: str, occurred_at: str) -> None:
    # The same instant, written with each zone's own offset at that instant.
    batch = _example()
    _operations(batch)[0]["time_zone"] = zone
    _operations(batch)[0]["occurred_at"] = occurred_at

    assert semantic_errors(batch) == []


@pytest.mark.parametrize("zone", ["Not/AZone", "America/Chicag", "Mars/Olympus"])
def test_unknown_time_zones_are_a_semantic_error(zone: str) -> None:
    batch = _example()
    _operations(batch)[0]["time_zone"] = zone

    assert _errors(batch) == []
    assert any(e.startswith("unknown_time_zone") for e in semantic_errors(batch))


@pytest.mark.parametrize(
    "value",
    [
        "2026-99-99T99:99:99+99:99",
        "2026-02-30T12:00:00Z",
        "2026-09-30T24:00:00Z",
        "2026-09-30T12:00:00+25:00",
    ],
)
def test_impossible_timestamps_are_a_semantic_error(value: str) -> None:
    batch = _example()
    _operations(batch)[0]["occurred_at"] = value

    assert _errors(batch) == []
    assert any(e.startswith("invalid_timestamp") for e in semantic_errors(batch))


def test_delete_timestamp_is_checked_too() -> None:
    batch = _load("valid_delete.json")
    _operations(batch)[0]["deleted_at"] = "2026-13-01T00:00:00Z"

    assert any(e.startswith("invalid_timestamp") for e in semantic_errors(batch))


@pytest.mark.parametrize("text", ["2.0", "2e0", "2E+0", "0.5", "1e400"])
def test_loader_rejects_every_float_spelling(text: str) -> None:
    with pytest.raises(ContractError, match="float"):
        _ = load_json(f'{{"revision": {text}}}')


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_loader_rejects_non_finite_constants(constant: str) -> None:
    with pytest.raises(ContractError, match=constant.lstrip("-")):
        _ = load_json(f'{{"revision": {constant}}}')


def test_loader_rejects_duplicate_names_at_any_depth() -> None:
    with pytest.raises(ContractError, match="duplicate object name"):
        _ = load_json('{"a": {"b": 1, "b": 2}}')


def test_loader_accepts_integers_and_paired_surrogate_escapes() -> None:
    assert load_json('{"revision": 2, "s": "\\ud83d\\ude00"}') == {
        "revision": 2,
        "s": "\U0001f600",
    }


def test_canonical_json_rejects_unpaired_surrogates_before_encoding() -> None:
    for value in ({"k": "a\ud800"}, {"\udc00": "v"}, ["\udfff"]):
        with pytest.raises(ContractError, match="lone_surrogate"):
            _ = canonical_json(value)


def test_semantic_errors_report_unpaired_surrogates_and_floats() -> None:
    batch = _example()
    operation = _operations(batch)[0]
    operation["display_name"] = "Water \ud800"
    operation["revision"] = 2.0

    found = semantic_errors(batch)

    assert any(e.startswith("lone_surrogate") for e in found)
    assert any(e.startswith("float") for e in found)


def _top(batch: JsonObject) -> JsonObject:
    return batch


def _upsert(batch: JsonObject) -> JsonObject:
    return _operations(batch)[0]


def _first_link(batch: JsonObject) -> JsonObject:
    return cast("list[JsonObject]", _upsert(batch)["healthkit_links"])[0]


NEWLINE_FIELDS: tuple[tuple[Callable[[JsonObject], JsonObject], str], ...] = (
    (_top, "batch_id"),
    (_top, "producer_id"),
    (_top, "writer_bundle_id"),
    (_upsert, "intake_id"),
    (_upsert, "occurred_at"),
    (_upsert, "time_zone"),
    (_upsert, "domain_facts_hash"),
    (_first_fact, "component_id"),
    (_first_fact, "unit"),
    (_first_fact, "amount"),
    (_first_link, "healthkit_type"),
)


@pytest.mark.parametrize(
    ("holder", "field"), NEWLINE_FIELDS, ids=[field for _, field in NEWLINE_FIELDS]
)
def test_anchored_patterns_reject_a_terminal_newline(
    holder: Callable[[JsonObject], JsonObject], field: str
) -> None:
    # Python regular expressions let `$` match before a final newline; the schema
    # must still reject "value\n" for every anchored identifier, digest and number.
    batch = _example()
    parent = holder(batch)
    original = parent[field]
    assert isinstance(original, str)
    assert _errors(batch) == []

    parent[field] = original + "\n"

    assert _errors(batch), f"{field} accepted a terminal newline"


@pytest.mark.parametrize("provenance", ["ocr_confirmed", "recipe_calculated"])
def test_provenance_covers_ocr_and_recipe_sources(provenance: str) -> None:
    batch = _example()
    _first_fact(batch)["provenance"] = provenance

    assert _errors(batch) == []


def test_pseudo_time_zones_are_rejected_even_when_the_host_lists_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    listed = {
        "America/Chicago",
        "localtime",
        "posixrules",
        "Factory",
        "posix/UTC",
        "right/UTC",
    }
    monkeypatch.setattr(reference, "available_timezones", lambda: listed)
    reference.time_zones.cache_clear()
    try:
        zones = reference.time_zones()
    finally:
        reference.time_zones.cache_clear()

    assert zones == frozenset({"America/Chicago"})


def test_writer_bundle_change_moves_only_the_client_payload_hash() -> None:
    batch = _example()
    operation = _operations(batch)[0]
    before = expected_hashes(batch, operation)

    batch["writer_bundle_id"] = "com.example.other.writer"
    after = expected_hashes(batch, operation)

    assert after["client_payload_hash"] != before["client_payload_hash"]
    assert after["domain_facts_hash"] == before["domain_facts_hash"]
    assert after["projection_hash"] == before["projection_hash"]


@pytest.mark.parametrize("unit", ["mg\u001e", "mg\u00a0", "m g", "mg\n"])
def test_unit_pattern_is_printable_ascii_in_every_validator(unit: str) -> None:
    # ECMA-262 and Python disagree on which characters \S excludes, so units use
    # an explicit printable-ASCII class that both read the same way.
    batch = _example()
    _first_fact(batch)["unit"] = unit

    assert _errors(batch)


def test_offset_matching_the_zone_at_that_instant_is_accepted() -> None:
    batch = _example()
    operation = _operations(batch)[0]
    operation["occurred_at"] = (
        "2026-01-15T12:30:00-06:00"  # Chicago is UTC-6 in January
    )

    assert not [
        e for e in semantic_errors(batch) if e.startswith("offset_zone_mismatch")
    ]
