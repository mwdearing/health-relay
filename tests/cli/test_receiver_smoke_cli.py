from __future__ import annotations

import json
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, ClassVar, cast

import pytest
from typer.testing import CliRunner
from typing_extensions import override

from health_bridge.cli import app

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

SYNTHETIC_TOKEN = "hb_SYNTHETICTOKEN0123456789"


class _Recorder(BaseHTTPRequestHandler):
    seen_authorization: ClassVar[list[str | None]]
    redirect_to: ClassVar[str | None] = None
    body: ClassVar[bytes] = b"{}"
    content_type: ClassVar[str] = "application/json"

    def _record(self) -> None:
        type(self).seen_authorization.append(self.headers.get("Authorization"))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        self._record()
        if self.redirect_to is not None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", self.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        _ = self.wfile.write(self.body)

    def do_GET(self) -> None:
        self._record()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Length", "0")
        self.end_headers()

    @override
    def log_message(self, format: str, *args: object) -> None:
        return


def _serve(name: str, **attrs: object) -> tuple[ThreadingHTTPServer, type[_Recorder]]:
    handler = type(name, (_Recorder,), {"seen_authorization": [], **attrs})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


@pytest.fixture
def batch_file(tmp_path: Path) -> Path:
    path = tmp_path / "batch.json"
    _ = path.write_text(json.dumps({"synthetic": True}), encoding="utf-8")
    return path


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token.txt"
    _ = path.write_text(f"{SYNTHETIC_TOKEN}\n", encoding="utf-8")
    return path


@pytest.fixture
def servers() -> Generator[list[ThreadingHTTPServer]]:
    started: list[ThreadingHTTPServer] = []
    yield started
    for server in started:
        server.shutdown()
        server.server_close()


def _url(server: ThreadingHTTPServer, path: str = "/v1/batches") -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def test_smoke_does_not_follow_redirect_with_bearer_token(
    servers: list[ThreadingHTTPServer], batch_file: Path, token_file: Path
) -> None:
    target, target_handler = _serve("Target")
    origin, origin_handler = _serve("Origin", redirect_to=_url(target, "/collect"))
    servers.extend([target, origin])

    result = CliRunner().invoke(
        app,
        [
            "receiver", "smoke", "--input", str(batch_file),
            "--token-file", str(token_file), "--url", _url(origin),
        ],
    )  # fmt: skip

    assert result.exit_code != 0
    assert origin_handler.seen_authorization == [f"Bearer {SYNTHETIC_TOKEN}"]
    assert target_handler.seen_authorization == []
    assert SYNTHETIC_TOKEN not in result.output


def test_smoke_ignores_proxy_environment(
    servers: list[ThreadingHTTPServer],
    batch_file: Path,
    token_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy, proxy_handler = _serve("Proxy")
    origin, origin_handler = _serve("Origin", body=b'{"accepted": true}')
    servers.extend([proxy, origin])
    monkeypatch.setenv("HTTP_PROXY", _url(proxy, ""))
    monkeypatch.setenv("http_proxy", _url(proxy, ""))
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    result = CliRunner().invoke(
        app,
        [
            "receiver", "smoke", "--input", str(batch_file),
            "--token-file", str(token_file), "--url", _url(origin),
        ],
    )  # fmt: skip

    assert result.exit_code == 0
    assert proxy_handler.seen_authorization == []
    assert origin_handler.seen_authorization == [f"Bearer {SYNTHETIC_TOKEN}"]


def test_smoke_unreachable_receiver_says_proxies_are_not_used(
    batch_file: Path, token_file: Path
) -> None:
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = cast("tuple[str, int]", closed.getsockname())[1]
    closed.close()

    result = CliRunner().invoke(
        app,
        [
            "receiver", "smoke", "--input", str(batch_file),
            "--token-file", str(token_file),
            "--url", f"http://127.0.0.1:{port}/v1/batches",
        ],
    )  # fmt: skip

    assert result.exit_code == 1
    assert "not reachable" in result.stderr
    assert "proxies are not used" in result.stderr


def test_smoke_non_json_200_is_a_clean_error(
    servers: list[ThreadingHTTPServer], batch_file: Path, token_file: Path
) -> None:
    origin, _ = _serve(
        "Origin", body=b"<html>captive portal</html>", content_type="text/html"
    )
    servers.append(origin)

    result = CliRunner().invoke(
        app,
        [
            "receiver", "smoke", "--input", str(batch_file),
            "--token-file", str(token_file), "--url", _url(origin),
        ],
    )  # fmt: skip

    assert result.exit_code == 1
    assert "response is not a receiver batch result" in result.output
    assert not isinstance(result.exception, Exception) or isinstance(
        result.exception, SystemExit
    )


def test_smoke_reads_token_from_stdin(
    servers: list[ThreadingHTTPServer], batch_file: Path
) -> None:
    origin, origin_handler = _serve("Origin", body=b'{"accepted": true}')
    servers.append(origin)

    result = CliRunner().invoke(
        app,
        [
            "receiver", "smoke", "--input", str(batch_file),
            "--token-file", "-", "--url", _url(origin),
        ],
        input=f"{SYNTHETIC_TOKEN}\n",
    )  # fmt: skip

    assert result.exit_code == 0
    assert origin_handler.seen_authorization == [f"Bearer {SYNTHETIC_TOKEN}"]


def test_smoke_token_option_still_works_but_warns(
    servers: list[ThreadingHTTPServer], batch_file: Path
) -> None:
    origin, origin_handler = _serve("Origin", body=b'{"accepted": true}')
    servers.append(origin)

    result = CliRunner().invoke(
        app,
        [
            "receiver", "smoke", "--input", str(batch_file),
            "--token", SYNTHETIC_TOKEN, "--url", _url(origin),
        ],
    )  # fmt: skip

    assert result.exit_code == 0
    assert "deprecated" in result.output
    assert origin_handler.seen_authorization == [f"Bearer {SYNTHETIC_TOKEN}"]


def test_smoke_requires_exactly_one_token_source(
    batch_file: Path, token_file: Path
) -> None:
    runner = CliRunner()
    neither = runner.invoke(app, ["receiver", "smoke", "--input", str(batch_file)])
    both = runner.invoke(
        app,
        [
            "receiver", "smoke", "--input", str(batch_file),
            "--token", SYNTHETIC_TOKEN, "--token-file", str(token_file),
        ],
    )  # fmt: skip

    assert neither.exit_code != 0
    assert both.exit_code != 0
