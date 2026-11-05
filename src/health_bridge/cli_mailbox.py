from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, NoReturn

import typer
from pydantic import BaseModel, ConfigDict

from health_bridge.cli_launchd import service_app
from health_bridge.cli_mailbox_helper import helper_app
from health_bridge.mailbox.connections import (
    MailboxConnectionError,
    MailboxConnectionStore,
)
from health_bridge.mailbox.filesystem import MailboxFileError
from health_bridge.mailbox.importer import (
    MailboxBusyError,
    MailboxImportConfig,
    MailboxImporter,
)
from health_bridge.mailbox.models import MailboxImportResult
from health_bridge.mailbox.native_ack_publication import (
    NativeAckPublicationError,
    default_native_ack_publisher,
)
from health_bridge.receiver._mailbox_key_models import (
    MailboxKeyStoreErrorCode,
)
from health_bridge.receiver._mailbox_key_policy import (
    FilesystemKind,
    filesystem_kind,
    reject_prohibited_path,
)
from health_bridge.receiver.mailbox_keys import MailboxKeyStore, MailboxKeyStoreError
from health_bridge.storage.database import initialize_database

if TYPE_CHECKING:
    from health_bridge.mailbox.filesystem import MailboxDirectoryHandle

IMPORT_SETUP_ERROR_EXIT_CODE: Final = 3
_MIGRATIONS_TABLE_QUERY: Final = (
    "select 1 from sqlite_master where type = 'table' and name = 'schema_migrations'"
)

mailbox_app = typer.Typer(
    add_completion=False,
    help="Manage private local mailbox state.",
)
keys_app = typer.Typer(
    add_completion=False,
    help="Inspect the local receiver mailbox identity.",
)
mailbox_app.add_typer(keys_app, name="keys")
mailbox_app.add_typer(helper_app, name="helper")
mailbox_app.add_typer(service_app, name="service")


class MailboxKeysDoctorResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: str
    state: str | None = None
    signing_key_id: str | None = None
    agreement_key_id: str | None = None
    error_code: str | None = None


@mailbox_app.command("import")
def import_mailbox(
    db: Annotated[
        Path,
        typer.Option("--db", help="Private local Apple Health SQLite path."),
    ],
    mailbox: Annotated[
        Path,
        typer.Option("--mailbox", help="Resolved per-device mailbox directory."),
    ],
    once: Annotated[
        bool,
        typer.Option("--once", help="Run one bounded import pass and exit."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit aggregate redacted counts."),
    ] = False,
) -> None:
    if not once:
        typer.echo("Mailbox import requires --once.", err=True)
        raise typer.Exit(code=2)
    _require_initialized_database(db, json_output)
    try:
        result = production_importer(db, mailbox).import_once()
    except MailboxBusyError:
        _emit_import_result(MailboxImportResult(retryable=1), json_output)
        raise typer.Exit(code=2) from None
    except MailboxConnectionError as exc:
        _fail_import(exc.code.value, json_output)
    except NativeAckPublicationError:
        _fail_import("helper_not_ready", json_output)
    except MailboxFileError:
        _fail_import("mailbox_unavailable", json_output)
    except (MailboxKeyStoreError, OSError):
        _emit_import_result(MailboxImportResult(retryable=1), json_output)
        raise typer.Exit(code=2) from None
    _emit_import_result(result, json_output)


def _fail_import(error_code: str, json_output: bool) -> NoReturn:
    """Report a permanent setup error; exit 3 is never a transient retry."""
    if json_output:
        typer.echo(
            json.dumps(
                {"error_code": error_code, "status": "error"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        typer.echo(f"Mailbox import failed: {error_code}", err=True)
    raise typer.Exit(code=IMPORT_SETUP_ERROR_EXIT_CODE)


def _require_initialized_database(db: Path, json_output: bool) -> None:
    """Refuse a --db that is not an existing, initialized receiver database."""
    if not db.is_file():
        _fail_import("database_unavailable", json_output)
    try:
        connection = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            initialized = (
                connection.execute(_MIGRATIONS_TABLE_QUERY).fetchone() is not None
            )
        finally:
            connection.close()
    except sqlite3.OperationalError as exc:
        if _is_transient_sqlite_error(exc):
            _emit_import_result(MailboxImportResult(retryable=1), json_output)
            raise typer.Exit(code=2) from None
        _fail_import("database_unreadable", json_output)
    except sqlite3.Error:
        initialized = False
    if not initialized:
        _fail_import("database_uninitialized", json_output)


def _is_transient_sqlite_error(error: sqlite3.OperationalError) -> bool:
    """True for a busy or locked database, which a later run can read."""
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    message = str(error).lower()
    return "locked" in message or "busy" in message


def production_importer(
    db: Path,
    mailbox: Path,
    *,
    directory: MailboxDirectoryHandle | None = None,
) -> MailboxImporter:
    connections = MailboxConnectionStore.production()
    connection = connections.load(mailbox)
    ack_publisher = default_native_ack_publisher() if sys.platform == "darwin" else None
    initialize_database(db)
    return MailboxImporter(
        MailboxImportConfig(
            db_path=db,
            mailbox_path=mailbox,
            lock_path=connections.lock_path(mailbox),
            connection=connection,
            clock_ms=lambda: time.time_ns() // 1_000_000,
            directory=directory,
            ack_publisher=ack_publisher,
        )
    )


def _emit_import_result(result: MailboxImportResult, json_output: bool) -> None:
    values = {
        "imported": result.imported,
        "idempotent": result.idempotent,
        "quarantined": result.quarantined,
        "retryable": result.retryable,
        "conflict": result.conflict,
        "skipped": result.skipped,
    }
    if json_output:
        typer.echo(json.dumps(values, sort_keys=True, separators=(",", ":")))
        return
    typer.echo(" ".join(f"{key}={value}" for key, value in values.items()))


@keys_app.command("doctor")
def mailbox_keys_doctor(
    state_dir: Annotated[
        Path | None,
        typer.Option(
            "--state-dir",
            help="Use an isolated temporary key directory for synthetic QA only.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a public-only JSON health result."),
    ] = False,
) -> None:
    try:
        store = _doctor_store(state_dir)
        _ = store.load_or_create()
        summary = store.public_summary()
    except MailboxKeyStoreError as exc:
        result = MailboxKeysDoctorResult(status="error", error_code=exc.code.value)
        typer.echo(result.model_dump_json(exclude_none=True), err=not json_output)
        raise typer.Exit(code=2) from None

    result = MailboxKeysDoctorResult(
        status="ok",
        state=summary.state.value,
        signing_key_id=summary.signing_key_id,
        agreement_key_id=summary.agreement_key_id,
    )
    if json_output:
        typer.echo(result.model_dump_json(exclude_none=True))
        return
    typer.echo(f"status: {result.status}")
    typer.echo(f"state: {result.state}")
    typer.echo(f"signing_key_id: {result.signing_key_id}")
    typer.echo(f"agreement_key_id: {result.agreement_key_id}")


def _doctor_store(state_dir: Path | None) -> MailboxKeyStore:
    if state_dir is None:
        return MailboxKeyStore.production()
    reject_prohibited_path(state_dir)
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    requested = state_dir.resolve(strict=False)
    try:
        relative = requested.relative_to(temporary_root)
    except ValueError as exc:
        raise MailboxKeyStoreError(MailboxKeyStoreErrorCode.PROHIBITED_STORAGE) from exc
    if not relative.parts:
        raise MailboxKeyStoreError(MailboxKeyStoreErrorCode.PROHIBITED_STORAGE)
    kind = filesystem_kind(requested)
    if kind is not FilesystemKind.LOCAL:
        raise MailboxKeyStoreError(MailboxKeyStoreErrorCode.PROHIBITED_STORAGE)
    return MailboxKeyStore.for_testing(
        state_dir=requested,
        anchor_dir=requested.parent / f"{requested.name}.anchor",
        filesystem_kind=kind.value,
    )
