from __future__ import annotations

import io
import socket
import time
from threading import Thread
from types import SimpleNamespace
from typing import TYPE_CHECKING, final

import pytest

from health_bridge.receiver.rejection_counter import RejectionCounter, RejectionKind
from health_bridge.receiver.server import (
    ReceiverHTTPServer,
    ReceiverRequestHandler,
    build_receiver_server,
    server_port,
)
from health_bridge.storage.database import initialize_database

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

SECRET_TOKEN = "hbr_SECRET_TOKEN_VALUE"
HEALTH = b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n"


class FakeClock:
    def __init__(self) -> None:
        self.now: float = 500.0

    def __call__(self) -> float:
        return self.now


def status_of(response: bytes) -> int:
    return int(response.split(maxsplit=2)[1])


def socket_request_from(port: int, source_ip: str, request: bytes) -> bytes:
    with socket.create_connection(
        ("127.0.0.1", port), timeout=2, source_address=(source_ip, 0)
    ) as sock:
        sock.sendall(request)
        response = b""
        while chunk := sock.recv(65536):
            response += chunk
    return response


@pytest.fixture
def running(
    tmp_path: Path,
) -> Generator[Callable[..., tuple[ReceiverHTTPServer, FakeClock, list[str]]]]:
    servers: list[ReceiverHTTPServer] = []
    threads: list[Thread] = []

    def start(**kwargs: object) -> tuple[ReceiverHTTPServer, FakeClock, list[str]]:
        db = tmp_path / "r.sqlite"
        initialize_database(db)
        clock = FakeClock()
        lines: list[str] = []
        counter = RejectionCounter(clock=clock, emit=lines.append)
        server = ReceiverHTTPServer(
            host="127.0.0.1",
            port=0,
            db_path=db,
            intake_context_enabled=True,
            rejection_counter=counter,
            **kwargs,  # pyright: ignore[reportArgumentType]
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
        return server, clock, lines

    yield start
    for server, thread in zip(servers, threads, strict=True):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_missing_token_increments_unauthenticated(
    running: Callable[..., tuple[ReceiverHTTPServer, FakeClock, list[str]]],
) -> None:
    server, clock, lines = running(request_timeout_seconds=2)
    port = server_port(server)
    request = b"POST /v1/batches HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n"
    response = socket_request_from(port, "127.0.0.2", request)
    assert status_of(response) == 401
    assert server.rejection_counter.totals()["unauthenticated"] == 1
    assert lines == []

    clock.now += 61
    bad = (
        "POST /v1/intake-context/batches HTTP/1.1\r\nHost: x\r\n"
        f"Authorization: Bearer {SECRET_TOKEN}\r\nContent-Length: 0\r\n\r\n"
    ).encode()
    assert status_of(socket_request_from(port, "127.0.0.3", bad)) == 401
    summary_prefix = "receiver rejected requests since the previous summary: "
    counts = "unauthenticated=1 connection_refused=0 deadline_expired=0"
    assert lines == [summary_prefix + counts]
    emitted = "\n".join(lines)
    for leaked in ("127.0.0.2", "127.0.0.3", "intake-context", SECRET_TOKEN, "Bearer"):
        assert leaked not in emitted


def test_connection_over_per_client_cap_increments_connection_refused(
    running: Callable[..., tuple[ReceiverHTTPServer, FakeClock, list[str]]],
) -> None:
    server, _clock, _lines = running(
        request_timeout_seconds=2,
        max_connections=64,
        max_connections_per_client=1,
    )
    port = server_port(server)
    holder = socket.create_connection(
        ("127.0.0.1", port), timeout=2, source_address=("127.0.0.2", 0)
    )
    time.sleep(0.1)
    try:
        assert socket_request_from(port, "127.0.0.2", HEALTH) == b""
        assert server.rejection_counter.totals()["connection_refused"] == 1
        assert status_of(socket_request_from(port, "127.0.0.3", HEALTH)) == 200
        assert server.rejection_counter.totals()["connection_refused"] == 1
    finally:
        holder.close()


def test_connection_over_total_cap_increments_connection_refused(
    running: Callable[..., tuple[ReceiverHTTPServer, FakeClock, list[str]]],
) -> None:
    server, _clock, _lines = running(request_timeout_seconds=2, max_connections=1)
    port = server_port(server)
    holder = socket.create_connection(("127.0.0.1", port), timeout=2)
    time.sleep(0.1)
    try:
        assert socket_request_from(port, "127.0.0.1", HEALTH) == b""
        assert server.rejection_counter.totals()["connection_refused"] == 1
    finally:
        holder.close()


def test_expired_head_deadline_increments_deadline_expired(
    running: Callable[..., tuple[ReceiverHTTPServer, FakeClock, list[str]]],
) -> None:
    server, _clock, _lines = running(request_timeout_seconds=0.3)
    port = server_port(server)
    received = bytearray()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(b"GET /health HTTP/1.1\r\nHost: x\r\n")
        started = time.monotonic()
        while time.monotonic() - started < 2.0:
            try:
                sock.sendall(b"X-Drip: 1\r\n")
            except OSError:
                break
            time.sleep(0.05)
            sock.settimeout(0.01)
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            if not chunk:
                break
            received.extend(chunk)
        sock.settimeout(1)
        try:
            while chunk := sock.recv(65536):
                received.extend(chunk)
        except OSError:
            pass
    assert status_of(bytes(received)) == 408
    assert server.rejection_counter.totals()["deadline_expired"] == 1


# Its own loopback address: on a shared host another local process probing
# 127.0.0.1 ports (seen as stray "GET /" requests) would otherwise be counted.
# This end-to-end check depends on timer order; the deterministic regression
# guard for an idle connection is the handler-level test further down.
IDLE_TEST_HOST = "127.0.0.77"


def test_plain_idle_timeout_is_not_counted_as_deadline(tmp_path: Path) -> None:
    try:
        server = build_receiver_server(
            tmp_path / "r.sqlite",
            IDLE_TEST_HOST,
            0,
            intake_context_enabled=True,
            request_timeout_seconds=0.2,
        )
    except OSError:
        pytest.skip(f"{IDLE_TEST_HOST} is not a local address here (stock macOS)")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection((IDLE_TEST_HOST, server_port(server)), timeout=2):
            time.sleep(0.5)
        assert server.rejection_counter.totals() == {
            "unauthenticated": 0,
            "connection_refused": 0,
            "deadline_expired": 0,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@final
class _TimingOutReader:
    """A request stream whose first read waits out the inactivity timeout."""

    def __init__(self, first_byte: bytes) -> None:
        self.first_byte = first_byte

    def peek(self, _size: int) -> bytes:
        if not self.first_byte:
            raise TimeoutError
        return self.first_byte

    def readline(self, _limit: int) -> bytes:
        raise TimeoutError


def _timed_out_handler(
    first_byte: bytes,
) -> tuple[ReceiverRequestHandler, list[str], io.BytesIO]:
    counted: list[str] = []
    counter = RejectionCounter(emit=lambda _line: None)
    counter_record = counter.record

    def record(kind: RejectionKind) -> None:
        counted.append(kind)
        counter_record(kind)

    wfile = io.BytesIO()
    handler = ReceiverRequestHandler.__new__(ReceiverRequestHandler)
    # Instance attributes only: no socket, no timers. The wall-clock deadline has
    # already won the race against the socket's inactivity timeout.
    vars(handler).update(
        server=SimpleNamespace(
            rejection_counter=SimpleNamespace(
                record=record, flush_if_due=counter.flush_if_due
            ),
            request_timeout_seconds=30.0,
            request_deadline_seconds=30.0,
        ),
        rfile=_TimingOutReader(first_byte),
        wfile=wfile,
        _deadline_has_expired=lambda: True,
    )
    return handler, counted, wfile


def test_idle_timeout_after_the_deadline_expired_is_not_counted() -> None:
    handler, counted, wfile = _timed_out_handler(b"")
    handler.handle_one_request()
    assert counted == []
    assert wfile.getvalue() == b""


def test_started_request_timing_out_after_the_deadline_is_counted() -> None:
    handler, counted, _wfile = _timed_out_handler(b"G")
    handler.handle_one_request()
    assert counted == ["deadline_expired"]
