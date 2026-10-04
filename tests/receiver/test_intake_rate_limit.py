"""Per-token rate limiting on POST /v1/intake-context/batches."""

import sqlite3
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import cast, final

import pytest

from health_bridge.receiver.intake_tokens import create_intake_token
from health_bridge.receiver.server import (
    INTAKE_CAPABILITIES_RATE_LIMIT_COUNT,
    INTAKE_RATE_LIMIT_COUNT,
    INTAKE_RATE_LIMIT_WINDOW_SECONDS,
    IntakeRateLimiter,
    PairingRedeemRateLimiter,
    ReceiverHTTPServer,
    ReceiverRequestHandler,
    build_receiver_server,
    server_port,
)
from tests.receiver.test_intake_context_routes import CAP, POST, WORKED, call


def fake_clock(
    start: float = 1_000.0,
) -> tuple[Callable[[], float], Callable[[float], None]]:
    """A monotonic-looking clock a test advances by hand."""
    now = [start]

    def read() -> float:
        return now[0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    return read, advance


@contextmanager
def served(
    db: Path,
    *,
    count: int | None = None,
    window_seconds: float | None = None,
    clock: Callable[[], float] | None = None,
) -> Generator[str]:
    if count is None and window_seconds is None:
        server = build_receiver_server(db, "127.0.0.1", 0, intake_context_enabled=True)
    else:
        server = build_receiver_server(
            db,
            "127.0.0.1",
            0,
            intake_context_enabled=True,
            intake_rate_limit_count=(
                INTAKE_RATE_LIMIT_COUNT if count is None else count
            ),
            intake_rate_limit_window_seconds=(
                INTAKE_RATE_LIMIT_WINDOW_SECONDS
                if window_seconds is None
                else window_seconds
            ),
        )
    if clock is not None:
        server.intake_limiter = IntakeRateLimiter(
            max_batches=server.intake_rate_limit_count,
            window_seconds=server.intake_rate_limit_window_seconds,
            clock=clock,
        )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server_port(server)}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def token_for(db: Path, label: str) -> str:
    return create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label=label
    ).token


def receipt_count(db: Path) -> int:
    with sqlite3.connect(db) as connection:
        row = cast(
            "tuple[int]",
            connection.execute(
                "select count(*) from intake_operation_receipts"
            ).fetchone(),
        )
    return row[0]


def test_batches_up_to_the_limit_are_accepted(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    with served(db, count=3, window_seconds=60.0) as base:
        statuses = [call(base, "POST", POST, issued, WORKED)[0] for _ in range(3)]
    assert statuses == [200, 200, 200]


def test_batch_over_the_limit_is_429_with_retry_after(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    with served(db, count=2, window_seconds=60.0) as base:
        for _ in range(2):
            assert call(base, "POST", POST, issued, WORKED)[0] == 200
        status, body, headers = call(base, "POST", POST, issued, WORKED)
    assert (status, body) == (429, {"error": "rate_limited"})
    assert headers["Connection"].lower() == "close"
    retry_after = headers["Retry-After"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60


def test_each_token_has_its_own_budget(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    first = token_for(db, "a")
    second = token_for(db, "b")
    with served(db, count=1, window_seconds=60.0) as base:
        assert call(base, "POST", POST, first, WORKED)[0] == 200
        assert call(base, "POST", POST, first, WORKED)[0] == 429
        assert call(base, "POST", POST, second, WORKED)[0] == 200


def test_failed_authentication_does_not_consume_budget(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    with served(db, count=2, window_seconds=60.0) as base:
        for _ in range(5):
            assert call(base, "POST", POST, "hri_" + "x" * 43, WORKED)[0] == 401
            assert call(base, "POST", POST, None, WORKED)[0] == 401
        statuses = [call(base, "POST", POST, issued, WORKED)[0] for _ in range(3)]
    assert statuses == [200, 200, 429]


def test_budget_recovers_once_the_window_passes(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    read, advance = fake_clock()
    with served(db, count=2, window_seconds=30.0, clock=read) as base:
        spent = [call(base, "POST", POST, issued, WORKED)[0] for _ in range(2)]
        assert spent == [200, 200]
        assert call(base, "POST", POST, issued, WORKED)[0] == 429
        advance(29.0)
        assert call(base, "POST", POST, issued, WORKED)[0] == 429
        advance(2.0)
        assert call(base, "POST", POST, issued, WORKED)[0] == 200


def test_retry_after_counts_down_as_the_window_ages(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    read, advance = fake_clock()
    with served(db, count=1, window_seconds=20.0, clock=read) as base:
        assert call(base, "POST", POST, issued, WORKED)[0] == 200
        first = int(call(base, "POST", POST, issued, WORKED)[2]["Retry-After"])
        advance(5.0)
        second = int(call(base, "POST", POST, issued, WORKED)[2]["Retry-After"])
    assert first == 20
    assert 1 <= second < first


def test_limited_batch_is_neither_accepted_nor_stored(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    with served(db, count=1, window_seconds=60.0) as base:
        assert call(base, "POST", POST, issued, WORKED)[0] == 200
        stored = receipt_count(db)
        for _ in range(3):
            status, body, _ = call(base, "POST", POST, issued, WORKED)
            assert (status, body) == (429, {"error": "rate_limited"})
    assert stored > 0
    assert receipt_count(db) == stored


def test_capabilities_do_not_spend_the_batch_budget(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    with served(db, count=1, window_seconds=60.0) as base:
        assert call(base, "POST", POST, issued, WORKED)[0] == 200
        for _ in range(4):
            status, body, _ = call(base, "GET", CAP, issued)
            assert status == 200
            assert body["schema"] == "healthrelay.intake-context"


def test_default_limit_is_sixty_batches_a_minute(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    server = build_receiver_server(db, "127.0.0.1", 0, intake_context_enabled=True)
    try:
        assert server.intake_rate_limit_count == 60
        assert server.intake_rate_limit_window_seconds == 60.0
        assert server.intake_limiter.max_batches == 60
        assert server.intake_limiter.window_seconds == 60.0
    finally:
        server.server_close()


def test_a_server_that_skips_receiver_init_still_answers(tmp_path: Path) -> None:
    """A subclass that bypasses ReceiverHTTPServer.__init__ keeps the defaults."""

    @final
    class MinimalServer(ReceiverHTTPServer):
        def __init__(self, db_path: Path) -> None:
            self.db_path: Path = db_path
            self.intake_context_enabled: bool = True
            self.request_timeout_seconds: float = 30.0
            self.mailbox_key_store = None
            self.mailbox_connection_store = None
            self.mailbox_worker = None
            self.pairing_redeem_limiter = PairingRedeemRateLimiter()
            ThreadingHTTPServer.__init__(self, ("127.0.0.1", 0), ReceiverRequestHandler)

    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    server = MinimalServer(db)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server_port(server)}"
        assert server.intake_rate_limit_count == 60
        assert call(base, "POST", POST, issued, WORKED)[0] == 200
        assert call(base, "GET", CAP, issued)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_idle_token_keys_are_dropped() -> None:
    now = [500.0]
    limiter = IntakeRateLimiter(
        max_batches=1,
        window_seconds=10.0,
        clock=lambda: now[0],
    )
    for index in range(limiter.max_tokens + 10):
        now[0] = 500.0 + index * 30.0
        assert limiter.allow(f"hri_{index:04d}")
    assert len(limiter.tracked_clients) <= limiter.max_tokens


@pytest.mark.parametrize(
    ("max_batches", "window_seconds"),
    [(0, 60.0), (-1, 60.0), (1, 0.0), (1, -5.0)],
)
def test_limiter_rejects_nonpositive_settings(
    max_batches: int, window_seconds: float
) -> None:
    with pytest.raises(ValueError, match="intake rate limit"):
        _ = IntakeRateLimiter(max_batches=max_batches, window_seconds=window_seconds)


def test_limiter_samples_the_clock_while_holding_its_lock() -> None:
    holder: list[IntakeRateLimiter] = []
    seen: list[bool] = []

    def clock() -> float:
        seen.append(holder[0]._lock.locked())  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        return 100.0

    limiter = IntakeRateLimiter(max_batches=1, window_seconds=60.0, clock=clock)
    holder.append(limiter)
    assert limiter.allow("tok_a")
    assert not limiter.allow("tok_a")
    _ = limiter.retry_after_seconds("tok_a")
    assert seen
    assert all(seen)


def test_capabilities_have_their_own_limit_with_retry_after(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    with served(db) as base:
        statuses = [
            call(base, "GET", CAP, issued)[0]
            for _ in range(INTAKE_CAPABILITIES_RATE_LIMIT_COUNT)
        ]
        status, body, headers = call(base, "GET", CAP, issued)
        batch_status = call(base, "POST", POST, issued, WORKED)[0]
    assert statuses == [200] * INTAKE_CAPABILITIES_RATE_LIMIT_COUNT
    assert (status, body) == (429, {"error": "rate_limited"})
    assert headers["Retry-After"].isdigit()
    # A polling loop on capabilities never blocks real uploads.
    assert batch_status == 200


def test_capabilities_are_privately_cacheable(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = token_for(db, "a")
    with served(db) as base:
        status, _, headers = call(base, "GET", CAP, issued)
    assert status == 200
    # Authenticated: a shared cache must never store it ("private"), the client may.
    assert headers["Cache-Control"] == "private, max-age=300"
    # A cached copy belongs to one credential: another token must not reuse it.
    assert headers["Vary"] == "Authorization"


def test_idle_key_cleanup_tolerates_an_empty_window() -> None:
    read, advance = fake_clock()
    limiter = IntakeRateLimiter(max_batches=2, window_seconds=10.0, clock=read)
    assert limiter.allow("tok_a")
    limiter._hits["tok_empty"] = deque()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    advance(11.0)
    assert limiter.allow("tok_b")
    assert "tok_a" not in limiter.tracked_clients
