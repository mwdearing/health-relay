from __future__ import annotations

import stat
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel
from typer.testing import CliRunner, Result

from health_bridge.cli import app

if TYPE_CHECKING:
    from pathlib import Path

OWNER_ID: Final = "owner-1"
PRODUCER_ID: Final = "synthetic-app"
WRITER_BUNDLE_ID: Final = "dev.example.app"
PRODUCER_LABEL: Final = "Synthetic app"
TOKEN_LABEL: Final = "phone"


class SecretFilePayload(BaseModel):
    token: str
    token_prefix: str


class ListedToken(BaseModel):
    token_prefix: str
    revoked_at: str | None


class TokenListPayload(BaseModel):
    tokens: list[ListedToken]


class ListedProducer(BaseModel):
    producer_id: str
    writer_bundle_id: str


class ProducerListPayload(BaseModel):
    producers: list[ListedProducer]


class SetupPayload(BaseModel):
    status: str
    producer_id: str
    owner_id: str
    writer_bundle_id: str
    token_prefix: str
    secret_file: str
    rotated: bool
    revoked_token_prefix: str | None
    next_steps: list[str]


def _cli(*args: str) -> Result:
    return CliRunner().invoke(app, ["receiver", *args])


def _setup_args(db_path: Path, secret_path: Path) -> tuple[str, ...]:
    return (
        "intake-setup",
        "--db",
        str(db_path),
        "--owner-id",
        OWNER_ID,
        "--producer-id",
        PRODUCER_ID,
        "--writer-bundle-id",
        WRITER_BUNDLE_ID,
        "--label",
        PRODUCER_LABEL,
        "--output-secret",
        str(secret_path),
    )


def _run_setup(db_path: Path, secret_path: Path, *extra: str) -> Result:
    return _cli(*_setup_args(db_path, secret_path), *extra)


def _secret_token(secret_path: Path) -> str:
    document = SecretFilePayload.model_validate_json(secret_path.read_bytes())
    return document.token


def _setup(db_path: Path, secret_path: Path, *extra: str) -> SetupPayload:
    result = _run_setup(db_path, secret_path, *extra)
    assert result.exit_code == 0, (result.output, result.exception)
    return SetupPayload.model_validate_json(result.stdout)


def _active_token_prefixes(db_path: Path) -> list[str]:
    result = _cli("intake-list-tokens", "--db", str(db_path))
    assert result.exit_code == 0, (result.output, result.exception)
    listed = TokenListPayload.model_validate_json(result.stdout)
    return [token.token_prefix for token in listed.tokens if token.revoked_at is None]


def test_intake_setup_cli_writes_a_private_token_and_registers_the_producer(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    payload = _setup(db_path, secret_path)

    # Then
    assert payload.status == "registered"
    assert payload.owner_id == OWNER_ID
    assert payload.producer_id == PRODUCER_ID
    assert payload.writer_bundle_id == WRITER_BUNDLE_ID
    assert payload.secret_file == str(secret_path)
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
    assert _secret_token(secret_path).startswith("hri_")
    assert _active_token_prefixes(db_path) == [payload.token_prefix]


def test_intake_setup_cli_never_prints_the_token(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    result = _run_setup(db_path, secret_path)

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    token = _secret_token(secret_path)
    assert token not in result.output
    assert result.exception is None


def test_intake_setup_cli_refuses_to_replace_an_existing_secret_file(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    first = _setup(db_path, secret_path)
    original = secret_path.read_bytes()

    # When
    result = _run_setup(db_path, secret_path)

    # Then
    assert result.exit_code == 1
    assert "--rotate" in result.output
    assert "Traceback" not in result.output
    assert secret_path.read_bytes() == original
    assert _active_token_prefixes(db_path) == [first.token_prefix]


def test_intake_setup_cli_rotate_replaces_the_token_and_revokes_the_previous_one(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    first = _setup(db_path, secret_path)
    first_token = _secret_token(secret_path)

    # When
    rotated = _setup(db_path, secret_path, "--rotate")

    # Then
    assert rotated.rotated is True
    assert rotated.revoked_token_prefix == first.token_prefix
    assert rotated.token_prefix != first.token_prefix
    assert _secret_token(secret_path) != first_token
    assert _active_token_prefixes(db_path) == [rotated.token_prefix]
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600


def test_intake_setup_cli_registers_the_producer_once_across_runs(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    first_secret = tmp_path / "private" / "first.json"
    second_secret = tmp_path / "private" / "second.json"
    _ = _setup(db_path, first_secret)

    # When
    _ = _setup(db_path, second_secret)

    # Then
    result = _cli("intake-list-producers", "--db", str(db_path))
    assert result.exit_code == 0, (result.output, result.exception)
    listed = ProducerListPayload.model_validate_json(result.stdout)
    assert [producer.producer_id for producer in listed.producers] == [PRODUCER_ID]


def test_intake_setup_cli_refuses_a_different_writer_bundle(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    _ = _setup(db_path, secret_path)

    # When
    result = _cli(
        "intake-setup",
        "--db",
        str(db_path),
        "--owner-id",
        OWNER_ID,
        "--producer-id",
        PRODUCER_ID,
        "--writer-bundle-id",
        "dev.example.other",
        "--label",
        PRODUCER_LABEL,
        "--output-secret",
        str(tmp_path / "private" / "other.json"),
    )

    # Then
    assert result.exit_code == 1
    assert "already registered" in result.output
    assert "Traceback" not in result.output
    assert not (tmp_path / "private" / "other.json").exists()


def test_intake_setup_cli_prints_the_next_steps(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    payload = _setup(db_path, secret_path)

    # Then
    steps = "\n".join(payload.next_steps)
    assert "--enable-intake-context" in steps
    assert "intake-smoke" in steps
    assert str(secret_path) in steps
    assert str(db_path) in steps
    assert _secret_token(secret_path) not in "\n".join(payload.next_steps)


def test_intake_setup_cli_rejects_a_secret_path_over_the_database(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"

    # When
    result = _run_setup(db_path, db_path)

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_intake_setup_cli_refuses_an_invalid_producer_id(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    result = _cli(
        "intake-setup",
        "--db",
        str(db_path),
        "--owner-id",
        OWNER_ID,
        "--producer-id",
        "not a producer id",
        "--writer-bundle-id",
        WRITER_BUNDLE_ID,
        "--label",
        PRODUCER_LABEL,
        "--output-secret",
        str(secret_path),
    )

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not secret_path.exists()


def test_intake_setup_cli_rotate_on_a_missing_secret_file_issues_a_token(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    payload = _setup(db_path, secret_path, "--rotate")

    # Then
    assert payload.rotated is False
    assert payload.revoked_token_prefix is None
    assert _secret_token(secret_path).startswith("hri_")
    assert _active_token_prefixes(db_path) == [payload.token_prefix]
