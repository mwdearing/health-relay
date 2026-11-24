from __future__ import annotations

import json
import os
import sqlite3
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from health_bridge.cli import app
from health_bridge.mailbox.connections import (
    MailboxConnectionError,
    MailboxConnectionErrorCode,
)
from health_bridge.mailbox.filesystem import MailboxFileError, MailboxFileErrorCode
from health_bridge.mailbox.importer import MailboxBusyError
from health_bridge.mailbox.native_ack_publication import NativeAckPublicationError
from tests.mailbox.importer_support import (
    BATCH,
    IMPORTED_COUNTS,
    configured_importer,
    environment,
    invoke_import_cli,
    write_delivery,
)

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import Result

    from health_bridge.mailbox.importer import MailboxImporter


def test_cli_json_is_aggregate_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = environment(tmp_path)
    _ = write_delivery(value)
    result = invoke_import_cli(value, configured_importer(value), monkeypatch)

    assert result.exit_code == 0
    assert json.loads(result.stdout) == IMPORTED_COUNTS
    assert str(value.mailbox_path) not in result.stdout
    assert BATCH.decode() not in result.stdout


def _run_import(db: Path, mailbox: Path, *extra: str) -> Result:
    return CliRunner().invoke(
        app,
        [
            "mailbox",
            "import",
            "--db",
            str(db),
            "--mailbox",
            str(mailbox),
            "--once",
            *extra,
        ],
    )


def test_import_refuses_missing_database_and_creates_nothing(tmp_path: Path) -> None:
    mailbox = tmp_path / ("02" * 16) / ("03" * 16)
    mailbox.mkdir(parents=True)
    db = tmp_path / "typo" / "health.sqlite"

    result = _run_import(db, mailbox)

    assert result.exit_code == 3
    assert "database_unavailable" in result.output
    assert "retryable=1" not in result.output
    assert not db.parent.exists()


def test_import_refuses_uninitialized_database_and_leaves_it_untouched(
    tmp_path: Path,
) -> None:
    db = tmp_path / "empty.sqlite"
    _ = db.write_bytes(b"")
    mailbox = tmp_path / ("02" * 16) / ("03" * 16)
    mailbox.mkdir(parents=True)

    result = _run_import(db, mailbox)

    assert result.exit_code == 3
    assert "database_uninitialized" in result.output
    assert db.read_bytes() == b""
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "02" * 16,
        "empty.sqlite",
    ]


def test_import_locked_database_probe_is_retryable_not_uninitialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = environment(tmp_path)

    error = sqlite3.OperationalError("database is locked")

    def locked(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise error

    monkeypatch.setattr("health_bridge.cli_mailbox.sqlite3.connect", locked)
    result = _run_import(value.db_path, value.mailbox_path, "--json")

    assert result.exit_code == 2
    assert json.loads(result.stdout)["retryable"] == 1
    assert "database_uninitialized" not in result.output


def test_import_unopenable_database_is_unreadable_not_uninitialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = environment(tmp_path)

    error = sqlite3.OperationalError("unable to open database file")

    def unopenable(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise error

    monkeypatch.setattr("health_bridge.cli_mailbox.sqlite3.connect", unopenable)
    result = _run_import(value.db_path, value.mailbox_path, "--json")

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "error_code": "database_unreadable",
        "status": "error",
    }


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_import_permission_denied_database_is_unreadable(tmp_path: Path) -> None:
    db = tmp_path / "locked.sqlite"
    _ = db.write_bytes(b"")
    db.chmod(0o000)
    mailbox = tmp_path / ("02" * 16) / ("03" * 16)
    mailbox.mkdir(parents=True)
    try:
        result = _run_import(db, mailbox, "--json")
    finally:
        db.chmod(0o600)

    assert result.exit_code == 3
    assert json.loads(result.stdout)["error_code"] == "database_unreadable"


def test_import_reports_malformed_mailbox_path_as_permanent(tmp_path: Path) -> None:
    value = environment(tmp_path)
    mailbox = tmp_path / "not-a-mailbox"
    mailbox.mkdir()

    result = _run_import(value.db_path, mailbox, "--json")

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "error_code": "connection_malformed",
        "status": "error",
    }


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            MailboxConnectionError(MailboxConnectionErrorCode.UNAVAILABLE),
            "connection_unavailable",
        ),
        (NativeAckPublicationError("helper"), "helper_not_ready"),
        (MailboxFileError(MailboxFileErrorCode.UNSAFE_ENTRY), "mailbox_unavailable"),
        (MailboxFileError(MailboxFileErrorCode.OVERSIZE), "mailbox_unavailable"),
    ],
)
def test_import_maps_permanent_setup_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: str,
) -> None:
    value = environment(tmp_path)

    def fail(_db: Path, _mailbox: Path) -> MailboxImporter:
        raise error

    monkeypatch.setattr("health_bridge.cli_mailbox.production_importer", fail)
    result = _run_import(value.db_path, value.mailbox_path, "--json")

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {"error_code": code, "status": "error"}


def test_import_busy_stays_retryable_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = environment(tmp_path)

    def busy(_db: Path, _mailbox: Path) -> MailboxImporter:
        raise MailboxBusyError

    monkeypatch.setattr("health_bridge.cli_mailbox.production_importer", busy)
    result = _run_import(value.db_path, value.mailbox_path, "--json")

    assert result.exit_code == 2
    assert json.loads(result.stdout)["retryable"] == 1


@pytest.mark.parametrize(
    "code",
    [
        MailboxFileErrorCode.STORAGE_UNAVAILABLE,
        MailboxFileErrorCode.DELIVERY_FAILED,
        MailboxFileErrorCode.PATH_REPLACED,
    ],
)
def test_import_transient_mailbox_file_errors_stay_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: MailboxFileErrorCode
) -> None:
    value = environment(tmp_path)

    def fail(_db: Path, _mailbox: Path) -> MailboxImporter:
        raise MailboxFileError(code)

    monkeypatch.setattr("health_bridge.cli_mailbox.production_importer", fail)
    result = _run_import(value.db_path, value.mailbox_path, "--json")

    assert result.exit_code == 2
    assert json.loads(result.stdout)["retryable"] == 1


@pytest.mark.parametrize(
    "message", ["database is locked", "disk I/O error", "database or disk is full"]
)
def test_import_locked_database_during_setup_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    value = environment(tmp_path)

    def locked(_db: Path, _mailbox: Path) -> MailboxImporter:
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr("health_bridge.cli_mailbox.production_importer", locked)
    result = _run_import(value.db_path, value.mailbox_path, "--json")

    assert result.exit_code == 2
    assert json.loads(result.stdout)["retryable"] == 1


def test_import_other_sqlite_error_during_setup_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = environment(tmp_path)

    def corrupt(_db: Path, _mailbox: Path) -> MailboxImporter:
        message = "file is not a database"
        raise sqlite3.DatabaseError(message)

    monkeypatch.setattr("health_bridge.cli_mailbox.production_importer", corrupt)
    result = _run_import(value.db_path, value.mailbox_path, "--json")

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "error_code": "database_unreadable",
        "status": "error",
    }


def test_import_non_sqlite_database_is_unreadable_not_uninitialized(
    tmp_path: Path,
) -> None:
    db = tmp_path / "not-a-database.sqlite"
    _ = db.write_bytes(b"synthetic text, not an SQLite database\n" * 64)
    mailbox = tmp_path / ("04" * 16) / ("05" * 16)
    mailbox.mkdir(parents=True)

    result = _run_import(db, mailbox, "--json")

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "error_code": "database_unreadable",
        "status": "error",
    }


def test_import_unopenable_wal_shm_is_unreadable_not_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = environment(tmp_path)

    def shm_open_failed(_db: Path, _mailbox: Path) -> MailboxImporter:
        message = "disk I/O error"
        error = sqlite3.OperationalError(message)
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR_SHMOPEN
        raise error

    monkeypatch.setattr(
        "health_bridge.cli_mailbox.production_importer", shm_open_failed
    )
    result = _run_import(value.db_path, value.mailbox_path, "--json")

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "error_code": "database_unreadable",
        "status": "error",
    }
