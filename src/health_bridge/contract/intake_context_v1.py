"""Pydantic model and digest calculator for the healthrelay.intake-context v1 contract.

docs/reference/intake-context-v1.md is the contract. Amounts are decimal strings
and only revision, projection_sequence and sync_version are integers, so no
float can enter a model or a digest.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import (
    Annotated,
    ClassVar,
    Final,
    Literal,
    LiteralString,
    NoReturn,
    Self,
    TypeAlias,
    cast,
)
from zoneinfo import ZoneInfo, available_timezones

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticCustomError

JsonObject: TypeAlias = dict[str, object]

INT64_MAX: Final = 9223372036854775807
MAX_HOUR: Final = 23
MAX_MINUTE: Final = 59
SCHEMA_NAME: Final = "healthrelay.intake-context"
SCHEMA_VERSION: Final = "1.0"

UUID_PATTERN: Final = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?!\n)$"
)
PRODUCER_ID_PATTERN: Final = r"^[a-z0-9][a-z0-9._-]{0,63}(?!\n)$"
SLUG_PATTERN: Final = r"^[a-z0-9][a-z0-9._-]{0,63}(?!\n)$"
BUNDLE_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9.-]{0,254}(?!\n)$"
DECIMAL_PATTERN: Final = r"^(0|[1-9][0-9]*)(\.[0-9]+)?(?!\n)$"
UNIT_PATTERN: Final = r"^[!-~]+(?!\n)$"
DIGEST_PATTERN: Final = r"^sha256:[0-9a-f]{64}(?!\n)$"
TIMESTAMP_PATTERN: Final = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})(?!\n)$"
)
TIME_ZONE_PATTERN: Final = r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*(?!\n)$"
HEALTHKIT_TYPE_PATTERN: Final = r"^HKQuantityTypeIdentifier[A-Za-z0-9]+(?!\n)$"

_TIMESTAMP_BODY: Final = (
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.[0-9]+)?(?:Z|([+-])([0-9]{2}):([0-9]{2}))"
)
_TIMESTAMP_PARTS: Final = re.compile(_TIMESTAMP_BODY, re.ASCII)
_SURROGATE: Final = re.compile(r"[\ud800-\udfff]")
_PSEUDO_ZONES: Final = frozenset({"localtime", "posixrules", "Factory"})
_PSEUDO_ZONE_PREFIXES: Final = ("posix/", "right/")
_DOMAIN_EXCLUDED: Final = frozenset(
    {
        "operation_id",
        "projection_sequence",
        "healthkit_links",
        "domain_facts_hash",
        "projection_hash",
        "client_payload_hash",
    }
)
_DIGEST_FIELDS: Final = ("domain_facts_hash", "projection_hash", "client_payload_hash")

NutritionCompleteness: TypeAlias = Literal["complete", "partial", "unknown"]
Provenance: TypeAlias = Literal[
    "user_confirmed",
    "label_confirmed",
    "ocr_confirmed",
    "catalog_reference",
    "recipe_calculated",
    "estimated",
]
FactKind: TypeAlias = Literal["nutrient", "compound", "blend"]
ValueState: TypeAlias = Literal[
    "known", "unknown", "not_applicable", "below_reporting_threshold"
]
AggregationRole: TypeAlias = Literal[
    "context_only", "compound_measurement", "blend_total_only"
]
QuantityBasis: TypeAlias = Literal["compound_mass", "active_nutrient_mass", "unknown"]
Disposition: TypeAlias = Literal["active", "superseded", "deleted"]


_ROLE_FOR_KIND: Final[dict[FactKind, AggregationRole]] = {
    "nutrient": "context_only",
    "compound": "compound_measurement",
    "blend": "blend_total_only",
}


class IntakeContextContractError(ValueError):
    """A parse-time or validation failure; the message carries ``type=<token>``."""


def _fail(
    error_type: LiteralString, message: LiteralString, **context: str
) -> NoReturn:
    raise PydanticCustomError(error_type, message, dict(context))


def _describe(error_type: str, message: str, location: str = "") -> str:
    prefix = f"{location}: " if location else ""
    return f"{prefix}{message} (type={error_type})"


def _error(error_type: str, message: str, location: str = "") -> NoReturn:
    raise IntakeContextContractError(_describe(error_type, message, location))


def _violations(
    value: object, path: str
) -> Iterator[tuple[LiteralString, LiteralString, str]]:
    """Yield (error type, message, path) for floats and unpaired surrogates."""
    if isinstance(value, float):
        yield "float_number", "a float number is not allowed", path
    elif isinstance(value, str):
        if _SURROGATE.search(value):
            yield "lone_surrogate", "unpaired surrogate in a string", path
    elif isinstance(value, dict):
        for name, item in cast("JsonObject", value).items():
            if _SURROGATE.search(name):
                yield "lone_surrogate", "unpaired surrogate in an object name", path
            yield from _violations(item, f"{path}/{name}")
    elif isinstance(value, list):
        for index, item in enumerate(cast("list[object]", value)):
            yield from _violations(item, f"{path}/{index}")


def _reject_float_text(text: str) -> NoReturn:
    _error("float_number", f"float number {text} is not allowed")


def _reject_constant(name: str) -> NoReturn:
    _error("non_finite_number", f"{name} is not allowed")


def _reject_duplicate_names(pairs: list[tuple[str, object]]) -> JsonObject:
    found: JsonObject = {}
    for name, value in pairs:
        if name in found:
            _error("duplicate_object_name", f"duplicate object name {name!r}")
        found[name] = value
    return found


def _parse_json_text(text: str) -> object:
    try:
        return cast(
            "object",
            json.loads(
                text,
                parse_float=_reject_float_text,
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_names,
            ),
        )
    except IntakeContextContractError:
        raise
    except RecursionError as exc:
        message = _describe("nesting_too_deep", "JSON nesting is too deep")
        raise IntakeContextContractError(message) from exc
    except ValueError as exc:
        message = _describe("invalid_json", f"not valid JSON: {exc}")
        raise IntakeContextContractError(message) from exc


def canonical_json(value: object) -> bytes:
    """Canonical JSON bytes as defined by the contract."""
    for error_type, message, path in _violations(value, ""):
        _error(error_type, message, path or "<value>")
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        message = _describe("unsupported_json_value", f"not canonicalisable: {exc}")
        raise IntakeContextContractError(message) from exc


def _sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


@lru_cache(maxsize=1)
def _time_zone_names() -> frozenset[str]:
    return frozenset(
        name
        for name in available_timezones()
        if name not in _PSEUDO_ZONES and not name.startswith(_PSEUDO_ZONE_PREFIXES)
    )


def _parse_instant(text: str) -> datetime | None:
    match = _TIMESTAMP_PARTS.fullmatch(text)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    sign, offset_hour, offset_minute = match.groups()[6:]
    offset = timedelta(hours=int(offset_hour or 0), minutes=int(offset_minute or 0))
    if hour > MAX_HOUR or max(minute, second, int(offset_minute or 0)) > MAX_MINUTE:
        return None
    if offset >= timedelta(days=1):
        return None
    try:
        _ = date(year, month, day)
    except ValueError:
        return None
    if sign == "-":
        offset = -offset
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone(offset))


def _check_timestamp(value: str) -> str:
    if _parse_instant(value) is None:
        _fail(
            "invalid_timestamp",
            "{value} is not a real RFC 3339 instant",
            value=value,
        )
    return value


def _check_time_zone(value: str) -> str:
    if value not in _time_zone_names():
        _fail(
            "unknown_time_zone",
            "{value} is not a name in the IANA time zone database",
            value=value,
        )
    return value


def _check_code(value: str) -> str:
    if value == "dietary_water":
        _fail(
            "dietary_water_not_allowed",
            "code: water is always hydration",
        )
    return value


def _reject_explicit_null(value: object) -> object:
    if value is None:
        _fail(
            "explicit_null_not_allowed",
            "field may be omitted but cannot be null",
        )
    return value


def _reject_bool(value: object) -> object:
    if isinstance(value, bool):
        _fail("int_type", "a boolean is not an integer")
    return value


def _reject_json_number(value: object) -> object:
    if isinstance(value, int | float) and not isinstance(value, bool):
        _fail(
            "float_number",
            "an amount is a decimal string, never a JSON number",
        )
    return value


Uuid: TypeAlias = Annotated[str, StringConstraints(pattern=UUID_PATTERN)]
ProducerId: TypeAlias = Annotated[str, StringConstraints(pattern=PRODUCER_ID_PATTERN)]
Slug: TypeAlias = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
BundleId: TypeAlias = Annotated[str, StringConstraints(pattern=BUNDLE_ID_PATTERN)]
DecimalString: TypeAlias = Annotated[
    str,
    BeforeValidator(_reject_json_number),
    StringConstraints(pattern=DECIMAL_PATTERN),
]
Unit: TypeAlias = Annotated[
    str, StringConstraints(pattern=UNIT_PATTERN, min_length=1, max_length=32)
]
Text: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Digest: TypeAlias = Annotated[str, StringConstraints(pattern=DIGEST_PATTERN)]
Timestamp: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=TIMESTAMP_PATTERN),
    AfterValidator(_check_timestamp),
]
TimeZoneName: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=TIME_ZONE_PATTERN),
    AfterValidator(_check_time_zone),
]
CatalogCode: TypeAlias = Annotated[
    str, StringConstraints(pattern=SLUG_PATTERN), AfterValidator(_check_code)
]
HealthKitType: TypeAlias = Annotated[
    str, StringConstraints(pattern=HEALTHKIT_TYPE_PATTERN)
]
PositiveInt64: TypeAlias = Annotated[int, Field(strict=True, ge=1, le=INT64_MAX)]
FirstSequence: TypeAlias = Annotated[Literal[1], BeforeValidator(_reject_bool)]
LaterSequence: TypeAlias = Annotated[int, Field(strict=True, ge=2, le=INT64_MAX)]
SyncIdentifier: TypeAlias = Annotated[
    str, StringConstraints(min_length=1, max_length=256)
]

OptionalText: TypeAlias = Annotated[Text | None, BeforeValidator(_reject_explicit_null)]
OptionalDecimal: TypeAlias = Annotated[
    DecimalString | None, BeforeValidator(_reject_explicit_null)
]
OptionalUnit: TypeAlias = Annotated[Unit | None, BeforeValidator(_reject_explicit_null)]
OptionalBasis: TypeAlias = Annotated[
    QuantityBasis | None, BeforeValidator(_reject_explicit_null)
]


class _ContractModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        regex_engine="python-re",
        serialize_by_alias=True,
        strict=True,
    )


class Serving(_ContractModel):
    amount: DecimalString
    unit: Unit


class BlendMember(_ContractModel):
    label_name: Text
    amount: OptionalDecimal = None
    unit: OptionalUnit = None

    @model_validator(mode="after")
    def _amount_and_unit_travel_together(self) -> Self:
        if (self.amount is None) != (self.unit is None):
            _fail(
                "member_amount_unit_mismatch",
                "a member amount and unit appear together or not at all",
            )
        return self


class Fact(_ContractModel):
    component_id: Slug
    kind: FactKind
    code: CatalogCode
    label_name: OptionalText = None
    amount: OptionalDecimal = None
    unit: OptionalUnit = None
    value_state: ValueState
    quantity_basis: OptionalBasis = None
    aggregation_role: AggregationRole
    provenance: Provenance
    members: Annotated[
        list[BlendMember] | None,
        Field(min_length=1),
        BeforeValidator(_reject_explicit_null),
    ] = None

    @model_validator(mode="after")
    def _check_value_state(self) -> Self:
        if self.value_state == "known":
            if self.amount is None:
                _fail("amount_required", "a known value needs an amount")
            if self.unit is None:
                _fail("unit_required", "a known value needs a unit")
            return self
        if self.amount is not None:
            _fail(
                "amount_not_allowed",
                "{state} forbids an amount",
                state=self.value_state,
            )
        if self.value_state != "below_reporting_threshold" and self.unit is not None:
            _fail("unit_not_allowed", "{state} forbids a unit", state=self.value_state)
        return self

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind == "blend":
            if self.members is None:
                _fail("members_required", "a blend needs members")
        elif self.members is not None:
            _fail("members_not_allowed", "only a blend has members")
        if self.kind == "compound" and self.quantity_basis is None:
            _fail("quantity_basis_required", "a compound needs a quantity_basis")
        wanted = _ROLE_FOR_KIND[self.kind]
        if self.aggregation_role != wanted:
            _fail(
                "aggregation_role_mismatch",
                "a {kind} uses aggregation_role {wanted}",
                kind=self.kind,
                wanted=wanted,
            )
        return self


class HealthKitLink(_ContractModel):
    component_id: Slug
    healthkit_sample_uuid: Uuid
    healthkit_type: HealthKitType
    sync_identifier: SyncIdentifier
    sync_version: PositiveInt64
    disposition: Disposition


def healthkit_type_for(code: str) -> str | None:
    """The HealthKit quantity type a nutrient code is written as, if it has one."""
    if code == "hydration":
        return "HKQuantityTypeIdentifierDietaryWater"
    if not code.startswith("dietary_"):
        return None
    return "HKQuantityTypeIdentifier" + "".join(
        part[:1].upper() + part[1:] for part in code.split("_")
    )


def _check_links(links: list[HealthKitLink]) -> None:
    pairs: set[tuple[str, str]] = set()
    sample_owners: dict[str, set[str]] = {}
    sync_samples: dict[tuple[str, str, str], set[str]] = {}
    for link in links:
        pair = (link.component_id, link.healthkit_sample_uuid)
        if pair in pairs:
            _fail(
                "duplicate_link",
                "({component_id}, {sample}) appears more than once",
                component_id=pair[0],
                sample=pair[1],
            )
        pairs.add(pair)
    for link in links:
        if link.disposition != "active":
            continue
        owners = sample_owners.setdefault(link.healthkit_sample_uuid, set())
        owners.add(link.component_id)
        identity = (link.component_id, link.healthkit_type, link.sync_identifier)
        sync_samples.setdefault(identity, set()).add(link.healthkit_sample_uuid)
    for identity, samples in sync_samples.items():
        if len(samples) > 1:
            _fail(
                "sync_identity_on_multiple_samples",
                "{identity} is active on more than one sample",
                identity=identity[2],
            )
    for sample, owners in sample_owners.items():
        if len(owners) > 1:
            _fail(
                "sample_on_multiple_components",
                "{sample} is active on more than one component",
                sample=sample,
            )


def _check_links_against_facts(links: list[HealthKitLink], facts: list[Fact]) -> None:
    by_component: dict[str, Fact] = {}
    for fact in facts:
        if fact.component_id in by_component:
            _fail(
                "duplicate_component_id",
                "{component_id} appears on more than one fact",
                component_id=fact.component_id,
            )
        by_component[fact.component_id] = fact
    for link in links:
        fact = by_component.get(link.component_id)
        if fact is None:
            _fail(
                "unknown_link_component",
                "{component_id} is not a fact of this upsert",
                component_id=link.component_id,
            )
        if fact.kind != "nutrient":
            _fail(
                "link_to_non_nutrient",
                "{component_id} is a {kind}, not a nutrient",
                component_id=link.component_id,
                kind=fact.kind,
            )
        if link.healthkit_type != healthkit_type_for(fact.code):
            _fail(
                "healthkit_type_mismatch",
                "{healthkit_type} is not the type of {code}",
                healthkit_type=link.healthkit_type,
                code=fact.code,
            )


def _offset_matches_zone(occurred_at: str, time_zone: str) -> bool:
    instant = _parse_instant(occurred_at)
    if instant is None:
        return False
    try:
        local = instant.astimezone(ZoneInfo(time_zone))
    except (OverflowError, ValueError):
        return False
    return instant.utcoffset() == local.utcoffset()


class UpsertOperation(_ContractModel):
    operation_id: Uuid
    operation: Literal["upsert"]
    intake_id: Uuid
    revision: PositiveInt64
    projection_sequence: FirstSequence
    occurred_at: Timestamp
    time_zone: TimeZoneName
    recorded_at: Timestamp
    category: Slug
    display_name: Text
    serving: Serving
    facts: Annotated[list[Fact], Field(min_length=1)]
    healthkit_links: list[HealthKitLink]
    nutrition_completeness: NutritionCompleteness
    domain_facts_hash: Digest
    projection_hash: Digest
    client_payload_hash: Digest

    @model_validator(mode="after")
    def _check_rules_beyond_the_schema(self) -> Self:
        _check_links(self.healthkit_links)
        if not _offset_matches_zone(self.occurred_at, self.time_zone):
            _fail(
                "offset_zone_mismatch",
                "the offset of {occurred_at} is not the {time_zone} offset",
                occurred_at=self.occurred_at,
                time_zone=self.time_zone,
            )
        _check_links_against_facts(self.healthkit_links, self.facts)
        return self


class DeleteOperation(_ContractModel):
    operation_id: Uuid
    operation: Literal["delete"]
    intake_id: Uuid
    revision: PositiveInt64
    deleted_at: Timestamp
    domain_facts_hash: Digest
    client_payload_hash: Digest


class LinkProjectionOperation(_ContractModel):
    operation_id: Uuid
    operation: Literal["link_projection"]
    intake_id: Uuid
    revision: PositiveInt64
    projection_sequence: LaterSequence
    healthkit_links: list[HealthKitLink]
    projection_hash: Digest
    client_payload_hash: Digest

    @model_validator(mode="after")
    def _check_rules_beyond_the_schema(self) -> Self:
        _check_links(self.healthkit_links)
        return self


Operation: TypeAlias = Annotated[
    UpsertOperation | DeleteOperation | LinkProjectionOperation,
    Field(discriminator="operation"),
]


class IntakeContextBatchV1(_ContractModel):
    schema_name: Literal["healthrelay.intake-context"] = Field(alias="schema")
    schema_version: Literal["1.0"]
    batch_id: Uuid
    producer_id: ProducerId
    writer_bundle_id: BundleId
    installation_id: Uuid
    operations: Annotated[list[Operation], Field(min_length=1)]

    @model_validator(mode="before")
    @classmethod
    def _reject_floats_and_surrogates(cls, data: object) -> object:
        for error_type, message, path in _violations(data, ""):
            _fail(error_type, "{detail} at {path}", detail=message, path=path)
        return data


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        _describe(
            str(item["type"]),
            str(item["msg"]),
            ".".join(str(part) for part in item["loc"]),
        )
        for item in exc.errors(
            include_url=False, include_context=False, include_input=False
        )
    )


def validate_batch(data: str | bytes | dict[str, object]) -> IntakeContextBatchV1:
    """Parse and validate one batch from JSON text, UTF-8 bytes or a parsed object."""
    parsed: object
    if isinstance(data, bytes):
        try:
            parsed = _parse_json_text(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            message = _describe("invalid_utf8", "input is not valid UTF-8")
            raise IntakeContextContractError(message) from exc
    elif isinstance(data, str):
        parsed = _parse_json_text(data)
    else:
        parsed = data
    if not isinstance(parsed, dict):
        _error("not_an_object", "a batch must be a JSON object")
    try:
        return IntakeContextBatchV1.model_validate(parsed)
    except ValidationError as exc:
        raise IntakeContextContractError(_format_validation_error(exc)) from exc
    except RecursionError as exc:
        message = _describe("nesting_too_deep", "JSON nesting is too deep")
        raise IntakeContextContractError(message) from exc


def _operation_digests(
    batch: JsonObject, operation: JsonObject, kind: str
) -> dict[str, str]:
    digests: dict[str, str] = {}
    if kind in {"upsert", "delete"}:
        body = {k: v for k, v in operation.items() if k not in _DOMAIN_EXCLUDED}
        digests["domain_facts_hash"] = _sha256(
            {"producer_id": batch["producer_id"], **body}
        )
    if kind in {"upsert", "link_projection"}:
        links = sorted(
            cast("list[JsonObject]", operation["healthkit_links"]),
            key=lambda link: (
                cast("str", link["component_id"]),
                cast("str", link["healthkit_sample_uuid"]),
            ),
        )
        digests["projection_hash"] = _sha256(
            {
                "producer_id": batch["producer_id"],
                "intake_id": operation["intake_id"],
                "revision": operation["revision"],
                "projection_sequence": operation["projection_sequence"],
                "healthkit_links": links,
            }
        )
    sent = {k: v for k, v in operation.items() if k != "client_payload_hash"}
    digests["client_payload_hash"] = _sha256(
        {
            "producer_id": batch["producer_id"],
            "writer_bundle_id": batch["writer_bundle_id"],
            "installation_id": batch["installation_id"],
            "schema_version": batch["schema_version"],
            "operation": sent,
        }
    )
    return digests


def _dump(batch: IntakeContextBatchV1) -> tuple[JsonObject, list[JsonObject]]:
    document = cast("JsonObject", batch.model_dump(mode="json", exclude_unset=True))
    return document, cast("list[JsonObject]", document["operations"])


def expected_digests(batch: IntakeContextBatchV1) -> list[dict[str, str]]:
    """Recompute the digests each operation carries, one dict per operation."""
    document, operations = _dump(batch)
    return [
        _operation_digests(document, operation, cast("str", operation["operation"]))
        for operation in operations
    ]


def digest_mismatches(batch: IntakeContextBatchV1) -> list[tuple[str, str]]:
    """(operation_id, field) for each supplied digest that differs when recomputed."""
    _, operations = _dump(batch)
    mismatches: list[tuple[str, str]] = []
    for operation, expected in zip(operations, expected_digests(batch), strict=True):
        mismatches.extend(
            (cast("str", operation["operation_id"]), field)
            for field in _DIGEST_FIELDS
            if field in expected and operation[field] != expected[field]
        )
    return mismatches


__all__ = [
    "IntakeContextBatchV1",
    "IntakeContextContractError",
    "canonical_json",
    "digest_mismatches",
    "expected_digests",
    "healthkit_type_for",
    "validate_batch",
]
