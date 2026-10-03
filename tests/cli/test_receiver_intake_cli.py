from __future__ import annotations

import json
import re
import socket
import sqlite3
import stat
import time
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, ClassVar, Final, cast

import pytest
from pydantic import BaseModel, TypeAdapter
from typer.testing import CliRunner, Result
from typing_extensions import override

from health_bridge import cli_receiver, private_files
from health_bridge.cli import app
from health_bridge.receiver.intake_tokens import (
    IntakeProducerInactiveError,
    authenticate_intake_token,
    create_intake_token_for_active_producer,
)
from health_bridge.receiver.server import build_receiver_server, server_port

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

COUNT_ROWS_ADAPTER: TypeAdapter[list[tuple[int]]] = TypeAdapter(list[tuple[int]])
TEXT_ROWS_ADAPTER: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])

OWNER_ID: Final = "owner-1"
PRODUCER_ID: Final = "nutrition-app"
WRITER_BUNDLE_ID: Final = "dev.example.nutrition"
DISPLAY_LABEL: Final = "Nutrition app"


class ProducerPayload(BaseModel):
    owner_id: str
    producer_id: str
    writer_bundle_id: str
    label: str
    registered_at: str
    revoked_at: str | None
    status: str


class ProducerListPayload(BaseModel):
    producers: list[dict[str, object]]


class IssuedTokenPayload(BaseModel):
    label: str
    owner_id: str
    producer_id: str
    token: str
    token_prefix: str
    warning: str


class TokenFilePayload(BaseModel):
    label: str
    owner_id: str
    producer_id: str
    secret_file: str
    token_prefix: str
    warning: str


class TokenEntry(BaseModel):
    token_prefix: str
    owner_id: str
    producer_id: str
    label: str
    created_at: str
    revoked_at: str | None


class TokenListPayload(BaseModel):
    tokens: list[TokenEntry]


class RevokePayload(BaseModel):
    revoked_token_prefix: str
    revoked_token_count: int


class RevokeProducerPayload(BaseModel):
    owner_id: str
    producer_id: str
    writer_bundle_id: str
    label: str
    registered_at: str
    revoked_at: str | None
    revoked_token_count: int
    status: str


class ReactivateProducerPayload(BaseModel):
    owner_id: str
    producer_id: str
    registered_at: str
    revoked_at: str | None
    status: str
    warning: str


class IntakeSmokePayload(BaseModel):
    http_status: int


CREATE_TOKEN_ARGS: Final = (
    "intake-create-token",
    "--owner-id",
    OWNER_ID,
    "--producer-id",
    PRODUCER_ID,
    "--label",
    "phone",
)
REGISTER_ARGS: Final = (
    "intake-register-producer",
    "--owner-id",
    OWNER_ID,
    "--producer-id",
    PRODUCER_ID,
    "--writer-bundle-id",
    WRITER_BUNDLE_ID,
    "--label",
    DISPLAY_LABEL,
)
PRODUCER_ARGS: Final = (
    "--owner-id",
    OWNER_ID,
    "--producer-id",
    PRODUCER_ID,
)


def _cli(*args: str) -> Result:
    return CliRunner().invoke(app, ["receiver", *args])


def _revoke_producer(db_path: Path) -> Result:
    return _cli("intake-revoke-producer", "--db", str(db_path), *PRODUCER_ARGS)


def _reactivate_producer(db_path: Path) -> Result:
    return _cli("intake-reactivate-producer", "--db", str(db_path), *PRODUCER_ARGS)


def _active_token_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as connection:
        counts = COUNT_ROWS_ADAPTER.validate_python(
            connection.execute(
                "select count(*) from intake_context_tokens where revoked_at is null",
            ).fetchall(),
        )
    return counts[0][0]


def _single_column(db_path: Path, sql: str) -> list[str]:
    """Read one text column straight from SQLite, keeping the row type typed."""
    with sqlite3.connect(db_path) as connection:
        rows = TEXT_ROWS_ADAPTER.validate_python(connection.execute(sql).fetchall())
    return [row[0] for row in rows]


def _token_rows(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as connection:
        counts = COUNT_ROWS_ADAPTER.validate_python(
            connection.execute(
                "select count(*) from intake_context_tokens",
            ).fetchall(),
        )
    return counts[0][0]


def _readable_receiver_token_rows(db_path: Path) -> int:
    """Read the batch-token count, or -1 when the file is no longer SQLite."""
    try:
        with sqlite3.connect(db_path) as connection:
            rows = COUNT_ROWS_ADAPTER.validate_python(
                connection.execute("select count(*) from receiver_tokens").fetchall(),
            )
    except sqlite3.Error:
        return -1
    return rows[0][0]


def _token_hashes(db_path: Path) -> list[str]:
    return _single_column(db_path, "select token_hash from intake_context_tokens")


def _token_prefixes(db_path: Path) -> list[str]:
    return _single_column(
        db_path,
        "select token_prefix from intake_context_tokens order by intake_token_id",
    )


def _register(db_path: Path) -> ProducerPayload:
    result = _cli(*REGISTER_ARGS, "--db", str(db_path))
    assert result.exit_code == 0, (result.output, result.exception)
    return ProducerPayload.model_validate_json(result.stdout)


def _register_producer(db_path: Path) -> None:
    """Register the shared producer without inspecting the echoed payload."""
    _ = _register(db_path)


def _issue_to_file(db_path: Path, secret_path: Path) -> Result:
    return _cli(
        *CREATE_TOKEN_ARGS,
        "--db",
        str(db_path),
        "--output-secret",
        str(secret_path),
    )


def _raise_sqlite_error(*_args: object, **_kwargs: object) -> None:
    message = "synthetic storage failure"
    raise sqlite3.OperationalError(message)


def _readable_token_rows(db_path: Path) -> int:
    """Read the token count, or -1 when the file is no longer a SQLite database."""
    try:
        return _token_rows(db_path)
    except sqlite3.Error:
        return -1


def _digest(db_path: Path) -> bytes:
    return db_path.read_bytes()


def _write_legacy_database(db_path: Path) -> None:
    """A pre-intake receiver database: migrations recorded, no intake tables."""
    legacy_schema = (
        "create table schema_migrations (migration_id text primary key, "
        "applied_at text)"
    )
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(legacy_schema)
        _ = connection.execute(
            "insert into schema_migrations values (?, ?)",
            ("012_lab_results", "2026-01-01T00:00:00Z"),
        )


def test_intake_register_producer_cli_reports_the_stored_producer(
    tmp_path: Path,
) -> None:
    # Given / When
    payload = _register(tmp_path / "receiver.sqlite")

    # Then
    assert payload.owner_id == OWNER_ID
    assert payload.producer_id == PRODUCER_ID
    assert payload.writer_bundle_id == WRITER_BUNDLE_ID
    assert payload.label == DISPLAY_LABEL
    assert payload.status == "registered"
    assert payload.registered_at
    assert payload.revoked_at is None


def test_intake_register_producer_cli_is_idempotent(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    first = _register(db_path)

    # When
    second = _register(db_path)

    # Then
    assert second.status == "already-registered"
    assert second.registered_at == first.registered_at

    result = _cli("intake-list-producers", "--db", str(db_path))
    listed = ProducerListPayload.model_validate_json(result.stdout)
    assert [producer["producer_id"] for producer in listed.producers] == [PRODUCER_ID]
    assert [producer["registered_at"] for producer in listed.producers] == [
        first.registered_at
    ]


def test_intake_register_producer_cli_refuses_different_writer_bundle(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)

    # When
    result = _cli(
        "intake-register-producer",
        "--db",
        str(db_path),
        "--owner-id",
        OWNER_ID,
        "--producer-id",
        PRODUCER_ID,
        "--writer-bundle-id",
        "dev.example.other",
        "--label",
        DISPLAY_LABEL,
    )

    # Then
    assert result.exit_code == 1
    assert WRITER_BUNDLE_ID in result.stderr
    assert result.exception is not None
    assert isinstance(result.exception, SystemExit)

    listed = _cli("intake-list-producers", "--db", str(db_path))
    unchanged = ProducerListPayload.model_validate_json(listed.stdout).producers[0]
    assert unchanged["writer_bundle_id"] == WRITER_BUNDLE_ID


def test_intake_list_producers_cli_shows_registration_without_secrets(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)

    # When
    result = _cli("intake-list-producers", "--db", str(db_path))

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    producers = ProducerListPayload.model_validate_json(result.stdout).producers
    assert len(producers) == 1
    assert producers[0]["producer_id"] == PRODUCER_ID
    assert producers[0]["writer_bundle_id"] == WRITER_BUNDLE_ID
    assert producers[0]["label"] == DISPLAY_LABEL
    assert producers[0]["revoked_at"] is None
    assert "token" not in result.stdout


def test_intake_create_token_cli_refuses_unregistered_producer(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"

    # When
    result = _cli(*CREATE_TOKEN_ARGS, "--db", str(db_path), "--print-secret")

    # Then
    assert result.exit_code == 1
    assert "not registered" in result.stderr
    assert _token_rows(db_path) == 0
    assert "hri_" not in result.stdout


def test_intake_create_token_cli_requires_exactly_one_secret_destination(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    neither = _cli(*CREATE_TOKEN_ARGS, "--db", str(db_path))
    both = _cli(
        *CREATE_TOKEN_ARGS,
        "--db",
        str(db_path),
        "--print-secret",
        "--output-secret",
        str(secret_path),
    )

    # Then
    assert neither.exit_code == 1
    assert "Refusing to print intake token" in neither.stderr
    assert both.exit_code == 1
    assert "--print-secret" in both.stderr
    assert _token_rows(db_path) == 0
    assert not secret_path.exists()


def test_intake_create_token_cli_writes_private_file_and_authenticates(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    result = _issue_to_file(db_path, secret_path)

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    assert secret_path.is_file()
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
    secret = IssuedTokenPayload.model_validate_json(secret_path.read_text("utf-8"))
    assert secret.token.startswith("hri_")
    assert secret.token not in result.stdout
    reported = TokenFilePayload.model_validate_json(result.stdout)
    assert reported.secret_file == str(secret_path)
    assert reported.token_prefix == secret.token_prefix
    principal = authenticate_intake_token(db_path, secret.token)
    assert principal is not None
    assert principal.owner_id == OWNER_ID
    assert principal.producer_id == PRODUCER_ID


def test_intake_create_token_cli_refuses_symlink_output_without_leaving_a_token(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    target = tmp_path / "elsewhere.json"
    _ = target.write_text("", encoding="utf-8")
    secret_path = tmp_path / "private" / "intake-token.json"
    secret_path.parent.mkdir(mode=0o700)
    secret_path.symlink_to(target)

    # When
    result = _issue_to_file(db_path, secret_path)

    # Then
    assert result.exit_code == 1
    assert "Failed to open private token output file" in result.stderr
    assert target.read_text(encoding="utf-8") == ""
    assert _token_rows(db_path) == 0


def test_intake_create_token_cli_print_secret_token_authenticates(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)

    # When
    result = _cli(*CREATE_TOKEN_ARGS, "--db", str(db_path), "--print-secret")

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    payload = IssuedTokenPayload.model_validate_json(result.stdout)
    assert payload.token.startswith("hri_")
    assert payload.token_prefix == payload.token[:12]
    assert "shown once" in payload.warning
    principal = authenticate_intake_token(db_path, payload.token)
    assert principal is not None
    assert principal.producer_id == PRODUCER_ID


def test_intake_list_tokens_cli_never_shows_token_or_hash(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "intake-token.json"
    created = _issue_to_file(db_path, secret_path)
    assert created.exit_code == 0, (created.output, created.exception)
    token = IssuedTokenPayload.model_validate_json(secret_path.read_text("utf-8")).token
    hashes = _token_hashes(db_path)
    prefixes = _token_prefixes(db_path)

    # When
    result = _cli("intake-list-tokens", "--db", str(db_path))

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    entries = TokenListPayload.model_validate_json(result.stdout).tokens
    assert [entry.token_prefix for entry in entries] == prefixes
    for entry in entries:
        assert entry.owner_id == OWNER_ID
        assert entry.producer_id == PRODUCER_ID
        assert entry.label == "phone"
        assert entry.created_at
        assert entry.revoked_at is None
    for stored_hash in hashes:
        assert stored_hash not in result.stdout
    assert token not in result.stdout


def test_intake_revoke_token_cli_revokes_once(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    created = _cli(*CREATE_TOKEN_ARGS, "--db", str(db_path), "--print-secret")
    issued = IssuedTokenPayload.model_validate_json(created.stdout)

    # When
    first = _cli(
        "intake-revoke-token",
        "--db",
        str(db_path),
        "--token-prefix",
        issued.token_prefix,
    )
    second = _cli(
        "intake-revoke-token",
        "--db",
        str(db_path),
        "--token-prefix",
        issued.token_prefix,
    )
    unknown = _cli(
        "intake-revoke-token",
        "--db",
        str(db_path),
        "--token-prefix",
        "hri_unknown",
    )

    # Then
    assert first.exit_code == 0, (first.output, first.exception)
    revoked = RevokePayload.model_validate_json(first.stdout)
    assert revoked.revoked_token_count == 1
    assert revoked.revoked_token_prefix == issued.token_prefix
    assert authenticate_intake_token(db_path, issued.token) is None
    assert second.exit_code == 1
    assert unknown.exit_code == 1
    listed = TokenListPayload.model_validate_json(
        _cli("intake-list-tokens", "--db", str(db_path)).stdout,
    )
    assert listed.tokens[0].revoked_at


def test_intake_create_token_cli_refuses_revoked_producer(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(
            "update intake_producers set revoked_at = '2026-01-01T00:00:00Z'"
        )

    # When
    result = _cli(*CREATE_TOKEN_ARGS, "--db", str(db_path), "--print-secret")

    # Then
    assert result.exit_code == 1
    assert "revoked" in result.stderr
    assert _token_rows(db_path) == 0


def test_intake_register_producer_cli_refuses_invalid_producer_id(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"

    # When
    result = _cli(
        "intake-register-producer",
        "--db",
        str(db_path),
        "--owner-id",
        OWNER_ID,
        "--producer-id",
        "Not A Producer",
        "--writer-bundle-id",
        WRITER_BUNDLE_ID,
        "--label",
        DISPLAY_LABEL,
    )

    # Then
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "invalid --producer-id" in result.stderr
    assert not db_path.exists()


def test_intake_register_producer_cli_reports_storage_failure_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    monkeypatch.setattr(
        "health_bridge.cli_receiver.register_intake_producer",
        _raise_sqlite_error,
    )

    # When
    result = _cli(*REGISTER_ARGS, "--db", str(db_path))

    # Then
    assert result.exit_code == 1
    assert "Intake producer storage is unavailable." in result.stderr
    assert result.exception is not None
    assert isinstance(result.exception, SystemExit)


def test_intake_register_producer_cli_creates_missing_database(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "nested" / "receiver.sqlite"

    # When
    result = _cli(*REGISTER_ARGS, "--db", str(db_path))

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    assert db_path.is_file()
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert db_path.parent.is_dir()


@pytest.mark.parametrize(
    "suffix", ["", "-wal", "-shm", "-journal", ".lifecycle.lock", ".access.lock"]
)
def test_intake_create_token_cli_never_writes_over_its_own_database(
    tmp_path: Path,
    suffix: str,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    before = _digest(db_path)

    # When
    result = _cli(
        *CREATE_TOKEN_ARGS,
        "--db",
        str(db_path),
        "--output-secret",
        f"{db_path}{suffix}",
    )

    # Then
    assert result.exit_code == 1
    assert "Refusing to write the secret over the receiver database" in result.stderr
    assert _readable_token_rows(db_path) == 0
    assert _digest(db_path) == before


def test_intake_create_token_cli_refuses_the_database_reached_through_parent_segments(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    before = _digest(db_path)

    # When
    result = _cli(
        *CREATE_TOKEN_ARGS,
        "--db",
        str(db_path),
        "--output-secret",
        str(tmp_path / "sub" / ".." / "receiver.sqlite"),
    )

    # Then
    assert result.exit_code == 1
    assert "Refusing to write the secret over the receiver database" in result.stderr
    assert _readable_token_rows(db_path) == 0
    assert _digest(db_path) == before


@pytest.mark.parametrize(
    "suffix", ["", "-wal", "-shm", "-journal", ".lifecycle.lock", ".access.lock"]
)
def test_receiver_create_token_cli_never_writes_over_its_own_database(
    tmp_path: Path,
    suffix: str,
) -> None:
    # Given the existing batch-token command writes through the same helper
    db_path = tmp_path / "receiver.sqlite"
    issued = _cli(
        "create-token",
        "--db",
        str(db_path),
        "--label",
        "ios-companion",
        "--output-secret",
        str(tmp_path / "private" / "receiver-token.json"),
    )
    assert issued.exit_code == 0, (issued.output, issued.exception)
    before = _digest(db_path)

    # When
    result = _cli(
        "create-token",
        "--db",
        str(db_path),
        "--label",
        "ios-companion",
        "--output-secret",
        f"{db_path}{suffix}",
    )

    # Then
    assert result.exit_code == 1
    assert "Refusing to write the secret over the receiver database" in result.stderr
    assert _readable_receiver_token_rows(db_path) == 1
    assert _digest(db_path) == before


@pytest.mark.parametrize(
    "command",
    ["intake-list-producers", "intake-list-tokens"],
)
def test_intake_list_commands_refuse_a_missing_database_without_creating_it(
    tmp_path: Path,
    command: str,
) -> None:
    # Given
    db_path = tmp_path / "absent" / "receiver.sqlite"

    # When
    result = _cli(command, "--db", str(db_path))

    # Then
    assert result.exit_code == 1
    assert not db_path.exists()
    assert not db_path.parent.exists()


@pytest.mark.parametrize(
    "command",
    ["intake-list-producers", "intake-list-tokens"],
)
def test_intake_list_commands_refuse_an_older_database_without_modifying_it(
    tmp_path: Path,
    command: str,
) -> None:
    # Given
    db_path = tmp_path / "legacy.sqlite"
    _write_legacy_database(db_path)
    before = _digest(db_path)

    # When
    result = _cli(command, "--db", str(db_path))

    # Then
    assert result.exit_code == 1
    assert "013" in result.stderr
    assert "014" in result.stderr
    assert _digest(db_path) == before


@pytest.mark.parametrize(
    "command",
    ["intake-list-producers", "intake-list-tokens"],
)
def test_intake_list_commands_do_not_modify_a_current_database(
    tmp_path: Path,
    command: str,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    issued = _cli(*CREATE_TOKEN_ARGS, "--db", str(db_path), "--print-secret")
    assert issued.exit_code == 0, (issued.output, issued.exception)
    before = _digest(db_path)

    # When
    result = _cli(command, "--db", str(db_path))

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    assert _digest(db_path) == before


def test_intake_register_producer_cli_ignores_an_earlier_registered_at(
    tmp_path: Path,
) -> None:
    # Given a producer registered earlier, as a restored backup would report
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(
            "update intake_producers set registered_at = ? where producer_id = ?",
            ("2020-01-01T00:00:00Z", PRODUCER_ID),
        )

    # When
    result = _cli(*REGISTER_ARGS, "--db", str(db_path))

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    reloaded = ProducerPayload.model_validate_json(result.stdout)
    assert reloaded.status == "already-registered"
    assert reloaded.registered_at == "2020-01-01T00:00:00Z"

    listed = ProducerListPayload.model_validate_json(
        _cli("intake-list-producers", "--db", str(db_path)).stdout,
    )
    assert listed.producers[0]["registered_at"] == "2020-01-01T00:00:00Z"


def test_intake_create_token_cli_refuses_a_producer_revoked_after_registration(
    tmp_path: Path,
) -> None:
    # Given a producer revoked between registration and the token request
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(
            "update intake_producers set revoked_at = ? where producer_id = ?",
            ("2026-01-01T00:00:00Z", PRODUCER_ID),
        )

    # When
    result = _cli(*CREATE_TOKEN_ARGS, "--db", str(db_path), "--print-secret")

    # Then
    assert result.exit_code == 1
    assert "revoked" in result.stderr
    assert _token_rows(db_path) == 0


def test_create_intake_token_for_active_producer_refuses_without_inserting_a_row(
    tmp_path: Path,
) -> None:
    # Given the transactional insert helper called directly on a revoked producer
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(
            "update intake_producers set revoked_at = ? where producer_id = ?",
            ("2026-01-01T00:00:00Z", PRODUCER_ID),
        )

    # When
    with pytest.raises(IntakeProducerInactiveError):
        _ = create_intake_token_for_active_producer(
            db_path,
            owner_id=OWNER_ID,
            producer_id=PRODUCER_ID,
            label="phone",
        )

    # Then
    assert _token_rows(db_path) == 0


def test_create_intake_token_for_active_producer_issues_a_working_token(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)

    # When
    issued = create_intake_token_for_active_producer(
        db_path,
        owner_id=OWNER_ID,
        producer_id=PRODUCER_ID,
        label="phone",
    )

    # Then
    assert issued.token.startswith("hri_")
    assert authenticate_intake_token(db_path, issued.token) is not None


def test_intake_revoke_producer_cli_revokes_the_producer_and_its_tokens(
    tmp_path: Path,
) -> None:
    # Given a producer with two live tokens
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    tokens: list[IssuedTokenPayload] = []
    for index in range(2):
        secret_path = tmp_path / "private" / f"token-{index}.json"
        created = _issue_to_file(db_path, secret_path)
        assert created.exit_code == 0, (created.output, created.exception)
        tokens.append(
            IssuedTokenPayload.model_validate_json(secret_path.read_text("utf-8"))
        )
    first_token, second_token = tokens
    assert authenticate_intake_token(db_path, first_token.token) is not None
    assert authenticate_intake_token(db_path, second_token.token) is not None

    # When
    result = _revoke_producer(db_path)

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    payload = RevokeProducerPayload.model_validate_json(result.stdout)
    assert payload.status == "revoked"
    assert payload.producer_id == PRODUCER_ID
    assert payload.revoked_token_count == 2
    assert payload.revoked_at
    for issued in tokens:
        assert authenticate_intake_token(db_path, issued.token) is None
        assert issued.token not in result.stdout
        assert issued.token_prefix not in result.stdout


def test_intake_revoke_producer_cli_refuses_an_unknown_producer(tmp_path: Path) -> None:
    # Given a registered producer and a request for a different one
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    before = _digest(db_path)

    # When
    result = _cli(
        "intake-revoke-producer",
        "--db",
        str(db_path),
        "--owner-id",
        OWNER_ID,
        "--producer-id",
        "other-app",
    )

    # Then
    assert result.exit_code == 1
    assert "nothing was revoked" in result.stderr
    assert _digest(db_path) == before


def test_intake_revoke_producer_cli_refuses_a_second_revocation(tmp_path: Path) -> None:
    # Given an already revoked producer
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    first = _revoke_producer(db_path)
    assert first.exit_code == 0, (first.output, first.exception)

    # When
    second = _revoke_producer(db_path)

    # Then
    assert second.exit_code == 1
    assert "already revoked" in second.stderr


def test_intake_register_producer_cli_refuses_an_identical_revoked_producer(
    tmp_path: Path,
) -> None:
    # Given a revoked producer
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    revoked = _revoke_producer(db_path)
    assert revoked.exit_code == 0, (revoked.output, revoked.exception)

    # When
    result = _cli(*REGISTER_ARGS, "--db", str(db_path))

    # Then
    assert result.exit_code == 1
    assert "intake-reactivate-producer" in result.stderr
    listed = ProducerListPayload.model_validate_json(
        _cli("intake-list-producers", "--db", str(db_path)).stdout,
    )
    assert listed.producers[0]["revoked_at"] is not None


def test_intake_reactivate_producer_cli_clears_the_revocation(
    tmp_path: Path,
) -> None:
    # Given a revoked producer
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    revoked = _revoke_producer(db_path)
    assert revoked.exit_code == 0, (revoked.output, revoked.exception)

    # When
    result = _reactivate_producer(db_path)

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    payload = ReactivateProducerPayload.model_validate_json(result.stdout)
    assert payload.status == "reactivated"
    assert payload.revoked_at is None
    assert "new" in payload.warning
    listed = ProducerListPayload.model_validate_json(
        _cli("intake-list-producers", "--db", str(db_path)).stdout,
    )
    assert listed.producers[0]["revoked_at"] is None


def test_intake_reactivate_producer_cli_keeps_old_tokens_revoked(
    tmp_path: Path,
) -> None:
    # Given a token issued before the producer was revoked
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "token.json"
    created = _issue_to_file(db_path, secret_path)
    assert created.exit_code == 0, (created.output, created.exception)
    issued = IssuedTokenPayload.model_validate_json(secret_path.read_text("utf-8"))
    revoked = _revoke_producer(db_path)
    assert revoked.exit_code == 0, (revoked.output, revoked.exception)

    # When
    result = _reactivate_producer(db_path)

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    assert authenticate_intake_token(db_path, issued.token) is None
    listed = TokenListPayload.model_validate_json(
        _cli("intake-list-tokens", "--db", str(db_path)).stdout,
    )
    assert all(entry.revoked_at for entry in listed.tokens)
    fresh = _issue_to_file(db_path, tmp_path / "private" / "fresh.json")
    assert fresh.exit_code == 0, (fresh.output, fresh.exception)


def test_intake_reactivate_producer_cli_refuses_an_unknown_producer(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    before = _digest(db_path)

    # When
    result = _reactivate_producer(db_path)

    # Then
    assert result.exit_code == 1
    assert "already active" in result.stderr
    assert _digest(db_path) == before


def test_intake_create_token_cli_leaves_no_active_token_when_the_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a producer whose secret destination cannot be written
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    before = _active_token_count(db_path)

    def refuse_write(*_args: object, **_kwargs: object) -> None:
        raise OSError

    monkeypatch.setattr(
        "health_bridge.cli_receiver.write_private_text_file",
        refuse_write,
    )

    # When
    result = _issue_to_file(db_path, tmp_path / "private" / "token.json")

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert _active_token_count(db_path) == before


def test_intake_create_token_cli_discards_a_pending_token_row_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)

    def refuse_write(*_args: object, **_kwargs: object) -> None:
        raise OSError

    monkeypatch.setattr(
        "health_bridge.cli_receiver.write_private_text_file",
        refuse_write,
    )

    # When
    result = _issue_to_file(db_path, tmp_path / "private" / "token.json")

    # Then the unusable row is gone rather than left behind as debris
    assert result.exit_code == 1
    assert _token_rows(db_path) == 0


def test_intake_create_token_cli_activates_the_token_only_after_the_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a token file whose write is observed as it happens
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "token.json"
    observed: list[int] = []
    real_write = private_files.write_private_text_file

    def observing_write(path: Path, text: str) -> None:
        real_write(path, text)
        observed.append(_active_token_count(db_path))

    monkeypatch.setattr(cli_receiver, "write_private_text_file", observing_write)

    # When
    result = _issue_to_file(db_path, secret_path)

    # Then nothing was usable while the secret was being written
    assert result.exit_code == 0, (result.output, result.exception)
    assert observed == [0]
    issued = IssuedTokenPayload.model_validate_json(secret_path.read_text("utf-8"))
    assert authenticate_intake_token(db_path, issued.token) is not None


def test_receiver_start_rejects_an_out_of_range_request_timeout(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"

    # When
    for value in ("0", "-1", "301", "abc"):
        result = _cli(
            "start",
            "--db",
            str(db_path),
            "--request-timeout",
            value,
        )

        # Then
        assert result.exit_code != 0, value


def test_receiver_start_rejects_an_out_of_range_request_timeout_with_a_message(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"

    # When
    result = _cli("start", "--db", str(db_path), "--request-timeout", "0")

    # Then
    assert result.exit_code == 2
    assert "--request-timeout" in result.stderr
    assert "300" in result.stderr


@contextmanager
def _served_receiver(db_path: Path, *, intake_enabled: bool) -> Generator[str]:
    server = build_receiver_server(
        db_path,
        "127.0.0.1",
        0,
        intake_context_enabled=intake_enabled,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server_port(server)}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_intake_smoke_cli_reports_capabilities_for_a_live_token(
    tmp_path: Path,
) -> None:
    # Given a receiver serving the intake routes and a token it accepts
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "token.json"
    created = _issue_to_file(db_path, secret_path)
    assert created.exit_code == 0, (created.output, created.exception)
    issued = IssuedTokenPayload.model_validate_json(secret_path.read_text("utf-8"))

    with _served_receiver(db_path, intake_enabled=True) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    payload = IntakeSmokePayload.model_validate_json(result.stdout)
    assert payload.http_status == 200
    assert issued.token not in result.output
    assert issued.token_prefix not in result.output


def test_intake_smoke_cli_fails_for_a_revoked_token(tmp_path: Path) -> None:
    # Given a token whose producer was revoked
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "token.json"
    created = _issue_to_file(db_path, secret_path)
    assert created.exit_code == 0, (created.output, created.exception)
    revoked = _revoke_producer(db_path)
    assert revoked.exit_code == 0, (revoked.output, revoked.exception)

    with _served_receiver(db_path, intake_enabled=True) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then
    assert result.exit_code == 1
    assert "401" in result.stderr


def test_intake_smoke_cli_says_the_routes_are_not_enabled(tmp_path: Path) -> None:
    # Given a receiver started without --enable-intake-context
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "token.json"
    created = _issue_to_file(db_path, secret_path)
    assert created.exit_code == 0, (created.output, created.exception)

    with _served_receiver(db_path, intake_enabled=False) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then
    assert result.exit_code == 1
    assert "not enabled" in result.stderr
    assert "--enable-intake-context" in result.stderr


def test_intake_smoke_cli_refuses_a_non_http_url(tmp_path: Path) -> None:
    # Given
    secret_path = tmp_path / "private" / "token.json"
    secret_path.parent.mkdir(mode=0o700, exist_ok=True)
    _ = secret_path.write_text(json.dumps({"token": "hri_synthetic"}), encoding="utf-8")

    # When
    result = _cli(
        "intake-smoke",
        "--url",
        "file:///etc/passwd",
        "--token-file",
        str(secret_path),
    )

    # Then
    assert result.exit_code == 1
    assert "http or https" in result.stderr


def test_intake_smoke_cli_reports_an_unreadable_token_file(tmp_path: Path) -> None:
    # Given a token file that holds no intake token
    secret_path = tmp_path / "private" / "token.json"
    secret_path.parent.mkdir(mode=0o700, exist_ok=True)
    _ = secret_path.write_text(json.dumps({"label": "phone"}), encoding="utf-8")

    # When
    result = _cli(
        "intake-smoke",
        "--url",
        "http://127.0.0.1:8765",
        "--token-file",
        str(secret_path),
    )

    # Then
    assert result.exit_code == 1
    assert "--token-file" in result.stderr
    assert result.stdout == ""


def test_intake_smoke_cli_reports_a_token_file_that_is_not_utf8(tmp_path: Path) -> None:
    # Given a token file whose bytes are not valid UTF-8
    secret_path = tmp_path / "private" / "token.json"
    secret_path.parent.mkdir(mode=0o700, exist_ok=True)
    _ = secret_path.write_bytes(b"\xff\xfe\x00hri_synthetic")

    # When
    result = _cli(
        "intake-smoke",
        "--url",
        "http://127.0.0.1:8765",
        "--token-file",
        str(secret_path),
    )

    # Then the documented message replaces an unhandled decode error
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "--token-file" in result.stderr
    assert result.stdout == ""


class _QuietHandler(BaseHTTPRequestHandler):
    """A test HTTP handler that keeps the test output free of request logs."""

    @override
    def log_message(self, format: str, *args: object) -> None:
        _ = (format, args)


class _RedirectHandler(_QuietHandler):
    """Answers every request with a redirect to another host."""

    target: str = ""

    def do_GET(self) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", self.target)
        self.send_header("Content-Length", "0")
        self.end_headers()


class _RecordingHandler(_QuietHandler):
    """Records the Authorization header of every request it answers."""

    seen: ClassVar[list[str | None]] = []

    def do_GET(self) -> None:
        self.seen.append(self.headers.get("Authorization"))
        body = b"{}"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)


@contextmanager
def _serve(handler: type[BaseHTTPRequestHandler]) -> Generator[str]:
    """Serve one handler on a loopback port for the life of the context."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _redirecting_receiver(target: str) -> Generator[str]:
    """A receiver that answers the capabilities route with a redirect elsewhere."""
    with _serve(type("BoundRedirect", (_RedirectHandler,), {"target": target})) as base:
        yield base


@contextmanager
def _recording_receiver() -> Generator[tuple[str, list[str | None]]]:
    """A server that records the Authorization header of every request it sees."""
    seen: list[str | None] = []
    handler = type("BoundRecording", (_RecordingHandler,), {"seen": seen})
    with _serve(handler) as base:
        yield base, seen


def test_intake_smoke_cli_refuses_a_redirect_instead_of_forwarding_the_token(
    tmp_path: Path,
) -> None:
    # Given a receiver that redirects to another host
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "token.json"
    created = _issue_to_file(db_path, secret_path)
    assert created.exit_code == 0, (created.output, created.exception)
    issued = IssuedTokenPayload.model_validate_json(secret_path.read_text("utf-8"))

    with _recording_receiver() as (target, seen), _redirecting_receiver(target) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then the bearer token never reaches the redirect target
    assert result.exit_code == 1
    assert seen == []
    assert issued.token not in result.output


def test_intake_create_token_cli_keeps_an_existing_secret_file_when_activation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an existing secret file at the requested destination
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    first_path = tmp_path / "private" / "token.json"
    assert _issue_to_file(db_path, first_path).exit_code == 0
    original = first_path.read_text("utf-8")

    def fail_activation(*_args: object, **_kwargs: object) -> int:
        message = "synthetic activation failure"
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr(cli_receiver, "activate_intake_token", fail_activation)

    # When a second issuance to the same path cannot be activated
    result = _issue_to_file(db_path, first_path)

    # Then the previous secret is still on disk
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert first_path.read_text("utf-8") == original
    assert _active_token_count(db_path) == 1


def test_intake_create_token_cli_restores_the_previous_file_when_the_write_fails_late(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a working secret file that a late write failure would overwrite
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    first_path = tmp_path / "private" / "token.json"
    assert _issue_to_file(db_path, first_path).exit_code == 0
    original = first_path.read_text("utf-8")
    real_write = private_files.write_private_text_file

    def failing_after_replace(path: Path, text: str) -> None:
        # The atomic replace succeeds and a later step (chmod, fsync) fails.
        real_write(path, text)
        message = "synthetic post-replace failure"
        raise OSError(message)

    monkeypatch.setattr(cli_receiver, "write_private_text_file", failing_after_replace)

    # When a second issuance to the same path fails after replacing it
    result = _issue_to_file(db_path, first_path)

    # Then the earlier credential is put back rather than overwritten
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert first_path.read_text("utf-8") == original
    assert _active_token_count(db_path) == 1


def test_intake_create_token_cli_removes_a_late_failed_write_to_a_new_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a destination that does not exist yet and a write failing after replace
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    real_write = private_files.write_private_text_file

    def failing_after_replace(path: Path, text: str) -> None:
        real_write(path, text)
        message = "synthetic post-replace failure"
        raise OSError(message)

    monkeypatch.setattr(cli_receiver, "write_private_text_file", failing_after_replace)

    # When
    secret_path = tmp_path / "private" / "token.json"
    result = _issue_to_file(db_path, secret_path)

    # Then no unusable credential file is left behind
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not secret_path.exists()
    assert _active_token_count(db_path) == 0
    assert _token_rows(db_path) == 0


def test_intake_create_token_cli_removes_its_own_new_file_when_activation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a destination that does not exist yet
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)

    def fail_activation(*_args: object, **_kwargs: object) -> int:
        message = "synthetic activation failure"
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr(cli_receiver, "activate_intake_token", fail_activation)

    # When
    secret_path = tmp_path / "private" / "token.json"
    result = _issue_to_file(db_path, secret_path)

    # Then no unusable credential file is left behind
    assert result.exit_code == 1
    assert not secret_path.exists()
    assert _active_token_count(db_path) == 0


def test_intake_create_token_cli_refuses_to_activate_a_token_after_producer_revoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a producer revoked between the pending insert and the activation
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    secret_path = tmp_path / "private" / "token.json"
    real_write = private_files.write_private_text_file

    def revoking_write(path: Path, text: str) -> None:
        real_write(path, text)
        assert _revoke_producer(db_path).exit_code == 0

    monkeypatch.setattr(cli_receiver, "write_private_text_file", revoking_write)

    # When
    result = _issue_to_file(db_path, secret_path)

    # Then no active credential survives the revocation
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert _active_token_count(db_path) == 0
    assert not secret_path.exists()


def test_intake_create_token_cli_keeps_a_secret_when_the_producer_is_revoked_midway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an earlier secret file that must survive a later failed issuance
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    first_path = tmp_path / "private" / "token.json"
    assert _issue_to_file(db_path, first_path).exit_code == 0
    original = first_path.read_text("utf-8")
    real_write = private_files.write_private_text_file

    def revoking_write(path: Path, text: str) -> None:
        real_write(path, text)
        assert _revoke_producer(db_path).exit_code == 0

    monkeypatch.setattr(cli_receiver, "write_private_text_file", revoking_write)

    # When
    result = _issue_to_file(db_path, first_path)

    # Then the earlier credential file is left exactly as it was
    assert result.exit_code == 1
    assert first_path.read_text("utf-8") == original
    # The producer revocation revokes the earlier token too, and the failed
    # issuance adds nothing on top of that.
    assert _active_token_count(db_path) == 0


def test_intake_create_token_cli_refuses_a_secret_file_whose_producer_was_revoked(
    tmp_path: Path,
) -> None:
    # Given a producer with no live tokens and an existing destination
    db_path = tmp_path / "receiver.sqlite"
    _register_producer(db_path)
    assert _revoke_producer(db_path).exit_code == 0
    secret_path = tmp_path / "private" / "token.json"
    secret_path.parent.mkdir(mode=0o700, exist_ok=True)
    _ = secret_path.write_text("{}", encoding="utf-8")

    # When
    result = _issue_to_file(db_path, secret_path)

    # Then nothing is issued and the destination is untouched
    assert result.exit_code == 1
    assert secret_path.read_text("utf-8") == "{}"
    assert _token_rows(db_path) == 0


class _CapabilitiesBody(_QuietHandler):
    """Answers the capabilities route with a fixed status and body."""

    status: int = HTTPStatus.OK
    body: bytes = b"{}"

    def do_GET(self) -> None:
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        _ = self.wfile.write(self.body)


@contextmanager
def _canned_receiver(status: int, body: bytes) -> Generator[str]:
    handler = type(
        "CannedHandler",
        (_CapabilitiesBody,),
        {"status": status, "body": body},
    )
    with _serve(handler) as base:
        yield base


VALID_CAPABILITIES: Final = json.dumps(
    {
        "schema": "healthrelay.intake-context",
        "supported_versions": ["1.0"],
        "max_body_bytes": 1048576,
        "max_operations": 500,
        "authentication": {
            "scheme": "bearer",
            "header": "Authorization",
            "token_type": "intake",
        },
        "features": ["upsert", "delete", "link_projection"],
    },
).encode()


# The shape intake-create-token writes: the `hri_` prefix plus 32 bytes of
# URL-safe base64. Anything else is not a token this CLI will send.
ANSI_PATTERN: Final = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
BOX_PATTERN: Final = re.compile(r"[\u2500-\u257f\u2580-\u259f]")

SYNTHETIC_TOKEN: Final = "hri_" + "Ab3-_x" * 7 + "Z"


def _smoke_token_file(tmp_path: Path) -> Path:
    secret_path = tmp_path / "private" / "token.json"
    secret_path.parent.mkdir(mode=0o700, exist_ok=True)
    _ = secret_path.write_text(json.dumps({"token": SYNTHETIC_TOKEN}), encoding="utf-8")
    return secret_path


def test_intake_smoke_cli_rejects_a_capabilities_body_that_is_not_json(
    tmp_path: Path,
) -> None:
    # Given a 200 response from something that is not a health-bridge receiver
    secret_path = _smoke_token_file(tmp_path)

    with _canned_receiver(HTTPStatus.OK, b"<html>not a receiver</html>") as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then the false-positive success is refused
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_intake_smoke_cli_rejects_capabilities_without_a_known_schema(
    tmp_path: Path,
) -> None:
    # Given a 200 response that is JSON but not an intake capabilities document
    secret_path = _smoke_token_file(tmp_path)
    body = json.dumps({"status": "ok", "server": "nginx"}).encode()

    with _canned_receiver(HTTPStatus.OK, body) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_intake_smoke_cli_rejects_capabilities_without_a_supported_version(
    tmp_path: Path,
) -> None:
    # Given a 200 response advertising no version this CLI understands
    secret_path = _smoke_token_file(tmp_path)
    body = json.dumps(
        {
            "schema": "healthrelay.intake-context",
            "supported_versions": ["9.9"],
            "authentication": {"scheme": "bearer"},
            "features": [],
        }
    ).encode()

    with _canned_receiver(HTTPStatus.OK, body) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_intake_smoke_cli_rejects_a_capabilities_document_with_null_values(
    tmp_path: Path,
) -> None:
    # Given a 200 response whose capability fields are all present but null
    secret_path = _smoke_token_file(tmp_path)
    body = json.dumps(
        {
            "schema": "healthrelay.intake-context",
            "supported_versions": ["1.0"],
            "authentication": None,
            "features": None,
            "max_body_bytes": None,
            "max_operations": None,
        }
    ).encode()

    with _canned_receiver(HTTPStatus.OK, body) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then presence alone is not enough to call the receiver usable
    assert result.exit_code == 1
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authentication", "bearer"),
        ("authentication", {"scheme": "bearer"}),
        ("features", "upsert"),
        ("features", [1, 2]),
        ("max_body_bytes", "1048576"),
        ("max_body_bytes", 0),
        ("max_body_bytes", -1),
        ("max_operations", 0),
        ("max_operations", "500"),
    ],
)
def test_intake_smoke_cli_rejects_malformed_capability_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    # Given a 200 response with one capability field of the wrong shape
    secret_path = _smoke_token_file(tmp_path)
    document = cast("dict[str, object]", json.loads(VALID_CAPABILITIES))
    document[field] = value
    body = json.dumps(document).encode()

    with _canned_receiver(HTTPStatus.OK, body) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then an uploader that cannot use the value is reported as unusable
    assert result.exit_code == 1, field
    assert field in result.stderr


def test_intake_smoke_cli_rejects_an_unbounded_capabilities_body(
    tmp_path: Path,
) -> None:
    # Given a receiver whose capabilities response is far larger than any document
    secret_path = _smoke_token_file(tmp_path)
    padding = "x" * (cli_receiver.INTAKE_SMOKE_MAX_BODY_BYTES + 1024)
    body = json.dumps({**json.loads(VALID_CAPABILITIES), "padding": padding}).encode()

    with _canned_receiver(HTTPStatus.OK, body) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then the oversized response is refused instead of buffered whole
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "too large" in result.stderr


TRUNCATED_HEAD: Final = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: 4096\r\n"
    b"\r\n{"
)


@contextmanager
def _truncating_receiver(head: bytes) -> Generator[str]:
    """A server that answers with ``head`` and then closes the connection."""

    def serve(listener: socket.socket) -> None:
        with listener:
            try:
                connection = listener.accept()[0]
            except OSError:
                return
            with connection:
                try:
                    _ = connection.recv(4096)
                    _ = connection.sendall(head)
                except OSError:
                    return

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        thread = Thread(target=serve, args=(listener,), daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            listener.close()
            thread.join(timeout=5)


def test_intake_smoke_cli_reports_a_truncated_capabilities_body(
    tmp_path: Path,
) -> None:
    # Given a receiver that hangs up before sending the body it advertised
    secret_path = _smoke_token_file(tmp_path)

    with _truncating_receiver(TRUNCATED_HEAD) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then the partial body is refused without a traceback
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "not a JSON object" in result.stderr


def test_intake_smoke_cli_reports_a_connection_closed_before_a_response(
    tmp_path: Path,
) -> None:
    # Given a peer that answers with a status line urllib cannot parse
    secret_path = _smoke_token_file(tmp_path)

    with _truncating_receiver(b"not a status line at all\r\n\r\n") as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then the HTTP error is the normal unreachable diagnostic, not a traceback
    # A BadStatusLine is an HTTPException rather than an OSError, so it used to
    # escape this handler as a traceback.
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "not reachable" in result.stderr


@pytest.mark.parametrize(
    "url",
    [
        "http://[::1",
        "http://exa mple.com",
        "http://127.0.0.1:notaport",
        "http://",
        "https://exam\u00f8ple.com",
        "http://127.0.0.1:99999",
    ],
)
def test_intake_smoke_cli_refuses_a_malformed_url(tmp_path: Path, url: str) -> None:
    # Given a URL that cannot be parsed into a usable HTTP authority
    secret_path = _smoke_token_file(tmp_path)

    # When
    result = _cli("intake-smoke", "--url", url, "--token-file", str(secret_path))

    # Then the documented URL error replaces an escaping parse or request error
    assert result.exit_code == 1, url
    assert "Traceback" not in result.output
    assert "http or https" in result.stderr


@pytest.mark.parametrize(
    "token",
    [
        "hri_abc\ndef",
        "hri_abc\rX-Injected: 1",
        "hri_caf\u00e9",
        "hri_ok\n",
    ],
)
def test_intake_smoke_cli_refuses_a_token_that_is_not_header_safe(
    tmp_path: Path,
    token: str,
) -> None:
    # Given a token file whose token cannot go in an Authorization header
    secret_path = tmp_path / "private" / "token.json"
    secret_path.parent.mkdir(mode=0o700, exist_ok=True)
    _ = secret_path.write_text(json.dumps({"token": token}), encoding="utf-8")

    # When
    result = _cli(
        "intake-smoke",
        "--url",
        "http://127.0.0.1:8765",
        "--token-file",
        str(secret_path),
    )

    # Then the token-file diagnostic replaces an unhandled header error
    assert result.exit_code == 1, repr(token)
    assert "Traceback" not in result.output
    assert "--token-file" in result.stderr


def test_intake_smoke_cli_accepts_a_complete_capabilities_document(
    tmp_path: Path,
) -> None:
    # Given a 200 response with the expected schema and capability fields
    secret_path = _smoke_token_file(tmp_path)

    with _canned_receiver(HTTPStatus.OK, VALID_CAPABILITIES) as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    payload = IntakeSmokePayload.model_validate_json(result.stdout)
    assert payload.http_status == 200


STALLED_RESPONSE_HEAD: Final = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: 4096\r\n"
    b"\r\n{"
)
STALL_HOLD_SECONDS: Final = 2.0
STALL_READ_TIMEOUT_SECONDS: Final = 0.5


@contextmanager
def _stalling_receiver() -> Generator[str]:
    """A server that sends response headers and then stops delivering the body."""

    def serve(listener: socket.socket) -> None:
        with listener:
            try:
                connection = listener.accept()[0]
            except OSError:
                return
            with connection:
                try:
                    _ = connection.recv(4096)
                    _ = connection.sendall(STALLED_RESPONSE_HEAD)
                    # Hold the body open just past the client's read timeout.
                    time.sleep(STALL_HOLD_SECONDS)
                except OSError:
                    return

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        thread = Thread(target=serve, args=(listener,), daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            listener.close()
            thread.join(timeout=5)


def test_intake_smoke_cli_reports_a_stalled_capabilities_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a receiver that stops mid-body
    secret_path = _smoke_token_file(tmp_path)
    monkeypatch.setattr(
        cli_receiver,
        "INTAKE_SMOKE_TIMEOUT_SECONDS",
        STALL_READ_TIMEOUT_SECONDS,
    )

    with _stalling_receiver() as base:
        # When
        result = _cli("intake-smoke", "--url", base, "--token-file", str(secret_path))

    # Then the stall is the documented unreachable diagnostic
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "not reachable" in result.stderr


def test_receiver_start_help_describes_the_request_timeout_as_inactivity() -> None:
    # Given a help render with a fixed, wide, colourless terminal, so the
    # assertion does not depend on the CI terminal width or ANSI styling
    # rewrapping or splitting the option name.
    # When
    result = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}).invoke(
        app, ["receiver", "start", "--help"]
    )
    rendered = _strip_terminal_styling(result.output)

    # Then the help text does not promise a total request deadline
    assert result.exit_code == 0, (result.output, result.exception)
    assert "--request-timeout" in rendered
    assert "inactivity" in rendered.lower()


def _strip_terminal_styling(text: str) -> str:
    """Drop ANSI colour codes and the box drawing rich adds around help."""
    without_colour = ANSI_PATTERN.sub("", text)
    return BOX_PATTERN.sub(" ", without_colour)
