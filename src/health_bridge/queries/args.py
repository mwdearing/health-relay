"""Query arguments shared by the MCP tools and the `query` CLI commands.

Both surfaces validate through these models, so a date or timestamp that one
rejects cannot be accepted by the other.
"""

import re
from datetime import date
from typing import ClassVar, Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from health_bridge.contract.batch_v1 import UtcTimestamp, utc_datetime

# One more than a leap year: a full year of days plus the boundary day.
MAX_DAILY_SUMMARY_DAYS: Final = 366
DAILY_SPAN_MESSAGE: Final = (
    f"date range must not exceed {MAX_DAILY_SUMMARY_DAYS} days; narrow the range"
)
TIMESTAMP_HINT: Final = "must look like 2026-06-01T00:00:00Z (UTC, ending in Z)"
DATE_HINT: Final = "must be a date like 2026-06-01 (YYYY-MM-DD)"
TIMESTAMP_FIELDS: Final = frozenset({"start_time", "end_time"})
DATE_FIELDS: Final = frozenset({"start_date", "end_date"})
MAX_FIELD_NAME_LENGTH: Final = 40


class ArgsModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class TimeseriesArgs(ArgsModel):
    type_codes: list[str]
    start_time: UtcTimestamp
    end_time: UtcTimestamp

    @model_validator(mode="after")
    def require_ordered_interval(self) -> Self:
        if utc_datetime(self.start_time) > utc_datetime(self.end_time):
            message = "start_time must not be after end_time"
            raise ValueError(message)
        return self


class DateRangeArgs(ArgsModel):
    start_date: str
    end_date: str

    @field_validator("start_date", "end_date")
    @classmethod
    def require_iso_date(cls, value: str) -> str:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            message = "date must use YYYY-MM-DD"
            raise ValueError(message)
        _ = date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def require_ordered_dates(self) -> Self:
        if date.fromisoformat(self.start_date) > date.fromisoformat(self.end_date):
            message = "start_date must not be after end_date"
            raise ValueError(message)
        return self


class DailySummaryArgs(DateRangeArgs):
    """A date range that also stays within `MAX_DAILY_SUMMARY_DAYS`."""

    @model_validator(mode="after")
    def require_bounded_span(self) -> Self:
        require_daily_span(self.start_date, self.end_date)
        return self


def require_daily_span(start_date: str, end_date: str) -> None:
    """Reject a daily-summary range longer than `MAX_DAILY_SUMMARY_DAYS`.

    The daily summary builds one entry per calendar day, so its size follows
    the span rather than the data.
    """
    span = date.fromisoformat(end_date) - date.fromisoformat(start_date)
    if span.days > MAX_DAILY_SUMMARY_DAYS:
        raise ValueError(DAILY_SPAN_MESSAGE)


def summarize_validation_error(error: ValidationError) -> str:
    """Describe argument problems by field and expected form, never by value."""
    problems: list[str] = []
    for detail in error.errors():
        location = detail["loc"]
        field = ".".join(str(part) for part in location)[:MAX_FIELD_NAME_LENGTH]
        root = str(location[0]) if location else ""
        error_type = detail["type"]
        if not location:
            problem = detail["msg"].removeprefix("Value error, ")
        elif error_type == "missing":
            problem = f"{field}: is required"
        elif error_type == "extra_forbidden":
            problem = f"{field}: is not an accepted argument"
        elif root in TIMESTAMP_FIELDS:
            problem = f"{root}: {TIMESTAMP_HINT}"
        elif root in DATE_FIELDS:
            problem = f"{root}: {DATE_HINT}"
        elif root == "type_codes":
            problem = "type_codes: must be a list of type-code strings"
        else:
            problem = f"{field}: has an invalid value"
        if problem not in problems:
            problems.append(problem)
    return "; ".join(problems)
