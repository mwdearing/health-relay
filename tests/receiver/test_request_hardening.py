from __future__ import annotations

import json
import socket
import sqlite3
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, cast

import pytest
from typing_extensions import override

import health_bridge.receiver.server as server_module
from health_bridge.receiver.intake_tokens import create_intake_token
from health_bridge.receiver.server import (
    ReceiverHTTPServer,
    ReceiverRequestHandler,
    build_receiver_server,
    server_port,
)

if TYPE_CHECKING:
    from collections.abc import Generator

WORKED = Path("tests/fixtures/intake_context/valid_worked_example.json").read_bytes()


def request_bytes(token: str, *, length: str | None = None) -> bytes:
    framing = "" if length is None else f"Content-Length: {length}\r\n"
    return (
        "POST /v1/intake-context/batches HTTP/1.1\r\nHost: x\r\n"
        f"Authorization: Bearer {token}\r\n{framing}\r\n"
    ).encode()


def memory_request(db: Path, request: bytes, *, stream: BytesIO | None = None) -> bytes:
    server = ReceiverHTTPServer.__new__(ReceiverHTTPServer)
    server.db_path = db
    server.intake_context_enabled = True
    server.mailbox_worker = None
    handler = ReceiverRequestHandler.__new__(ReceiverRequestHandler)
    handler.server = server
    handler.client_address = ("127.0.0.1", 1)
    handler.rfile = BytesIO(request) if stream is None else stream
    output = BytesIO()
    handler.wfile = output
    handler.handle_one_request()
    assert handler.close_connection
    return output.getvalue()


def response_parts(response: bytes) -> tuple[int, bytes, dict[str, object]]:
    header, _, body = response.partition(b"\r\n\r\n")
    assert header.startswith(b"HTTP/"), response
    return (
        int(header.split()[1]),
        header.lower(),
        cast("dict[str, object]", json.loads(body)),
    )


class BodyTimeoutStream(BytesIO):
    @override
    def read(self, size: int | None = -1, /) -> bytes:
        del size
        raise TimeoutError


class HeaderTimeoutStream(BytesIO):
    @override
    def readline(self, size: int | None = -1, /) -> bytes:
        if self.tell() > 0:
            raise TimeoutError
        return super().readline(size)


def test_body_timeout_answers_408_and_closes(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    request = request_bytes(issued.token, length="100")
    response = memory_request(db, request, stream=BodyTimeoutStream(request))
    status, headers, body = response_parts(response)
    assert status == 408
    assert body == {"error": "request_timeout"}
    assert b"connection: close" in headers


def test_header_timeout_answers_408_and_closes(tmp_path: Path) -> None:
    request = b"GET /health HTTP/1.1\r\nHost: x\r\n"
    response = memory_request(
        tmp_path / "unused.sqlite", request, stream=HeaderTimeoutStream(request)
    )
    status, headers, body = response_parts(response)
    assert status == 408
    assert body == {"error": "request_timeout"}
    assert b"connection: close" in headers


def test_unexpected_acceptance_error_is_redacted_and_health_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        message = "synthetic-private-detail"
        raise RuntimeError(message)

    monkeypatch.setattr(server_module, "accept_batch", fail)
    response = memory_request(
        db, request_bytes(issued.token, length=str(len(WORKED))) + WORKED
    )
    status, headers, body = response_parts(response)
    assert status == 500
    assert body == {"error": "internal_error"}
    assert b"connection: close" in headers
    assert "synthetic-private-detail" not in caplog.text
    assert issued.token not in caplog.text
    assert [record.getMessage() for record in caplog.records] == ["RuntimeError"]
    assert all(record.exc_info is None for record in caplog.records)
    health = memory_request(db, b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
    assert response_parts(health)[0] == 200


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, OSError])
def test_existing_storage_error_response_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error_type

    monkeypatch.setattr(server_module, "accept_batch", fail)
    response = memory_request(
        db, request_bytes(issued.token, length=str(len(WORKED))) + WORKED
    )
    status, _, body = response_parts(response)
    assert status == 500
    assert body == {"error": "records could not be stored"}


@pytest.mark.parametrize(
    ("length", "status", "error"),
    [
        (None, 411, "content_length_required"),
        ("5000001", 413, "batch_too_large"),
        ("+100", 400, "invalid_content_length"),
    ],
)
def test_existing_framing_errors_are_preserved(
    tmp_path: Path, length: str | None, status: int, error: str
) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    response = memory_request(db, request_bytes(issued.token, length=length))
    actual, headers, body = response_parts(response)
    assert actual == status
    assert body == {"error": error}
    assert b"connection: close" in headers


@contextmanager
def served(db: Path) -> Generator[int]:
    server = build_receiver_server(
        db, "127.0.0.1", 0, intake_context_enabled=True, request_timeout_seconds=0.1
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server_port(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def socket_request(port: int, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.sendall(request)
        response = b""
        while chunk := sock.recv(65536):
            response += chunk
    return response


def test_short_body_socket_timeout_releases_connection(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db) as port:
        started = time.monotonic()
        response = socket_request(
            port, request_bytes(issued.token, length="100") + b"{"
        )
        assert time.monotonic() - started < 1.5
        status, headers, body = response_parts(response)
        assert status == 408
        assert body == {"error": "request_timeout"}
        assert b"connection: close" in headers
        assert (
            response_parts(
                socket_request(port, b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
            )[0]
            == 200
        )


def test_complete_requests_are_unaffected_by_socket_timeout(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db) as port:
        response = socket_request(
            port, request_bytes(issued.token, length=str(len(WORKED))) + WORKED
        )
        status, _, body = response_parts(response)
        assert status == 200
        results = cast("list[dict[str, object]]", body["results"])
        assert results[0]["result"] == "accepted"


class MemorySocket:
    def __init__(self, stream: BytesIO) -> None:
        self.stream: BytesIO = stream
        self.output: bytearray = bytearray()
        self.timeout: float | None = None

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def makefile(self, mode: str, buffering: int) -> BytesIO:
        assert mode == "rb"
        assert buffering == -1
        return self.stream

    def sendall(self, data: bytes) -> None:
        self.output.extend(data)


class IdleTimeoutStream(BytesIO):
    @override
    def readline(self, size: int | None = -1, /) -> bytes:
        line = super().readline(size)
        if not line:
            raise TimeoutError
        return line


@pytest.mark.parametrize("timeout", [30.0, 0.1])
def test_handler_applies_read_timeout_before_processing_request(
    tmp_path: Path, timeout: float
) -> None:
    server = ReceiverHTTPServer.__new__(ReceiverHTTPServer)
    server.db_path = tmp_path / "unused.sqlite"
    server.intake_context_enabled = False
    server.mailbox_worker = None
    server.request_timeout_seconds = timeout
    connection = MemorySocket(BytesIO(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n"))
    _ = ReceiverRequestHandler(
        cast("socket.socket", cast("object", connection)), ("127.0.0.1", 1), server
    )
    assert connection.timeout == timeout
    assert response_parts(bytes(connection.output))[0] == 200


def test_idle_keep_alive_timeout_closes_without_a_second_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ReceiverRequestHandler, "protocol_version", "HTTP/1.1")
    server = ReceiverHTTPServer.__new__(ReceiverHTTPServer)
    server.db_path = tmp_path / "unused.sqlite"
    server.intake_context_enabled = False
    server.mailbox_worker = None
    server.request_timeout_seconds = 0.1
    connection = MemorySocket(
        IdleTimeoutStream(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
    )
    handler = ReceiverRequestHandler(
        cast("socket.socket", cast("object", connection)), ("127.0.0.1", 1), server
    )
    assert handler.close_connection
    assert connection.output.count(b"HTTP/1.1") == 1
    assert response_parts(bytes(connection.output))[0] == 200


def test_complete_intake_request_still_commits_in_memory(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    response = memory_request(
        db, request_bytes(issued.token, length=str(len(WORKED))) + WORKED
    )
    status, _, body = response_parts(response)
    assert status == 200
    results = cast("list[dict[str, object]]", body["results"])
    assert results[0]["result"] == "accepted"


def test_unexpected_error_over_socket_keeps_server_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )

    def fail(*_args: object, **_kwargs: object) -> None:
        message = "synthetic-private-detail"
        raise RuntimeError(message)

    monkeypatch.setattr(server_module, "accept_batch", fail)
    with served(db) as port:
        response = socket_request(
            port, request_bytes(issued.token, length=str(len(WORKED))) + WORKED
        )
        status, headers, body = response_parts(response)
        assert status == 500
        assert body == {"error": "internal_error"}
        assert b"connection: close" in headers
        assert b"synthetic-private-detail" not in response
        health = socket_request(port, b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        assert response_parts(health)[0] == 200


def test_failure_after_headers_closes_without_appending_a_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ReceiverRequestHandler.do_GET

    def fail_after_response(handler: ReceiverRequestHandler) -> None:
        original(handler)
        raise RuntimeError

    monkeypatch.setattr(ReceiverRequestHandler, "do_GET", fail_after_response)
    response = memory_request(
        tmp_path / "unused.sqlite", b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n"
    )
    assert response.count(b"HTTP/1.0") == 1
    status, _, body = response_parts(response)
    assert status == 200
    assert body["status"] == "ok"
    assert b"internal_error" not in response


def test_failure_before_headers_are_sent_returns_only_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ReceiverRequestHandler.send_header
    failed = False

    def fail_once(handler: ReceiverRequestHandler, name: str, value: str) -> None:
        nonlocal failed
        if name == "Content-Type" and not failed:
            failed = True
            raise RuntimeError
        original(handler, name, value)

    monkeypatch.setattr(ReceiverRequestHandler, "send_header", fail_once)
    response = memory_request(
        tmp_path / "unused.sqlite", b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n"
    )
    status, headers, body = response_parts(response)
    assert status == 500
    assert headers.count(b"http/1.0") == 1
    assert b"connection: close" in headers
    assert body == {"error": "internal_error"}


def test_partial_request_line_timeout_answers_408(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    with served(db) as port:
        response = socket_request(port, b"GET /hea")
    status, headers, body = response_parts(response)
    assert status == 408
    assert body == {"error": "request_timeout"}
    assert b"connection: close" in headers
