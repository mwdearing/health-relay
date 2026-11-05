import json
from pathlib import Path

from typer.testing import CliRunner, Result

from health_bridge.cli import app
from health_bridge.receiver.tokens import (
    authenticate_receiver_token,
    create_receiver_token,
)
from health_bridge.storage import initialize_database

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
