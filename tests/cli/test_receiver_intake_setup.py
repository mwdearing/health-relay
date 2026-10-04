from __future__ import annotations

import shlex
import sqlite3
import stat
import threading
from pathlib import Path
from typing import Final

import pytest
import typer
from pydantic import BaseModel
from typer.testing import CliRunner, Result

from health_bridge import cli_receiver
from health_bridge.cli import app
from health_bridge.private_files import ensure_private_directory

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
    label: str
    revoked_at: str | None


class TokenListPayload(BaseModel):
    tokens: list[ListedToken]


class ListedProducer(BaseModel):
    producer_id: str
    writer_bundle_id: str
    label: str


class ProducerListPayload(BaseModel):
    producers: list[ListedProducer]


class SetupPayload(BaseModel):
    status: str
    producer_id: str
    owner_id: str
    writer_bundle_id: str
    token_label: str
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


def _active_tokens(db_path: Path) -> list[ListedToken]:
    result = _cli("intake-list-tokens", "--db", str(db_path))
    assert result.exit_code == 0, (result.output, result.exception)
    listed = TokenListPayload.model_validate_json(result.stdout)
    return [token for token in listed.tokens if token.revoked_at is None]


def _active_token_prefixes(db_path: Path) -> list[str]:
    return [token.token_prefix for token in _active_tokens(db_path)]


def _listed_producers(db_path: Path) -> list[ListedProducer]:
    result = _cli("intake-list-producers", "--db", str(db_path))
    assert result.exit_code == 0, (result.output, result.exception)
    listed = ProducerListPayload.model_validate_json(result.stdout)
    return listed.producers


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
    producers = _listed_producers(db_path)
    assert [producer.producer_id for producer in producers] == [PRODUCER_ID]


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


@pytest.mark.parametrize(
    "unusable_secret",
    [
        pytest.param("not json at all\n", id="invalid-json"),
        pytest.param('{"token": "hri_nope"}', id="missing-token-prefix"),
        pytest.param('{"token_prefix": 7}', id="token-prefix-not-a-string"),
        pytest.param('{"token_prefix": ""}', id="empty-token-prefix"),
        pytest.param('{"token_prefix": "hri_notaprefix"}', id="malformed-token-prefix"),
        pytest.param('{"token_prefix": "hri_unknown1"}', id="unknown-token-prefix"),
    ],
)
def test_intake_setup_cli_rotate_refuses_when_the_old_token_cannot_be_identified(
    tmp_path: Path,
    unusable_secret: str,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    first = _setup(db_path, secret_path)
    _ = secret_path.write_text(unusable_secret, encoding="utf-8")
    unusable = secret_path.read_bytes()

    # When
    result = _run_setup(db_path, secret_path, "--rotate")

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert secret_path.read_bytes() == unusable
    assert _active_token_prefixes(db_path) == [first.token_prefix]
    assert "intake-revoke-token" in result.output


def test_intake_setup_cli_claims_the_secret_path_before_touching_the_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    claimed_while_registering: list[bool] = []

    def _stop_at_registration(*_args: object, **_kwargs: object) -> None:
        claimed_while_registering.append(secret_path.exists())
        raise typer.Exit(code=1)

    monkeypatch.setattr(
        cli_receiver,
        "_register_intake_producer_or_exit",
        _stop_at_registration,
    )

    # When
    result = _run_setup(db_path, secret_path)

    # Then
    assert result.exit_code == 1
    assert claimed_while_registering == [True]
    # The claim is not a credential, so a failed run leaves the path usable.
    assert not secret_path.exists()


def test_intake_setup_cli_secret_path_claim_is_atomic_under_concurrency(
    tmp_path: Path,
) -> None:
    # Given: two runs that reach the destination claim at the same instant
    secret_path = tmp_path / "private" / "intake-token.json"
    ensure_private_directory(secret_path.parent)
    runners = 4
    start = threading.Barrier(runners)
    claims: list[bool] = []
    claims_lock = threading.Lock()

    def _claim() -> None:
        _ = start.wait(timeout=60)
        # The claim is the exclusive create both runs race on.
        claimed = cli_receiver._reserve_secret_output_path(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            secret_path,
        )
        with claims_lock:
            claims.append(claimed)

    # When
    threads = [threading.Thread(target=_claim) for _ in range(runners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    # Then
    assert not any(thread.is_alive() for thread in threads)
    assert claims.count(True) == 1
    assert claims.count(False) == runners - 1
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600


def _command_arguments(step: str) -> list[str]:
    return shlex.split(step[step.index("health-bridge") :])


def test_intake_setup_cli_shell_quotes_the_paths_it_prints(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "private dir" / "receiver db; echo pwned.sqlite"
    secret_path = tmp_path / "private dir" / "intake token.json"

    # When
    payload = _setup(db_path, secret_path)

    # Then
    restart = payload.next_steps[0]
    smoke = payload.next_steps[2]
    assert shlex.quote(str(db_path)) in restart
    assert str(db_path) in _command_arguments(restart)
    assert str(secret_path) in _command_arguments(smoke)
    assert shlex.quote(str(secret_path)) in smoke


def test_intake_setup_cli_prints_the_requested_receiver_url(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    payload = _setup(
        db_path,
        secret_path,
        "--url",
        "https://receiver.example:8443/intake",
        "--start-option=--service-config=/etc/receiver.json",
    )

    # Then
    steps = payload.next_steps
    smoke_arguments = _command_arguments(steps[2])
    assert "https://receiver.example:8443/intake" in smoke_arguments
    assert "--service-config=/etc/receiver.json" in _command_arguments(steps[0])
    assert "127.0.0.1" not in "\n".join(steps)


def test_intake_setup_cli_next_steps_placeholder_the_receiver_url(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    payload = _setup(db_path, secret_path)

    # Then
    steps = "\n".join(payload.next_steps)
    assert "<receiver URL>" in steps
    assert "127.0.0.1:8765" not in steps


def test_intake_setup_cli_rejects_an_empty_url(tmp_path: Path) -> None:
    # Given
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    result = _run_setup(tmp_path / "receiver.sqlite", secret_path, "--url", "   ")

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not secret_path.exists()


# --- round 2 findings -------------------------------------------------------

DOCS: Final = Path("docs/pairing.md")


def test_intake_setup_docs_limit_the_idempotency_claim_to_producer_registration() -> (
    None
):
    # Given
    setup_section = (
        DOCS.read_text(encoding="utf-8")
        .split(
            "### Guided setup",
            maxsplit=1,
        )[1]
        .split("### The individual commands", maxsplit=1)[0]
    )

    # When
    claims = [
        line.strip()
        for line in setup_section.splitlines()
        if "idempotent" in line.lower()
    ]

    # Then: the whole command is not repeatable, so only the registration step
    # may be described as idempotent.
    assert claims, "the guided setup section should still explain what repeats"
    assert all("registration" in claim.lower() for claim in claims), claims


def test_intake_setup_cli_labels_the_credential_apart_from_the_producer(
    tmp_path: Path,
) -> None:
    # Given: a producer named for the app, a credential named for the device
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
        PRODUCER_ID,
        "--writer-bundle-id",
        WRITER_BUNDLE_ID,
        "--producer-label",
        "Synthetic app",
        "--token-label",
        TOKEN_LABEL,
        "--output-secret",
        str(secret_path),
    )

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    assert _active_tokens(db_path)[0].label == TOKEN_LABEL
    listed = _listed_producers(db_path)
    assert [producer.label for producer in listed] == ["Synthetic app"]


def test_intake_setup_cli_falls_back_to_the_producer_label(
    tmp_path: Path,
) -> None:
    # Given: no --token-label, so the producer label names the credential too
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    payload = _setup(db_path, secret_path, "--producer-label", "Synthetic app")

    # Then
    assert payload.token_label == "Synthetic app"
    assert _active_tokens(db_path)[0].label == "Synthetic app"


def test_intake_setup_cli_rejects_an_empty_token_label(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    result = _run_setup(db_path, secret_path, "--token-label", "   ")

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not secret_path.exists()


@pytest.mark.parametrize(
    "unusable_url",
    [
        pytest.param("file:///tmp/receiver", id="not-http"),
        pytest.param("https://", id="no-host"),
        pytest.param("http://receiver example:8765", id="host-with-space"),
        pytest.param("http://receiver.example:99999", id="port-out-of-range"),
    ],
)
def test_intake_setup_cli_refuses_an_unusable_url_before_any_database_change(
    tmp_path: Path,
    unusable_url: str,
) -> None:
    # Given: a --url the printed smoke command could never use
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    result = _run_setup(db_path, secret_path, "--url", unusable_url)

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not db_path.exists()
    assert not secret_path.exists()


def test_intake_setup_cli_cleans_up_the_claim_when_private_mode_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a filesystem where the owner-only chmod cannot be applied
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    def _fail_chmod(_descriptor: int, _path: Path) -> None:
        msg = "chmod not permitted"
        raise PermissionError(msg)

    monkeypatch.setattr(cli_receiver, "apply_private_file_mode", _fail_chmod)

    # When
    result = _run_setup(db_path, secret_path)

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    # The zero-length claim must not survive: a retry on the same path has to work.
    assert not secret_path.exists()
    monkeypatch.undo()
    assert _setup(db_path, secret_path).status == "registered"


def test_intake_setup_cli_fails_when_a_rotation_cannot_revoke_the_old_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the store becomes unavailable after the new token is activated
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    first = _setup(db_path, secret_path)
    first_token = _secret_token(secret_path)

    def _unavailable(*_args: object, **_kwargs: object) -> int:
        msg = "database is locked"
        raise sqlite3.OperationalError(msg)

    monkeypatch.setattr(cli_receiver, "revoke_active_intake_token", _unavailable)

    # When
    result = _run_setup(db_path, secret_path, "--rotate")

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    # The credential that is still usable is named, so it can be revoked by hand.
    assert first.token_prefix in result.output
    assert "intake-revoke-token" in result.output
    # The new secret is not thrown away: it is active and valid.
    new_token = _secret_token(secret_path)
    assert new_token.startswith("hri_")
    assert new_token != first_token
    assert sorted(_active_token_prefixes(db_path)) == sorted(
        [
            first.token_prefix,
            SecretFilePayload.model_validate_json(
                secret_path.read_bytes()
            ).token_prefix,
        ],
    )


def test_intake_setup_cli_serializes_concurrent_rotations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two runs that reach the rotation lock at the same instant
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    _ = _setup(db_path, secret_path)
    lock_path = cli_receiver._rotation_lock_path(secret_path)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    # A bare claim, so a second claim cannot be told apart from a wait.
    monkeypatch.setattr(cli_receiver, "ROTATION_LOCK_TIMEOUT_SECONDS", 0)
    runners = 4
    start = threading.Barrier(runners)
    # No runner releases until every other one has tried, so a second claim
    # cannot slip in after the winner has already dropped the lock.
    attempted = threading.Barrier(runners)
    acquired: list[bool] = []
    modes: list[int] = []
    acquired_lock = threading.Lock()

    def _lock() -> None:
        _ = start.wait(timeout=60)
        # The lock file next to the secret is what a rotation must hold.
        held = cli_receiver._acquire_rotation_lock(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            secret_path,
        )
        try:
            with acquired_lock:
                acquired.append(held)
                if held:
                    # A lock another account could open or delete is not a lock.
                    modes.append(stat.S_IMODE(lock_path.stat().st_mode))
            _ = attempted.wait(timeout=60)
        finally:
            if held:
                cli_receiver._release_rotation_lock(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
                    secret_path,
                )

    # When
    threads = [threading.Thread(target=_lock) for _ in range(runners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    # Then
    assert not any(thread.is_alive() for thread in threads)
    assert acquired.count(True) == 1
    assert acquired.count(False) == runners - 1
    assert modes == [0o600]
    # The lock is not left behind to block later rotations.
    assert not lock_path.exists()


def test_intake_setup_cli_rotate_holds_a_private_lock_file_while_it_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    _ = _setup(db_path, secret_path)
    lock_path = cli_receiver._rotation_lock_path(secret_path)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    held_while_working: list[bool] = []

    def _record(_db: Path, **kwargs: object) -> dict[str, object]:
        held_while_working.append(lock_path.exists())
        return {
            "secret_file": str(kwargs["output_secret"]),
            "token_prefix": "hri_rotated0001",
        }

    monkeypatch.setattr(cli_receiver, "_write_and_activate_intake_token", _record)

    # When
    monkeypatch.setattr(cli_receiver, "ROTATION_LOCK_TIMEOUT_SECONDS", 0.2)
    result = _run_setup(db_path, secret_path, "--rotate")

    # Then
    assert result.exit_code == 0, (result.output, result.exception)
    assert held_while_working == [True]
    assert not lock_path.exists()


def test_intake_setup_cli_rotate_refuses_while_another_rotation_holds_the_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a second rotation already claimed this secret file
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"
    first = _setup(db_path, secret_path)
    original = secret_path.read_bytes()
    lock_path = cli_receiver._rotation_lock_path(secret_path)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    _ = cli_receiver._acquire_rotation_lock(secret_path)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    try:
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        # When
        monkeypatch.setattr(cli_receiver, "ROTATION_LOCK_TIMEOUT_SECONDS", 0.2)
        result = _run_setup(db_path, secret_path, "--rotate")
    finally:
        cli_receiver._release_rotation_lock(secret_path)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001

    # Then
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert secret_path.read_bytes() == original
    assert _active_token_prefixes(db_path) == [first.token_prefix]


def test_intake_setup_cli_next_steps_omit_the_database_for_a_service_config(
    tmp_path: Path,
) -> None:
    # Given: a receiver configured entirely by its service config
    db_path = tmp_path / "receiver.sqlite"
    secret_path = tmp_path / "private" / "intake-token.json"

    # When
    payload = _setup(
        db_path,
        secret_path,
        "--start-option=--service-config=.private/receiver.json",
    )

    # Then: `--db` and `--service-config` are mutually exclusive in receiver start
    restart_arguments = _command_arguments(payload.next_steps[0])
    assert "--service-config=.private/receiver.json" in restart_arguments
    assert "--db" not in restart_arguments
    assert str(db_path) not in restart_arguments
