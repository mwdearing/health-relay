"""Reference implementation of intake-context v1 canonical JSON and hashing.

The receiver, the sender and these tests must all produce the same bytes. This
module is the executable form of docs/reference/intake-context-v1.md.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import NoReturn, TypeAlias, cast
from zoneinfo import available_timezones

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "healthrelay.intake-context.v1.schema.json"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "intake_context"

JsonObject: TypeAlias = dict[str, object]

DOMAIN_EXCLUDED = frozenset(
    {
        "operation_id",
        "projection_sequence",
        "healthkit_links",
        "domain_facts_hash",
        "projection_hash",
        "client_payload_hash",
    }
)


class ContractError(ValueError):
    """Raised for input the contract forbids before any schema check can run."""


_SURROGATE = re.compile("[\ud800-\udfff]")
_TIMESTAMP = re.compile(
    r"""
    (\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?
    (?:Z|[+-](\d{2}):(\d{2}))
    """,
    re.VERBOSE,
)
_TIMESTAMP_FIELDS = ("occurred_at", "recorded_at", "deleted_at")


def _reject_float_text(text: str) -> NoReturn:
    message = f"float number {text} is not allowed; integers and decimals as strings"
    raise ContractError(message)


def _reject_constant(name: str) -> NoReturn:
    message = f"{name} is not allowed in canonical JSON"
    raise ContractError(message)


def _reject_duplicate_names(pairs: list[tuple[str, object]]) -> JsonObject:
    seen: JsonObject = {}
    for name, value in pairs:
        if name in seen:
            message = f"duplicate object name {name!r}"
            raise ContractError(message)
        seen[name] = value
    return seen


def _contract_violations(value: object, path: str) -> Iterator[str]:
    if isinstance(value, float):
        yield f"float at {path}: float values are not allowed"
    elif isinstance(value, str):
        if _SURROGATE.search(value):
            yield f"lone_surrogate at {path}: unpaired surrogate in string"
    elif isinstance(value, dict):
        for key, item in cast("JsonObject", value).items():
            if _SURROGATE.search(key):
                yield f"lone_surrogate at {path}: unpaired surrogate in object name"
            yield from _contract_violations(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(cast("list[object]", value)):
            yield from _contract_violations(item, f"{path}/{index}")


def load_json(text: str) -> object:
    """Parse JSON text, refusing what the contract forbids.

    Rejects duplicate object names, any number written as a float (so ``2.0``
    and ``2e0`` fail even where an integer is expected), NaN and infinities,
    and unpaired surrogates.
    """
    value = cast(
        "object",
        json.loads(
            text,
            parse_float=_reject_float_text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_names,
        ),
    )
    for violation in _contract_violations(value, ""):
        raise ContractError(violation)
    return value


def canonical_json(value: object) -> bytes:
    for violation in _contract_violations(value, ""):
        raise ContractError(violation)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def domain_facts_hash(batch: JsonObject, operation: JsonObject) -> str:
    body = {k: v for k, v in operation.items() if k not in DOMAIN_EXCLUDED}
    return digest({"producer_id": batch["producer_id"], **body})


def _duplicate_links(links: list[JsonObject]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    duplicates: list[tuple[str, str]] = []
    for link in links:
        pair = (
            cast("str", link["component_id"]),
            cast("str", link["healthkit_sample_uuid"]),
        )
        if pair in seen:
            duplicates.append(pair)
        seen.add(pair)
    return duplicates


def projection_hash(batch: JsonObject, operation: JsonObject) -> str:
    links = cast("list[JsonObject]", operation.get("healthkit_links", []))
    if _duplicate_links(links):
        message = "duplicate (component_id, healthkit_sample_uuid) in healthkit_links"
        raise ContractError(message)
    ordered = sorted(
        links,
        key=lambda link: (
            cast("str", link["component_id"]),
            cast("str", link["healthkit_sample_uuid"]),
        ),
    )
    return digest(
        {
            "producer_id": batch["producer_id"],
            "intake_id": operation["intake_id"],
            "revision": operation["revision"],
            "projection_sequence": operation["projection_sequence"],
            "healthkit_links": ordered,
        }
    )


def client_payload_hash(batch: JsonObject, operation: JsonObject) -> str:
    body = {k: v for k, v in operation.items() if k != "client_payload_hash"}
    return digest(
        {
            "producer_id": batch["producer_id"],
            "writer_bundle_id": batch["writer_bundle_id"],
            "installation_id": batch["installation_id"],
            "schema_version": batch["schema_version"],
            "operation": body,
        }
    )


def expected_hashes(batch: JsonObject, operation: JsonObject) -> dict[str, str]:
    kind = operation["operation"]
    hashes: dict[str, str] = {}
    if kind in {"upsert", "delete"}:
        hashes["domain_facts_hash"] = domain_facts_hash(batch, operation)
    if kind in {"upsert", "link_projection"}:
        hashes["projection_hash"] = projection_hash(batch, operation)
    hashes["client_payload_hash"] = client_payload_hash(batch, operation)
    return hashes


def seal(batch: JsonObject) -> JsonObject:
    """Return the batch with every operation's hashes recomputed in place."""
    for operation in cast("list[JsonObject]", batch["operations"]):
        hashes = expected_hashes(batch, operation)
        for key in ("domain_facts_hash", "projection_hash"):
            if key in hashes:
                operation[key] = hashes[key]
        operation["client_payload_hash"] = client_payload_hash(batch, operation)
    return batch


# Host-local or implementation-specific keys that available_timezones() may list but
# that do not name one IANA zone everywhere (e.g. "localtime" follows /etc/localtime).
_PSEUDO_ZONES = frozenset({"localtime", "posixrules", "Factory"})
_PSEUDO_ZONE_PREFIXES = ("posix/", "right/")


@lru_cache(maxsize=1)
def time_zones() -> frozenset[str]:
    """IANA zone names a receiver accepts (host-local pseudo-zones removed)."""
    return frozenset(
        zone
        for zone in available_timezones()
        if zone not in _PSEUDO_ZONES and not zone.startswith(_PSEUDO_ZONE_PREFIXES)
    )


def _timestamp_is_valid(text: str) -> bool:
    """RFC 3339 ranges: a real date, hour 0-23, minute, second and offset in range.

    A second of 60 is rejected: leap seconds are not representable on the producing
    platforms, and accepting the numeric range alone would admit instants that never
    existed.
    """
    match = _TIMESTAMP.fullmatch(text)
    if match is None:
        return False
    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    offset_hour, offset_minute = (int(g or 0) for g in match.groups()[6:])
    try:
        _ = date(year, month, day)
    except ValueError:
        return False
    return (
        hour <= 23
        and minute <= 59
        and second <= 59
        and offset_hour <= 23
        and offset_minute <= 59
    )


def _operation_errors(operation: JsonObject, path: str) -> Iterator[str]:
    kind = operation.get("operation")
    links = cast("list[JsonObject]", operation.get("healthkit_links", []))
    for component_id, sample_uuid in _duplicate_links(links):
        yield (
            f"duplicate_link at {path}/healthkit_links: "
            f"({component_id}, {sample_uuid}) appears more than once"
        )
    for field in _TIMESTAMP_FIELDS:
        value = operation.get(field)
        if isinstance(value, str) and not _timestamp_is_valid(value):
            yield f"invalid_timestamp at {path}/{field}: {value} is not a real instant"
    if kind != "upsert":
        return
    zone = operation.get("time_zone")
    if isinstance(zone, str) and zone not in time_zones():
        yield f"unknown_time_zone at {path}/time_zone: {zone} is not an IANA zone"
    facts = cast("list[JsonObject]", operation.get("facts", []))
    yield from _upsert_component_errors(facts, links, path)


def _upsert_component_errors(
    facts: list[JsonObject], links: list[JsonObject], path: str
) -> Iterator[str]:
    kinds: dict[str, object] = {}
    for fact in facts:
        component_id = cast("str", fact["component_id"])
        if component_id in kinds:
            yield f"duplicate_component_id at {path}/facts: {component_id}"
        kinds[component_id] = fact.get("kind")
    for link in links:
        component_id = cast("str", link["component_id"])
        if component_id not in kinds:
            yield (
                f"unknown_link_component at {path}/healthkit_links: "
                f"{component_id} is not a fact of this upsert"
            )
        elif kinds[component_id] != "nutrient":
            # Only nutrients have a HealthKit quantity type; compounds and blends
            # travel in the context only.
            yield (
                f"link_to_non_nutrient at {path}/healthkit_links: "
                f"{component_id} is a {kinds[component_id]}, not a nutrient"
            )


def semantic_errors(batch: JsonObject) -> list[str]:
    """Rules the JSON Schema cannot express, for a schema-valid batch.

    A link_projection's component IDs resolve against the stored target
    revision, which only the receiver can see, so only duplicate pairs are
    checked there.
    """
    errors = list(_contract_violations(batch, ""))
    operations = cast("list[JsonObject]", batch.get("operations", []))
    for index, operation in enumerate(operations):
        errors.extend(_operation_errors(operation, f"/operations/{index}"))
    return errors
