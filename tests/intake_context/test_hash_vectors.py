"""Independent golden vectors for the intake-context hashing contract."""

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import TypeAlias, cast

import pytest

JSONValue: TypeAlias = (
    bool | int | str | list["JSONValue"] | dict[str, "JSONValue"] | None
)

FIXTURE_DIR = Path(
    os.environ.get(
        "INTAKE_CONTEXT_FIXTURE_DIR",
        str(Path(__file__).parents[1] / "fixtures" / "intake_context"),
    )
)

WORKED_DOMAIN_HASH = (
    "sha256:93bb96b900c8d22d77630236eb60ec9c453e0627009d9fe01041ea3ef438c4f0"
)
WORKED_PROJECTION_HASH = (
    "sha256:8d5f54713418cd2f525dbf176e4d9e6c73241ded8ac5f9288db6e0179b8f71fd"
)
WORKED_CLIENT_HASH = (
    "sha256:873b7b15148917d14c17c36b074f8e4bb3fd1d0afea2652c8fb5319e54e81003"
)
WORKED_PROJECTION_BYTES = (
    b'{"healthkit_links":[{"component_id":"water","disposition":"active",'
    b'"healthkit_sample_uuid":"2c932bd1-c46d-4e38-b481-e0d842fdd429",'
    b'"healthkit_type":"HKQuantityTypeIdentifierDietaryWater",'
    b'"sync_identifier":"intake:e6677963-418c-4027-b563-551d8a531eed:water",'
    b'"sync_version":2}],"intake_id":"e6677963-418c-4027-b563-551d8a531eed",'
    b'"producer_id":"nutrition-app","projection_sequence":1,"revision":2}'
)

DOMAIN_EXCLUDED = {
    "operation_id",
    "projection_sequence",
    "healthkit_links",
    "domain_facts_hash",
    "projection_hash",
    "client_payload_hash",
}


def _object(value: JSONValue) -> dict[str, JSONValue]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return cast("dict[str, JSONValue]", value)


def _array(value: JSONValue) -> list[JSONValue]:
    assert isinstance(value, list)
    return cast("list[JSONValue]", value)


def _load(path: Path) -> dict[str, JSONValue]:
    value = cast("JSONValue", json.loads(path.read_text(encoding="utf-8")))
    return _object(value)


def _quote(value: str) -> bytes:
    pieces: list[str] = ['"']
    short_escapes = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    for character in value:
        code_point = ord(character)
        if 0xD800 <= code_point <= 0xDFFF:
            message = "unpaired surrogate is not valid canonical JSON"
            raise ValueError(message)
        if character in short_escapes:
            pieces.append(short_escapes[character])
        elif code_point < 0x20:
            pieces.append(f"\\u00{code_point:02x}")
        else:
            pieces.append(character)
    pieces.append('"')
    return "".join(pieces).encode()


def canonical_json(value: object) -> bytes:
    """Encode the contract's canonical JSON without json.dumps shortcuts."""
    if value is None:
        return b"null"
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, int):
        return str(value).encode("ascii")
    if isinstance(value, float):
        message = "floating-point numbers are not canonical input"
        raise TypeError(message)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list):
        items = cast("list[object]", value)
        return b"[" + b",".join(canonical_json(item) for item in items) + b"]"
    if isinstance(value, dict):
        object_value = cast("dict[object, object]", value)
        assert all(isinstance(key, str) for key in object_value)
        string_object = cast("dict[str, object]", object_value)
        members = (
            _quote(key) + b":" + canonical_json(string_object[key])
            for key in sorted(string_object)
        )
        return b"{" + b",".join(members) + b"}"
    message = f"unsupported canonical JSON value: {type(value).__name__}"
    raise TypeError(message)


def _digest(value: JSONValue) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _producer(batch: dict[str, JSONValue]) -> str:
    producer = batch["producer_id"]
    assert isinstance(producer, str)
    return producer


def domain_facts_value(
    batch: dict[str, JSONValue], operation: dict[str, JSONValue]
) -> dict[str, JSONValue]:
    value: dict[str, JSONValue] = {"producer_id": _producer(batch)}
    value.update(
        {key: item for key, item in operation.items() if key not in DOMAIN_EXCLUDED}
    )
    return value


def domain_facts_hash(
    batch: dict[str, JSONValue], operation: dict[str, JSONValue]
) -> str:
    return _digest(domain_facts_value(batch, operation))


def projection_value(
    batch: dict[str, JSONValue], operation: dict[str, JSONValue]
) -> dict[str, JSONValue]:
    links: list[JSONValue] = [
        _object(item) for item in _array(operation["healthkit_links"])
    ]

    def link_key(value: JSONValue) -> tuple[str, str]:
        link = _object(value)
        component_id = link["component_id"]
        sample_uuid = link["healthkit_sample_uuid"]
        assert isinstance(component_id, str)
        assert isinstance(sample_uuid, str)
        return component_id, sample_uuid

    return {
        "producer_id": _producer(batch),
        "intake_id": operation["intake_id"],
        "revision": operation["revision"],
        "projection_sequence": operation["projection_sequence"],
        "healthkit_links": sorted(links, key=link_key),
    }


def projection_hash(
    batch: dict[str, JSONValue], operation: dict[str, JSONValue]
) -> str:
    return _digest(projection_value(batch, operation))


def client_payload_value(
    batch: dict[str, JSONValue], operation: dict[str, JSONValue]
) -> dict[str, JSONValue]:
    return {
        "producer_id": _producer(batch),
        "writer_bundle_id": batch["writer_bundle_id"],
        "installation_id": batch["installation_id"],
        "schema_version": batch["schema_version"],
        "operation": {
            key: item for key, item in operation.items() if key != "client_payload_hash"
        },
    }


def client_payload_hash(
    batch: dict[str, JSONValue], operation: dict[str, JSONValue]
) -> str:
    return _digest(client_payload_value(batch, operation))


def _worked() -> tuple[dict[str, JSONValue], dict[str, JSONValue]]:
    batch = _load(FIXTURE_DIR / "valid_worked_example.json")
    operation = _object(_array(batch["operations"])[0])
    return batch, operation


def _assert_operation_hashes(
    fixture_name: str,
    batch: dict[str, JSONValue],
    operation: dict[str, JSONValue],
) -> None:
    kind = operation["operation"]
    if kind in {"upsert", "delete"}:
        assert domain_facts_hash(batch, operation) == operation["domain_facts_hash"], (
            fixture_name,
            "domain_facts_hash",
            canonical_json(domain_facts_value(batch, operation)),
        )
    if kind in {"upsert", "link_projection"}:
        assert projection_hash(batch, operation) == operation["projection_hash"], (
            fixture_name,
            "projection_hash",
            canonical_json(projection_value(batch, operation)),
        )
    assert client_payload_hash(batch, operation) == operation["client_payload_hash"], (
        fixture_name,
        "client_payload_hash",
        canonical_json(client_payload_value(batch, operation)),
    )


def test_spec_canonical_json_example() -> None:
    value = {"b": [2, 1], "a": {"d": "x", "c": None}}
    assert canonical_json(value) == b'{"a":{"c":null,"d":"x"},"b":[2,1]}'


def test_canonical_json_writes_non_ascii_as_raw_utf8() -> None:
    stdlib_bytes = json.dumps("café", ensure_ascii=False).encode()
    assert canonical_json("café") == stdlib_bytes == b'"caf\xc3\xa9"'


def test_canonical_json_writes_del_and_line_separator_as_raw_utf8() -> None:
    assert canonical_json("\u007f\u2028") == '"\u007f\u2028"'.encode()


def test_canonical_json_uses_required_short_and_lowercase_control_escapes() -> None:
    assert canonical_json('"\\\b\f\n\r\t\x01\x0e') == (
        b'"\\"\\\\\\b\\f\\n\\r\\t\\u0001\\u000e"'
    )


def test_canonical_json_sorts_keys_by_unicode_code_point() -> None:
    value = {"\U00010000": 1, "\uff5e": 2}
    assert canonical_json(value) == '{"\uff5e":2,"\U00010000":1}'.encode()

    mixed = {
        "outer": {"b": 1, "B": 2, "_": 3, "a": 4, "A": 5, "1": 6},
        "Outer": 0,
    }
    expected = b'{"Outer":0,"outer":{"1":6,"A":5,"B":2,"_":3,"a":4,"b":1}}'
    stdlib_bytes = json.dumps(
        mixed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert canonical_json(mixed) == stdlib_bytes == expected


def test_canonical_json_integer_and_literals() -> None:
    assert canonical_json([-27, 0, 9223372036854775807, True, False, None]) == (
        b"[-27,0,9223372036854775807,true,false,null]"
    )
    with pytest.raises(TypeError):
        _ = canonical_json(cast("JSONValue", 5.0))


def test_worked_golden_digests_are_literal_vectors() -> None:
    batch, operation = _worked()
    assert domain_facts_hash(batch, operation) == WORKED_DOMAIN_HASH
    assert projection_hash(batch, operation) == WORKED_PROJECTION_HASH
    assert client_payload_hash(batch, operation) == WORKED_CLIENT_HASH


def test_worked_projection_canonical_bytes_are_literal_vector() -> None:
    batch, operation = _worked()
    assert canonical_json(projection_value(batch, operation)) == WORKED_PROJECTION_BYTES


def test_every_valid_and_scenario_operation_digest() -> None:
    valid_paths = sorted(FIXTURE_DIR.glob("valid_*.json"))
    scenario_paths = sorted(FIXTURE_DIR.glob("scenario_*.json"))
    assert {path.name for path in valid_paths} >= {
        "valid_delete.json",
        "valid_link_projection_seq2.json",
        "valid_proprietary_blend.json",
        "valid_worked_example.json",
    }
    assert {path.name for path in scenario_paths} >= {
        "scenario_same_operation_different_content.json",
        "scenario_stale_revision.json",
    }

    for path in valid_paths:
        batch = _load(path)
        for item in _array(batch["operations"]):
            _assert_operation_hashes(path.name, batch, _object(item))
    for path in scenario_paths:
        scenario = _load(path)
        for step_item in _array(scenario["steps"]):
            batch = _object(_object(step_item)["batch"])
            for operation_item in _array(batch["operations"]):
                _assert_operation_hashes(path.name, batch, _object(operation_item))


def test_projection_hash_ignores_listed_link_order() -> None:
    batch = _load(FIXTURE_DIR / "valid_link_projection_seq2.json")
    operation = _object(_array(batch["operations"])[0])
    reversed_operation = copy.deepcopy(operation)
    links = _array(reversed_operation["healthkit_links"])
    links.reverse()
    reversed_operation["healthkit_links"] = links
    assert projection_hash(batch, reversed_operation) == projection_hash(
        batch, operation
    )


def test_projection_hash_changes_with_sync_version_and_disposition() -> None:
    batch, operation = _worked()
    baseline = projection_hash(batch, operation)
    for field, value in (("sync_version", 3), ("disposition", "superseded")):
        changed = copy.deepcopy(operation)
        links = _array(changed["healthkit_links"])
        link = _object(links[0])
        link[field] = value
        links[0] = link
        changed["healthkit_links"] = links
        assert projection_hash(batch, changed) != baseline


def test_domain_facts_hash_scope_and_producer() -> None:
    batch, operation = _worked()
    baseline = domain_facts_hash(batch, operation)
    changed_batch = copy.deepcopy(batch)
    changed_batch["installation_id"] = "another-installation"
    changed_batch["writer_bundle_id"] = "another.writer"
    assert domain_facts_hash(changed_batch, operation) == baseline

    changed_operation = copy.deepcopy(operation)
    for field in (
        "operation_id",
        "projection_sequence",
        "healthkit_links",
        "domain_facts_hash",
        "projection_hash",
        "client_payload_hash",
    ):
        changed_operation[field] = "ignored"
    assert domain_facts_hash(batch, changed_operation) == baseline

    changed_batch["producer_id"] = "another-producer"
    assert domain_facts_hash(changed_batch, operation) != baseline


def test_client_payload_hash_covers_batch_scope_and_nested_digest() -> None:
    batch, operation = _worked()
    baseline = client_payload_hash(batch, operation)
    for field in (
        "writer_bundle_id",
        "installation_id",
        "schema_version",
        "producer_id",
    ):
        changed_batch = copy.deepcopy(batch)
        changed_batch[field] = f"changed-{field}"
        assert client_payload_hash(changed_batch, operation) != baseline

    changed_operation = copy.deepcopy(operation)
    changed_operation["domain_facts_hash"] = "sha256:" + "0" * 64
    assert client_payload_hash(batch, changed_operation) != baseline


def test_facts_and_members_order_are_content() -> None:
    batch, operation = _worked()
    changed = copy.deepcopy(operation)
    facts = _array(changed["facts"])
    facts.reverse()
    changed["facts"] = facts
    assert domain_facts_hash(batch, changed) != domain_facts_hash(batch, operation)

    blend_batch = _load(FIXTURE_DIR / "valid_proprietary_blend.json")
    blend = _object(_array(blend_batch["operations"])[0])
    changed_blend = copy.deepcopy(blend)
    blend_facts = _array(changed_blend["facts"])
    blend_fact = _object(blend_facts[1])
    members = _array(blend_fact["members"])
    members.reverse()
    blend_fact["members"] = members
    blend_facts[1] = blend_fact
    changed_blend["facts"] = blend_facts
    assert domain_facts_hash(blend_batch, changed_blend) != domain_facts_hash(
        blend_batch, blend
    )


def test_decimal_string_spelling_is_content() -> None:
    batch, operation = _worked()
    changed = copy.deepcopy(operation)
    facts = _array(changed["facts"])
    compound = _object(facts[1])
    assert compound["amount"] == "5"
    compound["amount"] = "5.0"
    facts[1] = compound
    changed["facts"] = facts
    assert domain_facts_hash(batch, changed) != domain_facts_hash(batch, operation)


def test_tampered_display_name_changes_domain_and_client_not_projection() -> None:
    batch, operation = _worked()
    changed = copy.deepcopy(operation)
    changed["display_name"] = "Tampered synthetic drink"
    assert domain_facts_hash(batch, changed) != operation["domain_facts_hash"]
    assert client_payload_hash(batch, changed) != operation["client_payload_hash"]
    assert projection_hash(batch, changed) == operation["projection_hash"]
