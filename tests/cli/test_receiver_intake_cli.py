from __future__ import annotations

import sqlite3
import stat
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, TypeAdapter
from typer.testing import CliRunner, Result

from health_bridge.cli import app
from health_bridge.receiver.intake_tokens import authenticate_intake_token

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

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


def _cli(*args: str) -> Result:
    return CliRunner().invoke(app, ["receiver", *args])


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
    target.write_text("", encoding="utf-8")
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
