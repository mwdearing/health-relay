import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from health_bridge.cli import app
from health_bridge.receiver import tokens
from health_bridge.receiver.tokens import (
    authenticate_receiver_token,
    create_receiver_token,
)
from health_bridge.storage import database, initialize_database

TOKEN: str = "hb_synthetic_secret_value"


def _revoke(db_path: Path, prefix: str) -> Result:
    return CliRunner().invoke(
        app,
        ["receiver", "revoke-token", "--db", str(db_path), "--token-prefix", prefix],
    )


def test_revoke_token_cli_fails_for_unknown_prefix(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    initialize_database(db_path)
    _ = create_receiver_token(db_path, label="ios-dev", token=TOKEN)

    # When
    result = _revoke(db_path, "hb_TYPO1234")

    # Then
    assert result.exit_code == 1
    assert "revoked_token_prefix" not in result.stdout
    assert "nothing was revoked" in result.output
    assert authenticate_receiver_token(db_path, TOKEN)


def test_revoke_token_cli_revokes_once_then_fails(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    initialize_database(db_path)
    issued = create_receiver_token(db_path, label="ios-dev", token=TOKEN)

    # When
    first = _revoke(db_path, f"  {issued.token_prefix} ")
    second = _revoke(db_path, issued.token_prefix)

    # Then
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout) == {"revoked_token_prefix": issued.token_prefix}
    assert not authenticate_receiver_token(db_path, TOKEN)
    assert second.exit_code == 1
    assert "revoked_token_prefix" not in second.stdout


def test_revoke_token_cli_does_not_create_missing_database(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "typo-dir" / "receiver.sqlite"

    # When
    result = _revoke(db_path, "hb_anything1")

    # Then
    assert result.exit_code == 1
    assert "revoked_token_prefix" not in result.stdout
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_revoke_token_cli_rejects_blank_prefix_before_touching_db(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    initialize_database(db_path)
    _ = create_receiver_token(db_path, label="ios-dev", token=TOKEN)

    # When
    result = _revoke(db_path, "   ")

    # Then
    assert result.exit_code == 1
    assert "must not be empty" in result.output
    assert "revoked_token_prefix" not in result.stdout
    assert authenticate_receiver_token(db_path, TOKEN)


def test_revoke_token_cli_leaves_other_prefixes_active(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    initialize_database(db_path)
    first = create_receiver_token(db_path, label="a", token="hb_aaaaaaaa_secret")
    _ = create_receiver_token(db_path, label="b", token="hb_bbbbbbbb_secret")

    # When
    result = _revoke(db_path, first.token_prefix)

    # Then
    assert result.exit_code == 0, result.output
    assert not authenticate_receiver_token(db_path, "hb_aaaaaaaa_secret")
    assert authenticate_receiver_token(db_path, "hb_bbbbbbbb_secret")


def test_revoke_token_cli_refuses_ambiguous_prefix(tmp_path: Path) -> None:
    # Given: token_prefix is indexed, not unique, so two tokens can share it.
    db_path = tmp_path / "receiver.sqlite"
    initialize_database(db_path)
    _ = create_receiver_token(db_path, label="a", token="hb_samepref_one")
    _ = create_receiver_token(db_path, label="b", token="hb_samepref_two")

    # When
    result = _revoke(db_path, "hb_samepref")

    # Then
    assert result.exit_code == 1
    assert "Several active" in result.output
    assert "revoked_token_prefix" not in result.stdout
    assert authenticate_receiver_token(db_path, "hb_samepref_one")
    assert authenticate_receiver_token(db_path, "hb_samepref_two")


def test_revoke_active_receiver_token_takes_write_lock_before_counting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the count and the UPDATE must share one write-locked transaction.
    db_path = tmp_path / "receiver.sqlite"
    initialize_database(db_path)
    _ = create_receiver_token(db_path, label="a", token="hb_lockpref_one")
    statements: list[str] = []
    real_connect = database.connect_database

    @contextmanager
    def tracing_connect(path: Path) -> Generator[sqlite3.Connection]:
        with real_connect(path) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(tokens, "connect_database", tracing_connect)

    # When
    revoked = tokens.revoke_active_receiver_token(db_path, "hb_lockpref")

    # Then
    assert revoked == 1
    begin = next(i for i, s in enumerate(statements) if s.startswith("BEGIN IMMEDIATE"))
    count = next(i for i, s in enumerate(statements) if "COUNT" in s.upper())
    update = next(
        i for i, s in enumerate(statements) if s.lstrip().upper().startswith("UPDATE")
    )
    assert begin < count < update
