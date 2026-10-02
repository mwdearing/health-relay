import re
from datetime import UTC, datetime
from typing import Annotated, ClassVar, Final, Literal, LiteralString, Self, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

SCHEMA_MAJOR_ERROR_TYPE: Final = "unsupported_schema_major"
SCHEMA_MAJOR_ERROR_MESSAGE: Final = "schema_version must match major version 1"
EXPLICIT_NULL_ERROR_TYPE: Final = "explicit_null_not_allowed"
EXPLICIT_NULL_ERROR_MESSAGE: Final = "field may be omitted but cannot be null"
ALIASES_UNIQUE_ERROR_TYPE: Final = "aliases_not_unique"
ALIASES_UNIQUE_ERROR_MESSAGE: Final = "aliases must be unique"
SCHEMA_VERSION_PATTERN: Final = re.compile(r"^1\.\d+\.\d+$")
UTC_TIMESTAMP_PATTERN: Final = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
SYNTHETIC_SOURCE_PATTERN: Final = r"^(synthetic|apple_health)\.[a-z0-9_.-]+$"
SYNTHETIC_RECORD_PATTERN: Final = r"^(synthetic|hk)-[a-z0-9-]+$"
TYPE_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]*$"
INTAKE_PREFIX: Final = "intake_"
INTAKE_PAIR_KEYS: Final = ("intake_id", "intake_component_id")
SYNC_PAIR_KEYS: Final = ("sync_identifier", "sync_version")
INTAKE_ID_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
INTAKE_COMPONENT_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SYNC_VERSION_PATTERN: Final = re.compile(r"^[1-9][0-9]{0,18}$")
SYNC_IDENTIFIER_MAX_LENGTH: Final = 256
SYNC_VERSION_MAX: Final = 9223372036854775807
INTAKE_METADATA_ERROR_TYPE: Final = "invalid_intake_metadata"


def validate_utc_timestamp(value: str) -> str:
    try:
        _ = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        error_type = "invalid_utc_timestamp"
        error_message = "timestamp must be a valid UTC timestamp"
        raise PydanticCustomError(
            error_type,
            error_message,
        ) from exc
    return value


def utc_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def validate_order(start_time: str, end_time: str) -> None:
    if utc_datetime(start_time) > utc_datetime(end_time):
        message = "start_time must not be after end_time"
        raise ValueError(message)


UtcTimestamp: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=UTC_TIMESTAMP_PATTERN),
    AfterValidator(validate_utc_timestamp),
]
SyntheticSourceKey: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=SYNTHETIC_SOURCE_PATTERN),
]
SyntheticRecordId: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=SYNTHETIC_RECORD_PATTERN),
]
TypeCode: TypeAlias = Annotated[str, StringConstraints(pattern=TYPE_CODE_PATTERN)]
NonEmptyString: TypeAlias = Annotated[str, StringConstraints(min_length=1)]
NonNegativeFloat: TypeAlias = Annotated[float, Field(ge=0)]
NonNegativeInt: TypeAlias = Annotated[int, Field(ge=0)]
FiniteFloat: TypeAlias = Annotated[float, Field(allow_inf_nan=False)]
JsonNumber: TypeAlias = int | float


def reject_explicit_null_string(value: str | None) -> str | None:
    if value is None:
        raise PydanticCustomError(EXPLICIT_NULL_ERROR_TYPE, EXPLICIT_NULL_ERROR_MESSAGE)
    return value


def reject_explicit_null_number(value: JsonNumber | None) -> JsonNumber | None:
    if value is None:
        raise PydanticCustomError(EXPLICIT_NULL_ERROR_TYPE, EXPLICIT_NULL_ERROR_MESSAGE)
    return value


OmittableString: TypeAlias = Annotated[
    str | None,
    BeforeValidator(reject_explicit_null_string),
]
OmittableNonNegativeFloat: TypeAlias = Annotated[
    NonNegativeFloat | None,
    BeforeValidator(reject_explicit_null_number),
]
OmittableFiniteFloat: TypeAlias = Annotated[
    FiniteFloat | None,
    BeforeValidator(reject_explicit_null_number),
]


class StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class TimeWindow(StrictModel):
    start_time: UtcTimestamp
    end_time: UtcTimestamp

    @model_validator(mode="after")
    def reject_reversed_window(self) -> Self:
        validate_order(self.start_time, self.end_time)
        return self


class Source(StrictModel):
    source_key: SyntheticSourceKey
    name: NonEmptyString
    kind: Literal["phone", "watch", "app", "manual"]
    bundle_id: OmittableString = None
    device_model: OmittableString = None


class HealthType(StrictModel):
    type_code: TypeCode
    display_name: NonEmptyString
    category: Literal[
        "activity",
        "blood_respiratory",
        "body",
        "environmental",
        "fitness",
        "heart",
        "other",
        "provider_specific",
        "sleep",
        "workout",
    ]
    default_unit: NonEmptyString
    sensitivity: Literal["low", "moderate", "high"]
    aliases: tuple[NonEmptyString, ...]

    @field_validator("aliases")
    @classmethod
    def reject_duplicate_aliases(
        cls,
        value: tuple[NonEmptyString, ...],
    ) -> tuple[NonEmptyString, ...]:
        if len(value) == len(set(value)):
            return value
        raise PydanticCustomError(
            ALIASES_UNIQUE_ERROR_TYPE,
            ALIASES_UNIQUE_ERROR_MESSAGE,
        )


class Sample(StrictModel):
    client_record_id: SyntheticRecordId
    source_key: SyntheticSourceKey
    type_code: TypeCode
    start_time: UtcTimestamp
    end_time: UtcTimestamp
    value: float
    unit: NonEmptyString
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_reversed_interval(self) -> Self:
        validate_order(self.start_time, self.end_time)
        return self

    @field_validator("metadata")
    @classmethod
    def validate_intake_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        validate_intake_metadata(value)
        return value


def _structure_problem(metadata: dict[str, str]) -> LiteralString | None:
    for key in metadata:
        if key.startswith(INTAKE_PREFIX) and key not in INTAKE_PAIR_KEYS:
            return "unknown intake_ metadata key"
    for pair in (INTAKE_PAIR_KEYS, SYNC_PAIR_KEYS):
        present = [key in metadata for key in pair]
        if any(present) and not all(present):
            return "metadata keys must travel as a pair"
    return None


def _value_problem(metadata: dict[str, str]) -> LiteralString | None:
    if "intake_id" in metadata:
        if INTAKE_ID_PATTERN.fullmatch(metadata["intake_id"]) is None:
            return "intake_id must be a lowercase canonical UUID"
        if INTAKE_COMPONENT_PATTERN.fullmatch(metadata["intake_component_id"]) is None:
            return "intake_component_id must be a lowercase slug"
    if "sync_identifier" in metadata:
        if not 1 <= len(metadata["sync_identifier"]) <= SYNC_IDENTIFIER_MAX_LENGTH:
            return "sync_identifier must be 1 to 256 characters"
        version = metadata["sync_version"]
        if (
            SYNC_VERSION_PATTERN.fullmatch(version) is None
            or int(version) > SYNC_VERSION_MAX
        ):
            return "sync_version must be decimal text from 1 to 2^63-1"
    return None


def validate_intake_metadata(metadata: dict[str, str]) -> None:
    problem = _structure_problem(metadata) or _value_problem(metadata)
    if problem is not None:
        raise PydanticCustomError(INTAKE_METADATA_ERROR_TYPE, problem)


class Workout(StrictModel):
    client_record_id: SyntheticRecordId
    source_key: SyntheticSourceKey
    workout_type: NonEmptyString
    start_time: UtcTimestamp
    end_time: UtcTimestamp
    duration_seconds: NonNegativeInt
    energy_kcal: OmittableNonNegativeFloat = None
    distance_meters: OmittableNonNegativeFloat = None

    @model_validator(mode="after")
    def reject_inconsistent_duration(self) -> Self:
        validate_order(self.start_time, self.end_time)
        elapsed_seconds = int(
            (
                utc_datetime(self.end_time) - utc_datetime(self.start_time)
            ).total_seconds()
        )
        if self.duration_seconds > elapsed_seconds:
            message = "duration_seconds must not exceed workout interval"
            raise ValueError(message)
        return self


EcgClassification: TypeAlias = Literal[
    "not_set",
    "sinus_rhythm",
    "atrial_fibrillation",
    "inconclusive_low_heart_rate",
    "inconclusive_high_heart_rate",
    "inconclusive_poor_reading",
    "inconclusive_other",
    "unrecognized",
]
EcgSymptomsStatus: TypeAlias = Literal["not_set", "none", "present"]


class Electrocardiogram(StrictModel):
    """One Apple Watch ECG recording (schema 1.1, optional top-level array).

    Voltages are optional: a sender may ship only the summary. When present,
    their count must equal ``voltage_count``.
    """

    client_record_id: SyntheticRecordId
    source_key: SyntheticSourceKey
    start_time: UtcTimestamp
    end_time: UtcTimestamp
    classification: EcgClassification
    symptoms_status: EcgSymptomsStatus
    average_heart_rate_bpm: OmittableNonNegativeFloat = None
    sampling_frequency_hz: OmittableNonNegativeFloat = None
    voltage_count: NonNegativeInt
    voltages_microvolts: tuple[FiniteFloat, ...] = ()

    @model_validator(mode="after")
    def reject_reversed_interval_and_voltage_mismatch(self) -> Self:
        validate_order(self.start_time, self.end_time)
        if (
            self.voltages_microvolts
            and len(self.voltages_microvolts) != self.voltage_count
        ):
            message = "voltages_microvolts length must equal voltage_count"
            raise ValueError(message)
        return self


MedicationDoseStatus: TypeAlias = Literal[
    "taken",
    "skipped",
    "not_interacted",
    "snoozed",
    "not_logged",
    "notification_not_sent",
    "unknown",
]


class MedicationDoseEvent(StrictModel):
    """One HealthKit medication dose event (HealthRelay fork, optional top-level array).

    Names and doses are the user's own records; the receiver stores them as-is and the
    host decides what may leave it (Telegram gets counts only).
    """

    client_record_id: SyntheticRecordId
    source_key: SyntheticSourceKey
    medication_name: NonEmptyString
    medication_concept_key: OmittableString = None
    status: MedicationDoseStatus
    status_raw: int
    start_time: UtcTimestamp
    scheduled_time: OmittableString = None
    dose: OmittableNonNegativeFloat = None
    unit: OmittableString = None

    @model_validator(mode="after")
    def reject_invalid_scheduled_time(self) -> Self:
        if self.scheduled_time is not None:
            _ = validate_utc_timestamp(self.scheduled_time)
        return self


class LabResult(StrictModel):
    """One FHIR clinical-record Observation from an Apple Health export.zip.

    HealthRelay fork, optional top-level array; the on-device export importer's only
    record family -- ECG and medications sync live via HealthKit and never need a
    manual export.

    Fields mirror the source Observation loosely (LOINC code, display name, category,
    effective date as the source gave it, numeric or free-text value, reference range).
    The receiver stores them as-is; the host decides in/out-of-range flags and what may
    leave it (Telegram gets counts only).
    """

    client_record_id: SyntheticRecordId
    source_key: SyntheticSourceKey
    loinc: OmittableString = None
    name: NonEmptyString
    category: OmittableString = None
    effective_date: NonEmptyString
    value_num: OmittableFiniteFloat = None
    unit: OmittableString = None
    value_text: OmittableString = None
    ref_low: OmittableFiniteFloat = None
    ref_high: OmittableFiniteFloat = None
    ref_text: OmittableString = None


class SleepStageInterval(StrictModel):
    stage: Literal["in_bed", "awake", "core", "deep", "rem"]
    start_time: UtcTimestamp
    end_time: UtcTimestamp

    @model_validator(mode="after")
    def reject_reversed_interval(self) -> Self:
        validate_order(self.start_time, self.end_time)
        return self


class SleepSession(StrictModel):
    client_record_id: SyntheticRecordId
    source_key: SyntheticSourceKey
    start_time: UtcTimestamp
    end_time: UtcTimestamp
    stage_intervals: tuple[SleepStageInterval, ...]

    @model_validator(mode="after")
    def reject_out_of_bounds_stages(self) -> Self:
        validate_order(self.start_time, self.end_time)
        session_start = utc_datetime(self.start_time)
        session_end = utc_datetime(self.end_time)
        if any(
            utc_datetime(stage.start_time) < session_start
            or utc_datetime(stage.end_time) > session_end
            for stage in self.stage_intervals
        ):
            message = "sleep stage must be contained within its session"
            raise ValueError(message)
        return self


class DeletedRecord(StrictModel):
    record_family: Literal[
        "sample",
        "workout",
        "sleep_session",
        "electrocardiogram",
        "medication_dose_event",
        "lab_result",
    ]
    source_key: SyntheticSourceKey
    client_record_id: SyntheticRecordId
    deleted_at: UtcTimestamp


class SyncCursor(StrictModel):
    source_key: SyntheticSourceKey
    cursor_kind: NonEmptyString
    cursor_value: NonEmptyString


class SyncContext(StrictModel):
    sync_window: TimeWindow
    cursors: tuple[SyncCursor, ...]


class HealthBridgeBatchV1(StrictModel):
    schema_id: Literal["health_bridge.batch.v1"]
    schema_version: str
    generated_at: UtcTimestamp
    export_window: TimeWindow
    sources: tuple[Source, ...] = Field(min_length=1)
    health_types: tuple[HealthType, ...] = Field(min_length=1)
    samples: tuple[Sample, ...]
    workouts: tuple[Workout, ...]
    electrocardiograms: tuple[Electrocardiogram, ...] = ()
    medication_dose_events: tuple[MedicationDoseEvent, ...] = ()
    lab_results: tuple[LabResult, ...] = ()
    sleep_sessions: tuple[SleepSession, ...]
    deleted_records: tuple[DeletedRecord, ...]
    sync: SyncContext

    @field_validator("schema_version")
    @classmethod
    def reject_unknown_major_version(cls, value: str) -> str:
        if SCHEMA_VERSION_PATTERN.fullmatch(value):
            return value
        raise PydanticCustomError(
            SCHEMA_MAJOR_ERROR_TYPE,
            SCHEMA_MAJOR_ERROR_MESSAGE,
        )

    @model_validator(mode="after")
    def reject_sync_window_outside_export_window(self) -> Self:
        export_start = utc_datetime(self.export_window.start_time)
        export_end = utc_datetime(self.export_window.end_time)
        sync_start = utc_datetime(self.sync.sync_window.start_time)
        sync_end = utc_datetime(self.sync.sync_window.end_time)
        if sync_start < export_start or sync_end > export_end:
            message = "sync window must be contained within export window"
            raise ValueError(message)
        return self
