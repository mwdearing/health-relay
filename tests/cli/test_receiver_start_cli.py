from __future__ import annotations

import re
import socket
from typing import TYPE_CHECKING, Final, cast

import pytest
from typer.testing import CliRunner

import health_bridge.cli_receiver as cli_receiver_module
from health_bridge.cli import app
from health_bridge.launchd import LaunchdServiceError, LaunchdServiceErrorCode
from health_bridge.mailbox.connections import MailboxConnectionStore
from health_bridge.receiver.mailbox_keys import MailboxKeyStore
from health_bridge.receiver.transports import PublicReceiverTransport, ReceiverTransport

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ANSI_SGR_PATTERN: Final = re.compile(r"\x1b\[[0-9;]*m")


def test_receiver_start_help_preserves_supported_options() -> None:
    result = CliRunner().invoke(app, ["receiver", "start", "--help"])

    plain = ANSI_SGR_PATTERN.sub("", result.stdout)
    assert result.exit_code == 0, (result.output, result.exception)
    for option in (
        "--db",
        "--host",
        "--port",
        "--mailbox-root",
        "--icloud-container-identifier",
        "--service-config",
        "--enable-intake-context",
    ):
        assert option in plain


def test_receiver_start_preserves_direct_defaults_and_skips_mailbox_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "health.sqlite"
    captured: dict[str, object] = {}

    def select_transport(
        requested: PublicReceiverTransport,
        *,
        mailbox_root: Path | None,
        icloud_container_identifier: str | None,
    ) -> ReceiverTransport:
        captured.update(
            requested=requested,
            selected_mailbox_root=mailbox_root,
            selected_container=icloud_container_identifier,
        )
        return ReceiverTransport.DIRECT

    def forbidden_store() -> None:
        pytest.fail("Direct receiver start must not construct mailbox stores.")

    def serve_receiver(  # noqa: PLR0913 -- Mirrors the receiver server API.
        db_path: Path,
        host: str,
        port: int,
        *,
        mailbox_key_store: MailboxKeyStore | None,
        mailbox_connection_store: MailboxConnectionStore | None,
        mailbox_root: Path | None,
        intake_context_enabled: bool,
        request_timeout_seconds: float,
        on_listening: Callable[[str, int], None],
    ) -> None:
        del on_listening
        captured.update(
            db_path=db_path,
            host=host,
            port=port,
            mailbox_key_store=mailbox_key_store,
            mailbox_connection_store=mailbox_connection_store,
            mailbox_root=mailbox_root,
            intake_context_enabled=intake_context_enabled,
            request_timeout_seconds=request_timeout_seconds,
        )

    monkeypatch.setattr(
        cli_receiver_module, "_select_transport_or_exit", select_transport
    )
    monkeypatch.setattr(MailboxKeyStore, "production", forbidden_store)
    monkeypatch.setattr(
        MailboxConnectionStore,
        "production",
        forbidden_store,
    )
    monkeypatch.setattr(cli_receiver_module, "serve_receiver", serve_receiver)

    result = CliRunner().invoke(app, ["receiver", "start", "--db", str(database)])

    assert result.exit_code == 0, (result.output, result.exception)
    assert captured == {
        "requested": "direct",
        "selected_mailbox_root": None,
        "selected_container": None,
        "db_path": database,
        "host": "127.0.0.1",
        "port": 8765,
        "mailbox_key_store": None,
        "mailbox_connection_store": None,
        "mailbox_root": None,
        "intake_context_enabled": False,
        "request_timeout_seconds": 30.0,
    }


def test_receiver_start_requires_resolved_database() -> None:
    result = CliRunner().invoke(app, ["receiver", "start"])

    assert result.exit_code == 2
    assert result.stderr.strip() == "Receiver start requires --db."


@pytest.mark.parametrize(
    "receiver_option",
    [
        ["--db", "/private/synthetic.sqlite"],
        ["--host", "127.0.0.2"],
        ["--port", "9876"],
        ["--mailbox-root", "/private/synthetic-mailbox"],
        ["--icloud-container-identifier", "iCloud.dev.example.Synthetic"],
    ],
)
def test_receiver_start_rejects_service_config_option_conflicts_before_loading(
    receiver_option: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_loader(_path: Path) -> None:
        pytest.fail("Conflicting receiver options must fail before config loading.")

    monkeypatch.setattr(
        cli_receiver_module,
        "load_runnable_launch_agent_request",
        forbidden_loader,
    )

    result = CliRunner().invoke(
        app,
        [
            "receiver",
            "start",
            "--service-config",
            "/private/synthetic-service.json",
            *receiver_option,
        ],
    )

    assert result.exit_code == 2
    assert result.stderr.strip() == (
        "Service configuration cannot be combined with receiver options."
    )


def test_receiver_start_redacts_owned_config_loader_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_loader(_path: Path) -> None:
        raise LaunchdServiceError(LaunchdServiceErrorCode.UNSAFE_PERMISSIONS)

    monkeypatch.setattr(
        cli_receiver_module,
        "load_runnable_launch_agent_request",
        fail_loader,
    )

    result = CliRunner().invoke(
        app,
        [
            "receiver",
            "start",
            "--service-config",
            "/private/synthetic-service.json",
        ],
    )

    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "Mailbox LaunchAgent artifact permissions are unsafe."
    )
    assert "/private/synthetic-service.json" not in result.output


def test_receiver_start_maps_keyboard_interrupt_to_clean_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "health.sqlite"

    def select_direct(
        requested: PublicReceiverTransport,
        *,
        mailbox_root: Path | None,
        icloud_container_identifier: str | None,
    ) -> ReceiverTransport:
        del requested, mailbox_root, icloud_container_identifier
        return ReceiverTransport.DIRECT

    monkeypatch.setattr(
        cli_receiver_module,
        "_select_transport_or_exit",
        select_direct,
    )

    def interrupt_receiver(*_args: object, **kwargs: object) -> None:
        on_listening = cast("Callable[[str, int], None]", kwargs["on_listening"])
        on_listening("127.0.0.1", 8765)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_receiver_module, "serve_receiver", interrupt_receiver)

    result = CliRunner().invoke(app, ["receiver", "start", "--db", str(database)])

    assert result.exit_code == 0, (result.output, result.exception)
    assert result.stderr.strip() == (
        "health-bridge receiver listening on http://127.0.0.1:8765\n"
        "Intake-context routes: disabled"
    )


def test_receiver_start_enables_intake_context_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def serve(**kwargs: object) -> None:
        captured.update(kwargs)
        cast("Callable[[str, int], None]", kwargs["on_listening"])("127.0.0.1", 8765)

    monkeypatch.setattr(cli_receiver_module, "serve_receiver", serve)
    result = CliRunner().invoke(
        app,
        [
            "receiver",
            "start",
            "--db",
            str(tmp_path / "r.sqlite"),
            "--enable-intake-context",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert captured["intake_context_enabled"] is True
    assert "Intake-context routes: enabled" in result.stderr


def test_receiver_start_leaves_intake_context_disabled_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def serve(**kwargs: object) -> None:
        captured.update(kwargs)
        cast("Callable[[str, int], None]", kwargs["on_listening"])("127.0.0.1", 8765)

    monkeypatch.setattr(cli_receiver_module, "serve_receiver", serve)
    result = CliRunner().invoke(
        app, ["receiver", "start", "--db", str(tmp_path / "r.sqlite")]
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert captured["intake_context_enabled"] is False
    assert "Intake-context routes: disabled" in result.stderr


def test_receiver_start_reports_a_taken_port_without_claiming_to_listen(
    tmp_path: Path,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        result = CliRunner().invoke(
            app,
            [
                "receiver",
                "start",
                "--db",
                str(tmp_path / "r.sqlite"),
                "--port",
                str(port),
            ],
        )

    assert result.exit_code == 1
    assert "listening" not in result.stderr
    assert f"Could not listen on 127.0.0.1:{port}" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.exc_info is None or result.exc_info[0] is SystemExit


def _ipv6_loopback_usable() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _ipv6_loopback_usable(), reason="IPv6 loopback unavailable")
def test_receiver_start_binds_ipv6_hosts_and_prints_a_valid_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    from health_bridge.receiver import server as server_module  # noqa: PLC0415

    monkeypatch.setattr(
        server_module.ReceiverHTTPServer, "serve_forever", interrupt, raising=True
    )
    result = CliRunner().invoke(
        app,
        [
            "receiver",
            "start",
            "--db",
            str(tmp_path / "r.sqlite"),
            "--host",
            "::1",
            "--port",
            "0",
        ],
    )

    assert result.exit_code == 0, (result.output, result.exception)
    assert re.search(r"listening on http://\[::1\]:\d+", result.stderr)
