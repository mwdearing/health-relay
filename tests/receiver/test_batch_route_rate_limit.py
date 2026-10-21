"""Per-token rate limiting on POST /v1/batches (health-relay#134)."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

from health_bridge.receiver.server import (
    BATCH_RATE_LIMIT_COUNT,
    build_receiver_server,
    server_port,
)
from health_bridge.receiver.tokens import create_receiver_token
from tests.receiver.test_intake_context_routes import call


@contextmanager
def served(db: Path, *, count: int | None = None) -> Generator[str]:
    if count is None:
        server = build_receiver_server(db, "127.0.0.1", 0)
    else:
        server = build_receiver_server(db, "127.0.0.1", 0, batch_rate_limit_count=count)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server_port(server)}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_batch_route_answers_429_with_retry_after_over_the_limit(
    tmp_path: Path,
) -> None:
    db = tmp_path / "r.sqlite"
    token = create_receiver_token(db, "a").token
    with served(db, count=2) as base:
        first = [call(base, "POST", "/v1/batches", token, b"{}")[0] for _ in range(2)]
        status, body, headers = call(base, "POST", "/v1/batches", token, b"{}")
    assert first == [422, 422]
    assert (status, body) == (429, {"error": "rate_limited"})
    assert headers["Connection"].lower() == "close"
    assert 1 <= int(headers["Retry-After"]) <= 60


def test_each_batch_token_has_its_own_budget(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    one = create_receiver_token(db, "a").token
    two = create_receiver_token(db, "b").token
    with served(db, count=1) as base:
        assert call(base, "POST", "/v1/batches", one, b"{}")[0] == 422
        assert call(base, "POST", "/v1/batches", one, b"{}")[0] == 429
        assert call(base, "POST", "/v1/batches", two, b"{}")[0] == 422


def test_failed_authentication_does_not_spend_the_budget(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    token = create_receiver_token(db, "a").token
    with served(db, count=1) as base:
        for _ in range(3):
            assert call(base, "POST", "/v1/batches", "wrong-token", b"{}")[0] == 401
        assert call(base, "POST", "/v1/batches", token, b"{}")[0] == 422


def test_default_limit_is_sixty_batches_a_minute() -> None:
    assert BATCH_RATE_LIMIT_COUNT == 120


def test_tokens_sharing_a_prefix_do_not_share_a_budget(tmp_path: Path) -> None:
    db = tmp_path / "r.sqlite"
    one = create_receiver_token(db, "a", token="hb_sharedpfx-one").token
    two = create_receiver_token(db, "b", token="hb_sharedpfx-two").token
    with served(db, count=1) as base:
        assert call(base, "POST", "/v1/batches", one, b"{}")[0] == 422
        assert call(base, "POST", "/v1/batches", one, b"{}")[0] == 429
        assert call(base, "POST", "/v1/batches", two, b"{}")[0] == 422
