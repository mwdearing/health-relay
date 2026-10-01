"""Reference implementation of intake-context v1 canonical JSON and hashing.

The receiver, the sender and these tests must all produce the same bytes. This
module is the executable form of docs/reference/intake-context-v1.md.
"""

import hashlib
import json
from pathlib import Path
from typing import TypeAlias, cast

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


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        message = "float values are not allowed in canonical JSON"
        raise ValueError(message)  # noqa: TRY004
    if isinstance(value, dict):
        for item in cast("JsonObject", value).values():
            _reject_floats(item)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            _reject_floats(item)


def canonical_json(value: object) -> bytes:
    _reject_floats(value)
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


def projection_hash(batch: JsonObject, operation: JsonObject) -> str:
    links = cast("list[JsonObject]", operation.get("healthkit_links", []))
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
