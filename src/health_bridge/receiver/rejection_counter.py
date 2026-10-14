"""Count refused receiver requests and log one counts-only summary per window.

The receiver deliberately logs no request detail, so a burst of rejected traffic
would otherwise be invisible. This counter keeps three category totals and emits
a single WARNING line per window. The line carries counts only: no client
address, request path, header, token or body.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import TYPE_CHECKING, Final, Literal, final

if TYPE_CHECKING:
    from collections.abc import Callable

RejectionKind = Literal["unauthenticated", "connection_refused", "deadline_expired"]

REJECTION_LOGGER_NAME: Final = __name__
REJECTION_WINDOW_SECONDS: Final = 60.0
REJECTION_KINDS: Final[tuple[RejectionKind, ...]] = (
    "unauthenticated",
    "connection_refused",
    "deadline_expired",
)

_LOGGER = logging.getLogger(REJECTION_LOGGER_NAME)


def _zero_counts() -> dict[RejectionKind, int]:
    return dict.fromkeys(REJECTION_KINDS, 0)


def _log_warning(message: str) -> None:
    _LOGGER.warning("%s", message)


@final
class RejectionCounter:
    """Thread-safe counter that emits at most one summary line per window.

    Emission is lazy: a window closes when a later event, or a ``flush_if_due``
    call, arrives after it has lasted ``window_seconds``. The server calls
    ``flush_if_due`` on every request and at shutdown, so a burst that stops is
    still reported. No timer or background thread is used.
    """

    def __init__(
        self,
        *,
        window_seconds: float = REJECTION_WINDOW_SECONDS,
        clock: Callable[[], float] | None = None,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        if not window_seconds > 0:
            msg = "rejection window must be positive"
            raise ValueError(msg)
        self.window_seconds = window_seconds
        self.clock = time.monotonic if clock is None else clock
        self.emit = _log_warning if emit is None else emit
        self._lock = Lock()
        self._window_start: float | None = None
        self._window_counts: dict[RejectionKind, int] = _zero_counts()
        self._totals: dict[RejectionKind, int] = _zero_counts()

    def record(self, kind: RejectionKind) -> None:
        """Count one rejected request or connection of the given kind."""
        line: str | None = None
        with self._lock:
            now = self.clock()
            if self._window_start is None:
                self._window_start = now
            elif now - self._window_start >= self.window_seconds:
                line = self._close_window_locked()
                self._window_start = now
            self._window_counts[kind] += 1
            self._totals[kind] += 1
        if line is not None:
            self.emit(line)

    def flush_if_due(self, *, force: bool = False) -> None:
        """Emit the open window's summary once it is due, or now when forced."""
        line: str | None = None
        with self._lock:
            if self._window_start is None:
                return
            due = self.clock() - self._window_start >= self.window_seconds
            if force or due:
                line = self._close_window_locked()
                self._window_start = None
        if line is not None:
            self.emit(line)

    def totals(self) -> dict[str, int]:
        """Cumulative counts since construction, keyed by category name."""
        with self._lock:
            return {kind: self._totals[kind] for kind in REJECTION_KINDS}

    def _close_window_locked(self) -> str | None:
        """Return the summary for the closed window, or None when it was empty."""
        counts = self._window_counts
        self._window_counts = _zero_counts()
        if not any(counts.values()):
            return None
        return (
            # The window can close long after its last event (lazy flush), so the line
            # makes no claim about how recent the counts are.
            "receiver rejected requests since the previous summary: "
            f"unauthenticated={counts['unauthenticated']} "
            f"connection_refused={counts['connection_refused']} "
            f"deadline_expired={counts['deadline_expired']}"
        )
