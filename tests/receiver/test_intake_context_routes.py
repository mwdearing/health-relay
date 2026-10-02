import json
import socket
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from health_bridge.contract.intake_context_v1 import expected_digests, validate_batch
from health_bridge.receiver.intake_tokens import (
    create_intake_token,
    revoke_intake_token,
)
from health_bridge.receiver.server import build_receiver_server, server_port
from health_bridge.receiver.tokens import create_receiver_token

if TYPE_CHECKING:
    from http.client import HTTPResponse

CAP = "/v1/intake-context/capabilities"
POST = "/v1/intake-context/batches"
WORKED = Path("tests/fixtures/intake_context/valid_worked_example.json").read_bytes()


@contextmanager
def served(db: Path, *, enabled: bool | None) -> Generator[str]:
    server = (
        build_receiver_server(db, "127.0.0.1", 0)
        if enabled is None
        else build_receiver_server(db, "127.0.0.1", 0, intake_context_enabled=enabled)
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server_port(server)}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def call(
    base: str,
    method: str,
    path: str,
    token: str | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, object], dict[str, str]]:
    request = Request(base + path, data=body, method=method)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=20) as raw_response:  # pyright: ignore[reportAny]
            response = cast("HTTPResponse", raw_response)
            parsed = cast("dict[str, object]", json.loads(response.read()))
            return response.status, parsed, dict(response.getheaders())
    except HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw or b"{}"), dict(error.headers)


def test_routes_are_404_by_default_and_when_disabled(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    for enabled in (None, False):
        with served(db, enabled=enabled) as base:
            assert call(base, "GET", CAP, issued.token)[0] == 404
            assert call(base, "POST", POST, issued.token, WORKED)[0] == 404


def test_authentication_rules(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    batch = create_receiver_token(db, label="b")
    with served(db, enabled=True) as base:
        for method, path, body in (("GET", CAP, None), ("POST", POST, WORKED)):
            status, _, headers = call(base, method, path)
            assert status == 401
            assert headers["WWW-Authenticate"] == "Bearer"
            assert call(base, method, path, "hri_bogus", body)[0] == 401
            assert call(base, method, path, "garbage", body)[0] == 401
            assert call(base, method, path, batch.token, body)[0] == 403
        assert call(base, "POST", "/v1/batches", issued.token, b"{}")[0] == 403
        revoke_intake_token(db, issued.token_prefix)
        assert call(base, "GET", CAP, issued.token)[0] == 401


def test_capabilities(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        status, body, _ = call(base, "GET", CAP, issued.token)
    assert status == 200
    assert body["schema"] == "healthrelay.intake-context"
    assert body["supported_versions"] == ["1.0"]
    assert isinstance(body["max_body_bytes"], int)


def test_batch_accept_and_replay(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        status, body, _ = call(base, "POST", POST, issued.token, WORKED)
        results = body["results"]
        assert status == 200
        assert isinstance(results, list)
        assert results[0]["result"] == "accepted"
        first = cast("dict[str, object]", results[0])
        assert {"operation_id", "result", "accepted_revision", "detail"} <= set(first)
        replay = call(base, "POST", POST, issued.token, WORKED)[1]["results"]
        assert isinstance(replay, list)
        assert replay[0]["result"] == "duplicate"


def test_producer_mismatch_is_403_without_echo(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    other = create_intake_token(db, owner_id="o", producer_id="another", label="l")
    with served(db, enabled=True) as base:
        status, body, _ = call(base, "POST", POST, other.token, WORKED)
    assert status == 403
    assert body == {"error": "producer_mismatch"}


def test_oversize_and_malformed_bodies(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        for bad in (b"{not json", b"[]", b"{}"):
            status, body, _ = call(base, "POST", POST, issued.token, bad)
            assert status in (400, 403)
            assert "not json" not in json.dumps(body)
            assert issued.token not in json.dumps(body)
        assert call(base, "GET", "/health")[0] == 200


def raw_post(base: str, head: str, body: bytes = b"") -> tuple[int, str, bytes]:
    host, port = base.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(head.encode() + b"\r\n\r\n" + body)
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    header, _, payload = data.partition(b"\r\n\r\n")
    return int(header.split()[1]), header.decode().lower(), payload


def post_head(token: str, extra: str) -> str:
    return (
        f"POST {POST} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\n"
        f"Content-Type: application/json{extra}"
    )


def test_missing_content_length_is_411_and_closes(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        status, header, payload = raw_post(base, post_head(issued.token, ""), WORKED)
    assert status == 411
    assert "connection: close" in header
    assert issued.token.encode() not in payload


def test_bad_content_length_is_400_and_closes(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        for value in ("abc", "-5"):
            head = post_head(issued.token, f"\r\nContent-Length: {value}")
            status, header, _ = raw_post(base, head)
            assert status == 400
            assert "connection: close" in header


def test_oversize_is_413_without_reading_or_storing(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        head = post_head(issued.token, "\r\nContent-Length: 6000000")
        status, header, payload = raw_post(base, head, WORKED)
        assert status == 413
        assert "connection: close" in header
        assert json.loads(payload) == {"error": "batch_too_large"}
    with sqlite3.connect(db) as connection:
        rows = cast(
            "tuple[int]",
            connection.execute(
                "select count(*) from intake_operation_receipts"
            ).fetchone(),
        )
    assert rows == (0,)


def receipt_count(db: Path) -> int:
    with sqlite3.connect(db) as connection:
        row = cast(
            "tuple[int]",
            connection.execute(
                "select count(*) from intake_operation_receipts"
            ).fetchone(),
        )
    return row[0]


def batch_with(operations: list[object]) -> bytes:
    document = cast("dict[str, object]", json.loads(WORKED))
    document["operations"] = operations
    return json.dumps(document).encode()


def test_batches_with_no_usable_operations_are_400(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        bad_batches: list[list[object]] = [[], [{"operation": "upsert"}], [5]]
        for operations in bad_batches:
            status, body, _ = call(
                base, "POST", POST, issued.token, batch_with(operations)
            )
            assert status == 400
            assert body == {"error": "invalid_batch"}
    assert receipt_count(db) == 0


def test_capabilities_list_the_operation_limit(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        body = call(base, "GET", CAP, issued.token)[1]
    assert body["max_operations"] == 100
    assert body["authentication"] == {
        "scheme": "bearer",
        "header": "Authorization",
        "token_type": "intake",
    }
    assert body["features"] == ["upsert", "delete", "link_projection"]
    assert issued.token not in json.dumps(body)


def test_operation_limit_is_enforced_before_processing(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    operation = cast(
        "list[object]", cast("dict[str, object]", json.loads(WORKED))["operations"]
    )
    with served(db, enabled=True) as base:
        over = [operation[0]] * 101
        status, body, _ = call(base, "POST", POST, issued.token, batch_with(over))
        assert status == 413
        assert body == {"error": "too_many_operations"}
        assert receipt_count(db) == 0
        at_limit = [operation[0]] * 100
        status, body, _ = call(base, "POST", POST, issued.token, batch_with(at_limit))
        assert status == 200
        assert len(cast("list[object]", body["results"])) == 100


def test_server_without_the_flag_attribute_returns_404(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    server = build_receiver_server(db, "127.0.0.1", 0, intake_context_enabled=True)
    del server.intake_context_enabled
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server_port(server)}"
        assert call(base, "GET", CAP, issued.token)[0] == 404
        assert call(base, "POST", POST, issued.token, WORKED)[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_rejection_details_never_echo_request_values(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    document = cast("dict[str, object]", json.loads(WORKED))
    operations = cast("list[dict[str, object]]", document["operations"])
    for fact in cast("list[dict[str, object]]", operations[0]["facts"]):
        fact["component_id"] = "marker-dup"
    payload = json.dumps(document).encode()
    with served(db, enabled=True) as base:
        status, body, _ = call(base, "POST", POST, issued.token, payload)
    assert status == 200
    assert "marker-dup" not in json.dumps(body)
    results = cast("list[dict[str, object]]", body["results"])
    assert results[0]["result"] == "permanent_failure"
    assert results[0]["detail"] == "validation failed"


def two_operations() -> list[dict[str, object]]:
    document = cast("dict[str, object]", json.loads(WORKED))
    first = cast("list[dict[str, object]]", document["operations"])[0]
    second = cast("dict[str, object]", json.loads(json.dumps(first)))
    second["operation_id"] = "11111111-2222-4333-8444-555555555555"
    second["intake_id"] = "66666666-7777-4888-8999-aaaaaaaaaaaa"
    for link in cast("list[dict[str, object]]", second["healthkit_links"]):
        link["healthkit_sample_uuid"] = "77777777-8888-4999-8aaa-bbbbbbbbbbbb"
        link["sync_identifier"] = f"intake:{second['intake_id']}:water"
    document["operations"] = [first, second]
    for _ in range(2):  # the payload hash covers the other two digests
        second.update(expected_digests(validate_batch(document))[1])
    return [first, second]


def test_mixed_batch_without_operation_id_is_400(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    first, second = two_operations()
    del second["operation_id"]
    with served(db, enabled=True) as base:
        status, body, _ = call(
            base, "POST", POST, issued.token, batch_with([first, second])
        )
    assert (status, body) == (400, {"error": "invalid_batch"})


def test_mixed_batch_with_non_object_entry_is_400(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    first, _ = two_operations()
    with served(db, enabled=True) as base:
        status, body, _ = call(base, "POST", POST, issued.token, batch_with([first, 5]))
    assert (status, body) == (400, {"error": "invalid_batch"})


def test_matching_counts_still_answer_200(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    operations = two_operations()
    with served(db, enabled=True) as base:
        status, body, _ = call(
            base, "POST", POST, issued.token, batch_with(list(operations))
        )
    assert status == 200
    results = cast("list[dict[str, object]]", body["results"])
    assert [r["result"] for r in results] == ["accepted", "accepted"]


def test_retry_after_a_rejected_mixed_batch_converges(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    first, second = two_operations()
    broken = dict(second)
    del broken["operation_id"]
    with served(db, enabled=True) as base:
        status = call(base, "POST", POST, issued.token, batch_with([first, broken]))[0]
        assert status == 400
        retry = call(base, "POST", POST, issued.token, batch_with([first, second]))[1]
        names = [r["result"] for r in cast("list[dict[str, object]]", retry["results"])]
        assert names[0] in ("accepted", "duplicate")
        assert names[1] == "accepted"
        replay = call(base, "POST", POST, issued.token, batch_with([first, second]))[1]
        names = [
            r["result"] for r in cast("list[dict[str, object]]", replay["results"])
        ]
        assert names == ["duplicate", "duplicate"]


def test_invalid_content_length_formats_are_400(tmp_path: Path) -> None:
    """Non-numeric Content-Length values rejected with 400."""
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        for value in ("+100", "1_00", "0x64", "-0"):
            head = post_head(issued.token, f"\r\nContent-Length: {value}")
            status, header, payload = raw_post(base, head)
            assert status == 400, (
                f"Content-Length={value!r} should be 400, got {status}"
            )
            assert "connection: close" in header
            assert json.loads(payload) == {"error": "invalid_content_length"}


def test_multiple_content_length_headers_is_400(tmp_path: Path) -> None:
    """Multiple Content-Length headers with different values must be rejected."""
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        head = post_head(
            issued.token,
            f"\r\nContent-Length: {len(WORKED)}\r\nContent-Length: {len(WORKED) + 1}",
        )
        status, header, payload = raw_post(base, head)
        assert status == 400
        assert "connection: close" in header
        assert json.loads(payload) == {"error": "invalid_content_length"}


def test_leading_zeros_in_content_length_are_accepted(tmp_path: Path) -> None:
    """Leading zeros are valid digits per RFC 9110 and must be accepted."""
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        head = post_head(issued.token, f"\r\nContent-Length: 0{len(WORKED)}")
        status, _, _ = raw_post(base, head, WORKED)
        assert status == 200, f"Leading zeros should be accepted (got {status})"


def test_trailing_whitespace_in_content_length_is_accepted(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        for padding in (" ", "\t"):
            head = post_head(
                issued.token, f"\r\nContent-Length: {len(WORKED)}{padding}"
            )
            status, _, _ = raw_post(base, head, WORKED)
            assert status == 200


def test_huge_digit_content_length_is_413(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    issued = create_intake_token(
        db, owner_id="o", producer_id="nutrition-app", label="l"
    )
    with served(db, enabled=True) as base:
        head = post_head(issued.token, "\r\nContent-Length: " + "9" * 5000)
        status, header, payload = raw_post(base, head, WORKED)
    assert status == 413
    assert "connection: close" in header
    assert json.loads(payload) == {"error": "batch_too_large"}
