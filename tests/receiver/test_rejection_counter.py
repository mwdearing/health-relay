from __future__ import annotations

import logging

import pytest

from health_bridge.receiver.rejection_counter import (
    REJECTION_LOGGER_NAME,
    RejectionCounter,
)


class FakeClock:
    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now


def _line(
    *, unauthenticated: int, connection_refused: int, deadline_expired: int
) -> str:
    return (
        "receiver rejected requests in the last 60s: "
        f"unauthenticated={unauthenticated} "
        f"connection_refused={connection_refused} "
        f"deadline_expired={deadline_expired}"
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def lines() -> list[str]:
    return []


@pytest.fixture
def counter(clock: FakeClock, lines: list[str]) -> RejectionCounter:
    return RejectionCounter(clock=clock, emit=lines.append)


def test_no_summary_without_events(
    counter: RejectionCounter, clock: FakeClock, lines: list[str]
) -> None:
    clock.now += 600
    assert lines == []
    assert counter.totals() == {
        "unauthenticated": 0,
        "connection_refused": 0,
        "deadline_expired": 0,
    }


def test_one_summary_line_per_window(
    counter: RejectionCounter, clock: FakeClock, lines: list[str]
) -> None:
    counter.record("unauthenticated")
    clock.now += 10
    counter.record("connection_refused")
    clock.now += 10
    counter.record("unauthenticated")
    assert lines == []
    clock.now += 60
    counter.record("deadline_expired")
    assert lines == [_line(unauthenticated=2, connection_refused=1, deadline_expired=0)]


def test_counts_reset_per_window(
    counter: RejectionCounter, clock: FakeClock, lines: list[str]
) -> None:
    counter.record("unauthenticated")
    clock.now += 60
    counter.record("connection_refused")
    clock.now += 60
    counter.record("deadline_expired")
    assert lines == [
        _line(unauthenticated=1, connection_refused=0, deadline_expired=0),
        _line(unauthenticated=0, connection_refused=1, deadline_expired=0),
    ]


def test_totals_accumulate_across_windows(
    counter: RejectionCounter, clock: FakeClock
) -> None:
    counter.record("unauthenticated")
    counter.record("unauthenticated")
    clock.now += 61
    counter.record("connection_refused")
    counter.record("deadline_expired")
    assert counter.totals() == {
        "unauthenticated": 2,
        "connection_refused": 1,
        "deadline_expired": 1,
    }


def test_summary_is_at_most_once_per_sixty_seconds(
    counter: RejectionCounter, clock: FakeClock, lines: list[str]
) -> None:
    for _ in range(50):
        counter.record("unauthenticated")
        clock.now += 1
    assert lines == []
    clock.now += 10
    counter.record("unauthenticated")
    assert len(lines) == 1


def test_summary_line_contains_only_the_three_counts(
    counter: RejectionCounter, clock: FakeClock, lines: list[str]
) -> None:
    counter.record("unauthenticated")
    clock.now += 61
    counter.record("connection_refused")
    line = lines[0]
    assert line.startswith("receiver rejected requests in the last 60s: ")
    assert line.split(": ", 1)[1].split() == [
        "unauthenticated=1",
        "connection_refused=0",
        "deadline_expired=0",
    ]
    assert "127.0.0.1" not in line
    assert "/" not in line
    assert "Bearer" not in line


def test_default_emit_uses_warning_on_module_logger(
    clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    counter = RejectionCounter(clock=clock)
    with caplog.at_level(logging.WARNING, logger=REJECTION_LOGGER_NAME):
        counter.record("unauthenticated")
        clock.now += 61
        counter.record("connection_refused")
    records = [r for r in caplog.records if r.name == REJECTION_LOGGER_NAME]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].getMessage() == _line(
        unauthenticated=1, connection_refused=0, deadline_expired=0
    )


def test_logger_name_is_the_module_name() -> None:
    assert REJECTION_LOGGER_NAME == "health_bridge.receiver.rejection_counter"


def test_flush_reports_a_burst_that_stopped(clock: FakeClock, lines: list[str]) -> None:
    # Given a burst with no later rejection
    counter = RejectionCounter(clock=clock, emit=lines.append)
    counter.record("unauthenticated")
    counter.record("connection_refused")

    # When the window is not yet due, nothing is emitted
    clock.now += 59.0
    counter.flush_if_due()
    assert lines == []

    # Then once it is due a flush emits it exactly once
    clock.now += 1.0
    counter.flush_if_due()
    counter.flush_if_due()
    assert lines == [_line(unauthenticated=1, connection_refused=1, deadline_expired=0)]


def test_forced_flush_emits_an_open_window_and_nothing_when_empty(
    clock: FakeClock, lines: list[str]
) -> None:
    counter = RejectionCounter(clock=clock, emit=lines.append)
    counter.flush_if_due(force=True)
    assert lines == []

    counter.record("deadline_expired")
    counter.flush_if_due(force=True)
    assert lines == [_line(unauthenticated=0, connection_refused=0, deadline_expired=1)]
