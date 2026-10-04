import json
import math
import os
import re
import secrets
import shlex
import sqlite3
import stat
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from http.client import HTTPException, HTTPResponse
from pathlib import Path
from typing import Annotated, Final, Literal, TypeAlias, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
    urlopen,
)

import typer
from pydantic import TypeAdapter
from typing_extensions import override

from health_bridge.cli_receiver_start import (
    DEFAULT_RECEIVER_HOST,
    DEFAULT_RECEIVER_PORT,
    ReceiverStartDependencies,
    ReceiverStartOptions,
    run_receiver_start,
)
from health_bridge.contract.intake_context_v1 import (
    BUNDLE_ID_PATTERN,
    PRODUCER_ID_PATTERN,
)
from health_bridge.contract.intake_context_v1 import (
    SCHEMA_NAME as INTAKE_SCHEMA_NAME,
)
from health_bridge.contract.intake_context_v1 import (
    SCHEMA_VERSION as INTAKE_SCHEMA_VERSION,
)
from health_bridge.launchd import load_runnable_launch_agent_request
from health_bridge.mailbox.connections import MailboxConnectionStore
from health_bridge.private_files import (
    PRIVATE_FILE_MODE,
    apply_private_file_mode,
    ensure_private_directory,
    write_private_text_file,
)
from health_bridge.receiver.intake_tokens import (
    INTAKE_TOKEN_PREFIX,
    IntakeProducerInactiveError,
    IntakeTokenRecord,
    IssuedIntakeToken,
    activate_intake_token,
    create_intake_token_for_active_producer,
    create_pending_intake_token,
    discard_pending_intake_token,
    intake_token_status,
    list_intake_tokens_on,
    revoke_active_intake_token,
)
from health_bridge.receiver.invitations import (
    ReceiverDeviceSelectionError,
    list_receiver_devices,
    revoke_pairing_invitation,
    revoke_receiver_device,
)
from health_bridge.receiver.mailbox_keys import MailboxKeyStore
from health_bridge.receiver.pairing import (
    ReceiverPairingInvitationBundle,
    create_receiver_pairing_bundle,
    create_receiver_pairing_invitation_bundle,
    pairing_deep_link,
)
from health_bridge.receiver.pairing_setup_page import render_pairing_setup_page
from health_bridge.receiver.server import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    INTAKE_CONTEXT_CAPABILITIES_PATH,
    serve_receiver,
)
from health_bridge.receiver.tokens import create_receiver_token, revoke_receiver_token
from health_bridge.receiver.transports import (
    PublicReceiverTransport,
    ReceiverTransport,
    ReceiverTransportSelectionError,
    select_receiver_transport,
)
from health_bridge.storage._database_locks import (
    DATABASE_ACCESS_LOCK_SUFFIX,
    DATABASE_LIFECYCLE_LOCK_SUFFIX,
)
from health_bridge.storage.database import (
    connect_database,
    connect_readonly_database,
    database_access_lock,
    database_lifecycle_lock,
    initialize_database,
)
from health_bridge.storage.intake_context import (
    IntakeProducerConflictError,
    IntakeProducerRevokedError,
    ProducerRecord,
    list_producers,
    reactivate_producer,
    read_producer,
    register_producer,
    revoke_producer,
    revoke_producer_tokens,
)

receiver_app = typer.Typer(
    add_completion=False,
    help="User-owned local receiver commands for HealthKit companion sync.",
)
REFUSE_STDOUT_MESSAGE: Final = (
    "Refusing to print receiver bearer token to stdout by default. "
    "Re-run with --print-secret to print it once, or --output-secret <path> "
    "to write the secret JSON to a private file."
)
MUTUALLY_EXCLUSIVE_OUTPUT_MESSAGE: Final = (
    "Use only one secret destination: --print-secret or --output-secret."
)
PAIRING_FORMAT_REQUIRES_FLAG_MESSAGE: Final = (
    "Pairing output format requires --print-secret because it contains a "
    "bearer-token secret. Use --format setup-page --setup-page <path> for "
    "agent-safe onboarding."
)
WRITE_FAILURE_MESSAGE: Final = (
    "Failed to write private token output file; the issued token was revoked. "
    "Re-run the command with a writable private path."
)
SETUP_PAGE_WRITE_FAILURE_MESSAGE: Final = (
    "Failed to write private pairing setup page; the issued token was revoked. "
    "Re-run the command with a writable private path."
)
INTAKE_REFUSE_STDOUT_MESSAGE: Final = (
    "Refusing to print intake token to stdout by default. "
    "Re-run with --print-secret to print it once, or --output-secret <path> "
    "to write the secret JSON to a private file."
)
INTAKE_MUTUALLY_EXCLUSIVE_OUTPUT_MESSAGE: Final = (
    "Use only one secret destination: --print-secret or --output-secret."
)
INTAKE_SETUP_SECRET_EXISTS_MESSAGE: Final = (
    "Refusing to replace the existing intake secret file {path}; nothing was "  # noqa: S105 -- a message, not a secret.
    "changed. Re-run with --rotate to issue a new token into it and revoke the "
    "token it held, or choose another --output-secret path."
)
INTAKE_SETUP_ROTATE_UNIDENTIFIABLE_MESSAGE: Final = (
    "Refusing to rotate the intake secret file {path}: it does not name an "
    "intake token this receiver knows, so the token it held could not be "
    "revoked and would stay usable; nothing was changed. Revoke it yourself "
    "with receiver intake-revoke-token after receiver intake-list-tokens, then "
    "re-run, or move the file aside."
)
INTAKE_SETUP_URL_PLACEHOLDER: Final = "<receiver URL>"
INTAKE_SETUP_URL_MESSAGE: Final = (
    "Refusing to print next steps for an unusable --url; give the http or "
    "https URL the receiver actually listens on."
)
INTAKE_WRITE_FAILURE_MESSAGE: Final = (
    "Failed to write private intake token output file; no token was issued. "
    "Re-run the command with a writable private path."
)
INTAKE_ACTIVATION_FAILURE_MESSAGE: Final = (
    "Failed to activate the intake token after writing its secret file; no "
    "token is active. Re-run the command."
)
INTAKE_ROUTES_DISABLED_MESSAGE: Final = (
    "Receiver intake routes are not enabled on this receiver. Restart it with "
    "--enable-intake-context to serve the intake-context routes."
)
INTAKE_SMOKE_URL_MESSAGE: Final = (
    "Receiver intake smoke failed: --url must be an http or https URL with a host."
)
INTAKE_SMOKE_BODY_TOO_LARGE_MESSAGE: Final = (
    "Receiver intake smoke failed: the capabilities response is too large to be a "
    "capabilities document."
)
INTAKE_SMOKE_UNREACHABLE_MESSAGE: Final = (
    "Receiver intake smoke failed: the receiver was not reachable."
)
INTAKE_SMOKE_TOKEN_FILE_MESSAGE: Final = (
    "Receiver intake smoke failed: could not read an intake token from the "  # noqa: S105 -- a message, not a secret.
    "given --token-file."
)
MAX_REQUEST_TIMEOUT_SECONDS: Final = 300.0
INTAKE_SMOKE_OK_STATUS: Final = 200
INTAKE_SMOKE_NOT_FOUND_STATUS: Final = 404
INTAKE_SMOKE_TIMEOUT_SECONDS: Final = 10
# The operations an intake uploader needs; a receiver advertising fewer is not usable.
INTAKE_SMOKE_REQUIRED_FEATURES: Final = ("upsert", "delete", "link_projection")
# A capabilities document is a few hundred bytes. Reading only this much keeps a
# large or endlessly drip-fed response from being buffered whole.
INTAKE_SMOKE_MAX_BODY_BYTES: Final = 64 * 1024
INTAKE_SMOKE_UNUSABLE_CAPABILITIES_MESSAGE: Final = (
    "Receiver intake smoke failed: the capabilities response is not a usable "
    "intake receiver ({reason}). Check that --url points at a health-bridge "
    "receiver started with --enable-intake-context."
)
PRODUCER_REVOKED_MESSAGE: Final = (
    "Intake producer {producer_id} is revoked; run receiver "
    "intake-reactivate-producer to restore it before registering or issuing "
    "tokens again."
)
INTAKE_REVOKE_PRODUCER_UNKNOWN_MESSAGE: Final = (
    "Intake producer {producer_id} is not registered for this owner, or its "
    "registration is already revoked; nothing was revoked."
)
INTAKE_REACTIVATE_PRODUCER_UNKNOWN_MESSAGE: Final = (
    "Intake producer {producer_id} is not registered for this owner, or it is "
    "already active; nothing was changed."
)
REQUEST_TIMEOUT_OUT_OF_RANGE_MESSAGE: Final = (
    "Receiver start requires --request-timeout greater than 0 and at most 300 seconds."
)
INTAKE_STORAGE_UNAVAILABLE_MESSAGE: Final = "Intake producer storage is unavailable."
INTAKE_TOKEN_STORAGE_UNAVAILABLE_MESSAGE: Final = "Intake token storage is unavailable."  # noqa: S105 - a message, not a secret.
INTAKE_PRODUCER_NOT_ACTIVE_MESSAGE: Final = (
    "Intake producer {producer_id} is not registered for this owner, or its "
    "registration is revoked; run receiver intake-register-producer first."
)
DATABASE_OUTPUT_PATH_MESSAGE: Final = (
    "Refusing to write the secret over the receiver database or one of its "
    "SQLite sidecars. Choose a different --output-secret path."
)
MIGRATIONS_REQUIRED_MESSAGE: Final = (
    "This database has no intake-context tables. Update the receiver so "
    "migrations 013 and 014 are applied, then retry."
)
INTAKE_PRODUCER_CONFLICT_MESSAGE: Final = (
    "Intake producer {producer_id} is already registered with a different writer "
    "bundle or label and was left unchanged."
)
STORED_WRITER_BUNDLE_MESSAGE: Final = " The stored writer bundle is {writer_bundle_id}."
SmokeResponseValue: TypeAlias = int | str
SmokeResponse: TypeAlias = dict[str, SmokeResponseValue]
INTAKE_CAPABILITY_FIELDS: Final = frozenset(
    {
        "authentication",
        "features",
        "max_body_bytes",
        "max_operations",
        "schema",
        "supported_versions",
    }
)
# What a 200 capabilities response must carry before intake-smoke calls it usable.
REQUIRED_INTAKE_CAPABILITY_FIELDS: Final = frozenset(
    {
        "authentication",
        "features",
        "max_body_bytes",
        "max_operations",
    }
)
# A generated intake token: the `hri_` prefix plus 32 bytes of URL-safe base64.
# Nothing else can be sent as a bearer credential without corrupting the request.
INTAKE_TOKEN_PATTERN: Final = re.compile(
    re.escape(INTAKE_TOKEN_PREFIX) + r"[A-Za-z0-9_-]{43}",
)
# Hostnames and IP literals urllib can encode into a request line.
INTAKE_SMOKE_HOST_PATTERN: Final = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=%:-]+")
PurgeIdentity: TypeAlias = tuple[int, int, int, int, int]
SMOKE_RESPONSE_ADAPTER: Final[TypeAdapter[SmokeResponse]] = TypeAdapter(SmokeResponse)
INTAKE_JSON_OBJECT_ADAPTER: Final[TypeAdapter[dict[str, object]]] = TypeAdapter(
    dict[str, object]
)
PURGE_SIDECAR_SUFFIXES: Final = ("", "-journal", "-wal", "-shm")
PURGE_WARNING: Final = (
    "Stop the receiver before confirming. This removes only the local HealthRelay "
    "SQLite database and its sidecars; it does not delete Apple Health data."
)


def _validate_request_timeout(request_timeout: float) -> None:
    """Refuse a read timeout the receiver cannot honour, before it binds."""
    if not math.isfinite(request_timeout):
        typer.echo(REQUEST_TIMEOUT_OUT_OF_RANGE_MESSAGE, err=True)
        raise typer.Exit(code=2)
    if not 0 < request_timeout <= MAX_REQUEST_TIMEOUT_SECONDS:
        typer.echo(REQUEST_TIMEOUT_OUT_OF_RANGE_MESSAGE, err=True)
        raise typer.Exit(code=2)


def _select_transport_or_exit(
    requested: PublicReceiverTransport,
    *,
    mailbox_root: Path | None,
    icloud_container_identifier: str | None,
    mailbox_allowed: bool = True,
) -> ReceiverTransport:
    try:
        return select_receiver_transport(
            requested,
            mailbox_root=mailbox_root,
            icloud_container_identifier=icloud_container_identifier,
            mailbox_allowed=mailbox_allowed,
        )
    except ReceiverTransportSelectionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


class PurgeRecoveryRequiredError(OSError):
    def __init__(
        self,
        *,
        quarantine_path: Path,
        quarantined_paths: tuple[Path, ...],
        truncated_paths: tuple[Path, ...],
        residual_paths: tuple[Path, ...],
    ) -> None:
        super().__init__("receiver database purge requires manual recovery")
        self.quarantine_path: Path = quarantine_path
        self.quarantined_paths: tuple[Path, ...] = quarantined_paths
        self.truncated_paths: tuple[Path, ...] = truncated_paths
        self.residual_paths: tuple[Path, ...] = residual_paths


@receiver_app.command("purge")
def purge_receiver_store(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm",
            help="Delete the listed local database files after safety checks.",
        ),
    ] = False,
) -> None:
    scope = tuple(Path(f"{db}{suffix}") for suffix in PURGE_SIDECAR_SUFFIXES)
    _validate_purge_scope(scope)

    existing = tuple(path for path in scope if path.exists())
    if not confirm:
        _echo_purge_result(
            status="dry-run",
            db=db,
            paths=existing,
            confirm_required=True,
        )
        return
    if not existing:
        _echo_purge_result(
            status="already-absent",
            db=db,
            paths=(),
            confirm_required=False,
        )
        return
    _validate_purge_parent(db)

    try:
        with (
            database_lifecycle_lock(
                db,
                exclusive=True,
                create=True,
                nonblocking=True,
            ),
            database_access_lock(
                db,
                exclusive=True,
                create=True,
                nonblocking=True,
            ),
        ):
            existing = _purge_database_transaction(db, scope)
    except BlockingIOError as exc:
        typer.echo("Receiver database is unavailable or still in use.", err=True)
        raise typer.Exit(code=1) from exc
    except PurgeRecoveryRequiredError as exc:
        _echo_purge_result(
            status="recovery-required",
            db=db,
            paths=exc.residual_paths,
            confirm_required=False,
            recovery=exc,
        )
        message = (
            "Receiver database purge requires recovery. Do not restart the receiver; "
        )
        message += "review the reported source and quarantine paths."
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, sqlite3.Error) as exc:
        typer.echo("Receiver database purge failed safely.", err=True)
        raise typer.Exit(code=1) from exc

    _echo_purge_result(
        status="purged" if existing else "already-absent",
        db=db,
        paths=existing,
        confirm_required=False,
    )


def _validate_purge_scope(scope: tuple[Path, ...]) -> None:
    for path in scope:
        if path.is_symlink():
            typer.echo(f"Refusing to purge symlink path: {path}", err=True)
            raise typer.Exit(code=1)
        if path.is_dir():
            typer.echo(f"Refusing to purge directory path: {path}", err=True)
            raise typer.Exit(code=1)


def _validate_purge_parent(db_path: Path) -> None:
    absolute_parent = db_path.absolute().parent
    try:
        resolved_parent = absolute_parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        typer.echo("Refusing to purge through an unavailable parent path.", err=True)
        raise typer.Exit(code=1) from exc
    if resolved_parent != absolute_parent:
        typer.echo("Refusing to purge through a symlinked parent path.", err=True)
        raise typer.Exit(code=1)


def _purge_database_transaction(
    db_path: Path,
    scope: tuple[Path, ...],
) -> tuple[Path, ...]:
    if os.name != "posix":
        message = "safe receiver purge is unavailable on this platform"
        raise OSError(message)
    _require_safe_purge_primitives()
    parent = db_path.parent
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_fd = os.open(parent, parent_flags)
    connection: sqlite3.Connection | None = None
    try:
        identities = _validated_purge_identities(parent_fd, scope)
        if db_path.exists():
            uri = f"{db_path.resolve(strict=True).as_uri()}?mode=rw"
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=0,
                isolation_level=None,
            )
            _ = connection.execute("begin exclusive")
        locked_identities = _validated_purge_identities(parent_fd, scope)
        if locked_identities != identities:
            message = "purge targets changed while acquiring database lock"
            raise OSError(message)
        if not identities:
            return ()
        _quarantine_and_delete(parent_fd, db_path, identities)
        return tuple(path for path in scope if path.name in identities)
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        os.close(parent_fd)


def _validated_purge_identities(
    parent_fd: int,
    scope: tuple[Path, ...],
) -> dict[str, PurgeIdentity]:
    identities: dict[str, PurgeIdentity] = {}
    for path in scope:
        try:
            path_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(path_stat.st_mode):
            message = f"purge target is not a regular file: {path.name}"
            raise OSError(message)
        if hasattr(os, "getuid") and path_stat.st_uid != os.getuid():
            message = f"purge target is not owned by this user: {path.name}"
            raise OSError(message)
        if path_stat.st_nlink != 1:
            message = f"purge target has an unsafe link count: {path.name}"
            raise OSError(message)
        identities[path.name] = _purge_identity(path_stat)
    return identities


def _quarantine_and_delete(
    parent_fd: int,
    db_path: Path,
    identities: dict[str, PurgeIdentity],
) -> None:
    quarantine_name = f".{db_path.name}.purge-{secrets.token_hex(8)}"
    quarantine_path = db_path.parent / quarantine_name
    os.mkdir(quarantine_name, mode=0o700, dir_fd=parent_fd)
    quarantine_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    quarantine_fd = os.open(quarantine_name, quarantine_flags, dir_fd=parent_fd)
    moved: list[str] = []
    opened: dict[str, int] = {}
    truncated: list[str] = []
    purged = False
    try:
        _move_paths_to_quarantine(
            parent_fd,
            quarantine_fd,
            identities,
            moved,
        )
        _require_absent_source_paths(parent_fd, db_path)
        opened = _open_validated_quarantined_paths(quarantine_fd, identities)
        _require_absent_source_paths(parent_fd, db_path)
        deletion_order = [name for name in identities if name != db_path.name]
        if db_path.name in identities:
            deletion_order.append(db_path.name)
        for name in deletion_order:
            os.ftruncate(opened[name], 0)
            truncated.append(name)
            os.fsync(opened[name])
        _require_no_residual_after_purge(
            parent_fd,
            db_path,
            quarantine_path,
            moved,
            truncated,
        )
        purged = True
    except PurgeRecoveryRequiredError:
        raise
    except Exception as exc:
        if truncated:
            raise _purge_recovery_error(
                quarantine_path,
                moved,
                truncated,
                _present_source_paths(parent_fd, db_path),
            ) from exc
        unrestored = _rollback_quarantine(parent_fd, quarantine_fd, moved, identities)
        if unrestored:
            raise PurgeRecoveryRequiredError(
                quarantine_path=quarantine_path,
                quarantined_paths=tuple(quarantine_path / name for name in unrestored),
                truncated_paths=(),
                residual_paths=_present_source_paths(parent_fd, db_path),
            ) from exc
        raise
    finally:
        for fd in opened.values():
            os.close(fd)
        os.close(quarantine_fd)
        if not purged and not truncated:
            with suppress(OSError):
                os.rmdir(quarantine_name, dir_fd=parent_fd)


def _purge_recovery_error(
    quarantine_path: Path,
    moved: list[str],
    truncated: list[str],
    residual_paths: tuple[Path, ...],
) -> PurgeRecoveryRequiredError:
    return PurgeRecoveryRequiredError(
        quarantine_path=quarantine_path,
        quarantined_paths=tuple(quarantine_path / name for name in moved),
        truncated_paths=tuple(quarantine_path / name for name in truncated),
        residual_paths=residual_paths,
    )


def _require_no_residual_after_purge(
    parent_fd: int,
    db_path: Path,
    quarantine_path: Path,
    moved: list[str],
    truncated: list[str],
) -> None:
    residual_paths = _present_source_paths(parent_fd, db_path)
    if residual_paths:
        raise _purge_recovery_error(
            quarantine_path,
            moved,
            truncated,
            residual_paths,
        )


def _move_paths_to_quarantine(
    parent_fd: int,
    quarantine_fd: int,
    identities: dict[str, PurgeIdentity],
    moved: list[str],
) -> None:
    for name, identity in identities.items():
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _purge_identity(current) != identity:
            message = f"purge target changed before quarantine: {name}"
            raise OSError(message)
        os.rename(
            name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=quarantine_fd,
        )
        moved.append(name)
        moved_stat = os.stat(name, dir_fd=quarantine_fd, follow_symlinks=False)
        if _purge_identity(moved_stat) != identity:
            message = f"purge target changed during quarantine: {name}"
            raise OSError(message)


def _require_absent_source_paths(
    parent_fd: int,
    db_path: Path,
) -> None:
    present = _present_source_paths(parent_fd, db_path)
    if present:
        message = f"purge target reappeared during quarantine: {present[0].name}"
        raise OSError(message)


def _present_source_paths(
    parent_fd: int,
    db_path: Path,
) -> tuple[Path, ...]:
    present: list[Path] = []
    for suffix in PURGE_SIDECAR_SUFFIXES:
        name = Path(f"{db_path}{suffix}").name
        try:
            _ = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        present.append(db_path.parent / name)
    return tuple(present)


def _open_validated_quarantined_paths(
    quarantine_fd: int,
    identities: dict[str, PurgeIdentity],
) -> dict[str, int]:
    opened: dict[str, int] = {}
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    for name, identity in identities.items():
        try:
            fd = os.open(name, flags, dir_fd=quarantine_fd)
            _require_opened_purge_identity(fd, name, identity)
            opened[name] = fd
        except Exception:
            for opened_fd in opened.values():
                os.close(opened_fd)
            raise
    return opened


def _require_opened_purge_identity(
    fd: int,
    name: str,
    identity: PurgeIdentity,
) -> None:
    if _purge_identity(os.fstat(fd)) != identity:
        message = f"quarantined purge target changed: {name}"
        raise OSError(message)


def _rollback_quarantine(
    parent_fd: int,
    quarantine_fd: int,
    moved: list[str],
    identities: dict[str, PurgeIdentity],
) -> tuple[str, ...]:
    unrestored: list[str] = []
    for name in reversed(moved):
        try:
            quarantined = os.stat(name, dir_fd=quarantine_fd, follow_symlinks=False)
        except FileNotFoundError:
            unrestored.append(name)
            continue
        if _purge_identity(quarantined) != identities[name]:
            unrestored.append(name)
            continue
        try:
            _ = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.rename(
                name,
                name,
                src_dir_fd=quarantine_fd,
                dst_dir_fd=parent_fd,
            )
            restored = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _purge_identity(restored) != identities[name]:
                unrestored.append(name)
        else:
            unrestored.append(name)
    return tuple(reversed(unrestored))


def _require_safe_purge_primitives() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        message = "safe receiver purge requires O_NOFOLLOW and O_DIRECTORY"
        raise OSError(message)
    for function in (os.open, os.stat, os.rename, os.mkdir, os.rmdir):
        if function not in os.supports_dir_fd:
            message = "safe receiver purge requires directory-relative operations"
            raise OSError(message)
    if os.stat not in os.supports_follow_symlinks:
        message = "safe receiver purge requires no-follow stat operations"
        raise OSError(message)


def _purge_identity(path_stat: os.stat_result) -> PurgeIdentity:
    return (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mode,
        path_stat.st_uid,
        path_stat.st_nlink,
    )


def _echo_purge_result(
    *,
    status: str,
    db: Path,
    paths: tuple[Path, ...],
    confirm_required: bool,
    recovery: PurgeRecoveryRequiredError | None = None,
) -> None:
    payload: dict[str, object] = {
        "status": status,
        "database": str(db),
        "paths": [str(path) for path in paths],
        "confirm_required": confirm_required,
        "warning": PURGE_WARNING,
    }
    if recovery is not None:
        payload["quarantine_path"] = str(recovery.quarantine_path)
        payload["quarantined_paths"] = [
            str(path) for path in recovery.quarantined_paths
        ]
        payload["truncated_paths"] = [str(path) for path in recovery.truncated_paths]
    typer.echo(
        json.dumps(
            payload,
            sort_keys=True,
        )
    )


@receiver_app.command("create-token")
def create_token(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    label: Annotated[
        str,
        typer.Option("--label", help="Human-readable device or companion label."),
    ],
    print_secret: Annotated[
        bool,
        typer.Option(
            "--print-secret",
            help="Print the one-time receiver bearer token to stdout.",
        ),
    ] = False,
    output_secret: Annotated[
        Path | None,
        typer.Option(
            "--output-secret",
            help="Write one-time receiver bearer token JSON to this private file.",
        ),
    ] = None,
) -> None:
    if print_secret and output_secret is not None:
        typer.echo(MUTUALLY_EXCLUSIVE_OUTPUT_MESSAGE, err=True)
        raise typer.Exit(code=1)
    if not print_secret and output_secret is None:
        typer.echo(REFUSE_STDOUT_MESSAGE, err=True)
        raise typer.Exit(code=1)

    if output_secret is not None:
        try:
            _validate_private_secret_output_path(output_secret, db=db)
        except ReceiverDatabaseOutputError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        except OSError as exc:
            typer.echo(
                f"Failed to open private token output file: {exc.strerror}",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    issued = create_receiver_token(db, label=label)
    secret_payload = {
        "label": issued.label,
        "token": issued.token,
        "token_prefix": issued.token_prefix,
        "warning": (
            "Store this token now; it is shown once and only a hash is kept locally."
        ),
    }
    if output_secret is not None:
        secret_text = json.dumps(secret_payload, sort_keys=True) + "\n"
        try:
            write_private_text_file(output_secret, secret_text)
        except OSError as exc:
            revoke_receiver_token(db, issued.token_prefix)
            typer.echo(WRITE_FAILURE_MESSAGE, err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(
            json.dumps(
                {
                    "label": issued.label,
                    "secret_file": str(output_secret),
                    "token_prefix": issued.token_prefix,
                    "warning": (
                        "Secret token JSON was written to the requested private "
                        "file. Keep it out of chat, Git, wiki, and logs."
                    ),
                },
                sort_keys=True,
            ),
        )
        return

    typer.echo(json.dumps(secret_payload, sort_keys=True))


class ReceiverDatabaseOutputError(OSError):
    """The requested secret destination is the receiver database or a sidecar."""

    def __init__(self) -> None:
        super().__init__(DATABASE_OUTPUT_PATH_MESSAGE)


def _validate_private_secret_output_path(path: Path, *, db: Path) -> None:
    # Checked before any directory is created, so a rejected destination leaves
    # no folders behind either.
    _refuse_receiver_database_destination(path, db=db)
    ensure_private_directory(path.parent)
    if path.is_symlink():
        msg = f"refusing to write private token file through symlink: {path}"
        raise OSError(msg)
    if path.is_dir():
        raise IsADirectoryError(str(path))


def _refuse_receiver_database_destination(path: Path, *, db: Path) -> None:
    """Refuse a secret destination that would replace the database, a sidecar or a lock.

    ``write_private_text_file`` replaces its destination atomically, so a
    destination that resolves to the receiver database would destroy the store
    the token was just issued into. Paths are compared after full resolution so
    ``..`` segments, symlinked directories and an existing symlinked destination
    all land on the same answer.
    """
    destination = _resolved_or_absolute(path)
    protected = {
        _resolved_or_absolute(Path(f"{db}{suffix}"))
        for suffix in (
            *PURGE_SIDECAR_SUFFIXES,
            DATABASE_LIFECYCLE_LOCK_SUFFIX,
            DATABASE_ACCESS_LOCK_SUFFIX,
        )
    }
    if destination in protected:
        raise ReceiverDatabaseOutputError


def _resolved_or_absolute(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path.absolute()


@receiver_app.command("create-pairing")
def create_pairing(  # noqa: PLR0912, PLR0913 - Typer exposes CLI branches/options.
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    label: Annotated[
        str,
        typer.Option("--label", help="Human-readable device or companion label."),
    ],
    receiver_url: Annotated[
        str,
        typer.Option(
            "--receiver-url", help="Receiver /v1/batches URL for the companion."
        ),
    ],
    transport: Annotated[
        PublicReceiverTransport,
        typer.Option(
            "--transport",
            help=(
                "Choose Direct/Tailscale-compatible delivery or explicit "
                "Encrypted iCloud Mailbox (Beta)."
            ),
        ),
    ] = "direct",
    mailbox_root: Annotated[
        Path | None,
        typer.Option(
            "--mailbox-root",
            help="Existing macOS iCloud Documents HealthBridgeMailbox/v1 path.",
        ),
    ] = None,
    icloud_container_identifier: Annotated[
        str | None,
        typer.Option(
            "--icloud-container-identifier",
            help="Expected iCloud container identifier for the mailbox root.",
        ),
    ] = None,
    output_format: Annotated[
        Literal["json", "deeplink", "setup-page"],
        typer.Option(
            "--format",
            help=(
                "Output secret JSON, secret deep-link summary, or a private "
                "setup page. Secret stdout formats require --print-secret."
            ),
        ),
    ] = "setup-page",
    setup_page: Annotated[
        Path | None,
        typer.Option("--setup-page", help="Path for --format setup-page HTML output."),
    ] = None,
    print_secret: Annotated[
        bool,
        typer.Option(
            "--print-secret",
            help="Allow secret-bearing pairing output on stdout for expert use.",
        ),
    ] = False,
    legacy_v1: Annotated[
        bool,
        typer.Option(
            "--legacy-v1",
            help="Issue a legacy long-lived bearer-token pairing bundle.",
        ),
    ] = False,
) -> None:
    if output_format == "setup-page" and setup_page is None:
        typer.echo("--setup-page is required when --format setup-page.", err=True)
        raise typer.Exit(code=1)
    if output_format in {"json", "deeplink"} and not print_secret:
        typer.echo(PAIRING_FORMAT_REQUIRES_FLAG_MESSAGE, err=True)
        raise typer.Exit(code=1)
    selected_transport = _select_transport_or_exit(
        transport,
        mailbox_root=mailbox_root,
        icloud_container_identifier=icloud_container_identifier,
        mailbox_allowed=not legacy_v1,
    )

    if legacy_v1:
        bundle = create_receiver_pairing_bundle(
            db,
            label=label,
            receiver_url=receiver_url,
        )
    else:
        bundle = create_receiver_pairing_invitation_bundle(
            db,
            label=label,
            receiver_url=receiver_url,
            transport=selected_transport,
        )
    deep_link = pairing_deep_link(bundle)
    if output_format == "deeplink":
        if isinstance(bundle, ReceiverPairingInvitationBundle):
            output = {
                "label": bundle.label,
                "pairing_url": deep_link,
                "pairing_schema_id": bundle.schema_id,
                "invitation_expires_at": bundle.expires_at,
                "warning": bundle.warning,
            }
        else:
            output = {
                "label": bundle.label,
                "pairing_url": deep_link,
                "token_prefix": bundle.token_prefix,
                "warning": bundle.warning,
            }
    elif output_format == "setup-page":
        setup_page_path = cast("Path", setup_page)
        try:
            write_private_text_file(
                setup_page_path,
                render_pairing_setup_page(bundle, deep_link),
            )
        except OSError as exc:
            if isinstance(bundle, ReceiverPairingInvitationBundle):
                revoke_pairing_invitation(db, bundle.invitation_id)
            else:
                revoke_receiver_token(db, bundle.token_prefix)
            typer.echo(SETUP_PAGE_WRITE_FAILURE_MESSAGE, err=True)
            raise typer.Exit(code=1) from exc
        if isinstance(bundle, ReceiverPairingInvitationBundle):
            output = {
                "label": bundle.label,
                "setup_page": str(setup_page_path),
                "pairing_schema_id": bundle.schema_id,
                "invitation_expires_at": bundle.expires_at,
                "warning": (
                    "Setup page contains a temporary, single-use pairing invitation. "
                    "Open it on your own device and delete it after pairing."
                ),
            }
        else:
            output = {
                "label": bundle.label,
                "setup_page": str(setup_page_path),
                "token_prefix": bundle.token_prefix,
                "warning": (
                    "Legacy setup page contains a pairing secret. Open it on your "
                    "own device and delete it after pairing."
                ),
            }
    else:
        output = bundle.model_dump(mode="json", exclude_none=True)
        output["pairing_url"] = deep_link
    typer.echo(json.dumps(output, sort_keys=True))


@receiver_app.command("list-devices")
def list_devices(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    include_revoked: Annotated[
        bool,
        typer.Option("--include-revoked", help="Include previously revoked devices."),
    ] = False,
) -> None:
    try:
        devices = list_receiver_devices(db, include_revoked=include_revoked)
    except (sqlite3.Error, OSError) as exc:
        typer.echo("Receiver device storage is unavailable.", err=True)
        raise typer.Exit(code=1) from exc
    output = {
        "devices": [
            {
                "device_ref": device.device_ref,
                "label": device.label,
                "platform": device.platform,
                "last_paired_at": device.last_paired_at,
                "revoked_at": device.revoked_at,
            }
            for device in devices
        ]
    }
    typer.echo(json.dumps(output, sort_keys=True))


@receiver_app.command("revoke-device")
def revoke_device(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    device_ref: Annotated[
        str,
        typer.Option(
            "--device-ref",
            help="Redacted reference returned by receiver list-devices.",
        ),
    ],
) -> None:
    try:
        revoked_token_count = revoke_receiver_device(db, device_ref)
    except ReceiverDeviceSelectionError as exc:
        typer.echo("Device reference is invalid or unavailable.", err=True)
        raise typer.Exit(code=1) from exc
    except (sqlite3.Error, OSError) as exc:
        typer.echo("Receiver device storage is unavailable.", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        json.dumps(
            {
                "revoked_device_ref": device_ref.strip().lower(),
                "revoked_token_count": revoked_token_count,
            },
            sort_keys=True,
        )
    )


@receiver_app.command("revoke-token")
def revoke_token(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    token_prefix: Annotated[
        str,
        typer.Option("--token-prefix", help="Prefix returned by create-token."),
    ],
) -> None:
    revoke_receiver_token(db, token_prefix)
    typer.echo(json.dumps({"revoked_token_prefix": token_prefix}, sort_keys=True))


@receiver_app.command("intake-register-producer")
def intake_register_producer(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    owner_id: Annotated[
        str,
        typer.Option("--owner-id", help="Owner identity the tokens are bound to."),
    ],
    producer_id: Annotated[
        str,
        typer.Option(
            "--producer-id",
            help="Stable producer identifier, e.g. an app slug.",
        ),
    ],
    writer_bundle_id: Annotated[
        str,
        typer.Option(
            "--writer-bundle-id",
            help="Bundle identifier that writes this producer.",
        ),
    ],
    label: Annotated[
        str,
        typer.Option("--label", help="Human-readable producer label."),
    ],
) -> None:
    invalid = _intake_producer_input_error(
        owner_id=owner_id,
        producer_id=producer_id,
        writer_bundle_id=writer_bundle_id,
        label=label,
    )
    if invalid is not None:
        typer.echo(invalid, err=True)
        raise typer.Exit(code=1)

    record, created = _register_intake_producer_or_exit(
        db,
        owner_id=owner_id,
        producer_id=producer_id,
        writer_bundle_id=writer_bundle_id,
        label=label,
    )

    typer.echo(
        json.dumps(
            {
                **_intake_producer_payload(record),
                "status": "registered" if created else "already-registered",
            },
            sort_keys=True,
        )
    )


@receiver_app.command("intake-list-producers")
def intake_list_producers(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
) -> None:
    try:
        records = _read_intake_producers(db)
    except _MissingIntakeTablesError as exc:
        typer.echo(MIGRATIONS_REQUIRED_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc
    except (sqlite3.Error, OSError) as exc:
        typer.echo(INTAKE_STORAGE_UNAVAILABLE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {"producers": [_intake_producer_payload(record) for record in records]},
            sort_keys=True,
        )
    )


@receiver_app.command("intake-revoke-producer")
def intake_revoke_producer(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    owner_id: Annotated[
        str,
        typer.Option("--owner-id", help="Owner identity the tokens are bound to."),
    ],
    producer_id: Annotated[
        str,
        typer.Option(
            "--producer-id",
            help="Registered producer identifier.",
        ),
    ],
) -> None:
    try:
        record, revoked_token_count = revoke_intake_producer(
            db,
            owner_id=owner_id,
            producer_id=producer_id,
        )
    except IntakeProducerUnavailableError as exc:
        typer.echo(
            INTAKE_REVOKE_PRODUCER_UNKNOWN_MESSAGE.format(producer_id=producer_id),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except (sqlite3.Error, OSError) as exc:
        typer.echo(INTAKE_STORAGE_UNAVAILABLE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {
                **_intake_producer_payload(record),
                "revoked_token_count": revoked_token_count,
                "status": "revoked",
            },
            sort_keys=True,
        )
    )


@receiver_app.command("intake-reactivate-producer")
def intake_reactivate_producer(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    owner_id: Annotated[
        str,
        typer.Option("--owner-id", help="Owner identity the tokens are bound to."),
    ],
    producer_id: Annotated[
        str,
        typer.Option(
            "--producer-id",
            help="Registered producer identifier.",
        ),
    ],
) -> None:
    try:
        record = reactivate_intake_producer(
            db,
            owner_id=owner_id,
            producer_id=producer_id,
        )
    except IntakeProducerUnavailableError as exc:
        typer.echo(
            INTAKE_REACTIVATE_PRODUCER_UNKNOWN_MESSAGE.format(producer_id=producer_id),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except (sqlite3.Error, OSError) as exc:
        typer.echo(INTAKE_STORAGE_UNAVAILABLE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {
                **_intake_producer_payload(record),
                "status": "reactivated",
                "warning": (
                    "Tokens revoked with the producer stay revoked; issue a new "
                    "intake token with intake-create-token."
                ),
            },
            sort_keys=True,
        )
    )


@receiver_app.command("intake-create-token")
def intake_create_token(  # noqa: PLR0913 -- Typer exposes independent token options.
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    owner_id: Annotated[
        str,
        typer.Option("--owner-id", help="Owner identity the token authenticates as."),
    ],
    producer_id: Annotated[
        str,
        typer.Option(
            "--producer-id",
            help="Registered producer identifier.",
        ),
    ],
    label: Annotated[
        str,
        typer.Option("--label", help="Human-readable device or client label."),
    ],
    print_secret: Annotated[
        bool,
        typer.Option(
            "--print-secret", help="Print the one-time intake token to stdout."
        ),
    ] = False,
    output_secret: Annotated[
        Path | None,
        typer.Option(
            "--output-secret",
            help="Write one-time intake token JSON to this private file.",
        ),
    ] = None,
) -> None:
    if print_secret and output_secret is not None:
        typer.echo(INTAKE_MUTUALLY_EXCLUSIVE_OUTPUT_MESSAGE, err=True)
        raise typer.Exit(code=1)
    if not print_secret and output_secret is None:
        typer.echo(INTAKE_REFUSE_STDOUT_MESSAGE, err=True)
        raise typer.Exit(code=1)

    if output_secret is not None:
        _validate_intake_secret_output_path(output_secret, db=db)

    issued = _issue_intake_token_or_exit(
        db,
        owner_id=owner_id,
        producer_id=producer_id,
        label=label,
        pending=output_secret is not None,
    )

    secret_payload = {
        "label": issued.label,
        "owner_id": owner_id,
        "producer_id": producer_id,
        "token": issued.token,
        "token_prefix": issued.token_prefix,
        "warning": (
            "Store this token now; it is shown once and only a hash is kept locally."
        ),
    }
    if output_secret is not None:
        _echo_intake_token_file_result(
            db,
            issued=issued,
            owner_id=owner_id,
            producer_id=producer_id,
            output_secret=output_secret,
            secret_payload=secret_payload,
        )
        return

    typer.echo(json.dumps(secret_payload, sort_keys=True))


def _validate_intake_secret_output_path(output_secret: Path, *, db: Path) -> None:
    try:
        _validate_private_secret_output_path(output_secret, db=db)
    except ReceiverDatabaseOutputError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        typer.echo(
            f"Failed to open private token output file: {exc.strerror}",
            err=True,
        )
        raise typer.Exit(code=1) from exc


def _echo_intake_token_file_result(  # noqa: PLR0913 -- one identity per issuance step.
    db: Path,
    *,
    issued: IssuedIntakeToken,
    owner_id: str,
    producer_id: str,
    output_secret: Path,
    secret_payload: Mapping[str, object],
) -> None:
    summary = _write_and_activate_intake_token(
        db,
        issued=issued,
        owner_id=owner_id,
        producer_id=producer_id,
        output_secret=output_secret,
        secret_payload=secret_payload,
    )
    typer.echo(json.dumps(summary, sort_keys=True))


def _write_and_activate_intake_token(  # noqa: PLR0913 -- one identity per issuance step.
    db: Path,
    *,
    issued: IssuedIntakeToken,
    owner_id: str,
    producer_id: str,
    output_secret: Path,
    secret_payload: Mapping[str, object],
) -> dict[str, object]:
    """Write the secret privately, then activate the token that secret unlocks.

    The token row is inserted pending, a state of its own, so nothing can
    authenticate with it until the file is on disk. A failed write therefore
    leaves no active credential even if the database cannot be reached to revoke
    one afterwards; the unusable row is then dropped. Activation happens after
    the write, so a token is never usable before the secret it belongs to exists,
    and activation promotes only a row still marked pending, with the producer
    rechecked in the same statement, so neither a revocation applied from another
    terminal nor a producer revoked in between can produce an active credential.
    Both a write that fails after replacing its destination and a failed
    activation put any file this command replaced back, so an existing credential
    file is never destroyed by a failed issuance.
    """
    secret_text = json.dumps(secret_payload, sort_keys=True) + "\n"
    if _is_unreadable_regular_file(output_secret):
        # A file this command cannot read back cannot be restored after a failed
        # issuance, so it is never replaced: refuse before anything is written.
        _discard_pending_intake_token_or_warn(db, issued.token_prefix)
        hint = "move it away or choose another --output-secret path"
        refusal = (
            f"Refusing to replace {output_secret}: it cannot be read back; {hint}."
        )
        typer.echo(refusal, err=True)
        raise typer.Exit(code=1)
    replaced_secret = _existing_secret_text(output_secret)
    try:
        write_private_text_file(output_secret, secret_text)
    except OSError as exc:
        # The write replaces its destination before it can still fail (chmod or
        # directory fsync), so a late failure leaves this command's unusable
        # token where a working credential used to be. Put the old file back.
        _discard_pending_intake_token_or_warn(db, issued.token_prefix)
        _restore_secret_file_or_warn(output_secret, replaced_secret)
        typer.echo(INTAKE_WRITE_FAILURE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc
    try:
        activated = activate_intake_token(
            db,
            owner_id=owner_id,
            producer_id=producer_id,
            token_prefix=issued.token_prefix,
        )
    except (sqlite3.Error, OSError) as exc:
        _discard_pending_intake_token_or_warn(db, issued.token_prefix)
        _restore_secret_file_or_warn(output_secret, replaced_secret)
        typer.echo(INTAKE_ACTIVATION_FAILURE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc
    if activated != 1:
        _discard_pending_intake_token_or_warn(db, issued.token_prefix)
        _restore_secret_file_or_warn(output_secret, replaced_secret)
        typer.echo(INTAKE_ACTIVATION_FAILURE_MESSAGE, err=True)
        raise typer.Exit(code=1)
    return {
        "label": issued.label,
        "owner_id": secret_payload["owner_id"],
        "producer_id": secret_payload["producer_id"],
        "secret_file": str(output_secret),
        "token_prefix": issued.token_prefix,
        "warning": (
            "Secret intake token JSON was written to the requested "
            "private file. Keep it out of chat, Git, wiki, and logs."
        ),
    }


@receiver_app.command("intake-list-tokens")
def intake_list_tokens(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
) -> None:
    try:
        tokens = _read_intake_tokens(db)
    except _MissingIntakeTablesError as exc:
        typer.echo(MIGRATIONS_REQUIRED_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc
    except (sqlite3.Error, OSError) as exc:
        typer.echo(INTAKE_TOKEN_STORAGE_UNAVAILABLE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {
                "tokens": [
                    {
                        "token_prefix": token.token_prefix,
                        "owner_id": token.owner_id,
                        "producer_id": token.producer_id,
                        "label": token.label,
                        "created_at": token.created_at,
                        "revoked_at": token.revoked_at,
                        "status": intake_token_status(token.revoked_at),
                    }
                    for token in tokens
                ]
            },
            sort_keys=True,
        )
    )


@receiver_app.command("intake-revoke-token")
def intake_revoke_token(
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    token_prefix: Annotated[
        str,
        typer.Option("--token-prefix", help="Prefix returned by intake-create-token."),
    ],
) -> None:
    try:
        revoked_count = revoke_active_intake_token(db, token_prefix)
    except (sqlite3.Error, OSError) as exc:
        typer.echo(INTAKE_TOKEN_STORAGE_UNAVAILABLE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc
    if revoked_count == 0:
        typer.echo(
            "No active intake token matches that prefix; nothing was revoked.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(
        json.dumps(
            {
                "revoked_token_prefix": token_prefix,
                "revoked_token_count": revoked_count,
            },
            sort_keys=True,
        )
    )


def _discard_pending_intake_token_or_warn(db: Path, token_prefix: str) -> None:
    """Drop an unusable pending token row; a failure here cannot leave it active."""
    try:
        discard_pending_intake_token(db, token_prefix)
    except (sqlite3.Error, OSError):
        typer.echo(
            "A pending intake token row could not be removed; it stays revoked.",
            err=True,
        )


@receiver_app.command("intake-setup")
def intake_setup(  # noqa: PLR0913 -- Typer exposes independent setup options.
    db: Annotated[
        Path,
        typer.Option("--db", help="User-owned SQLite database path."),
    ],
    owner_id: Annotated[
        str,
        typer.Option("--owner-id", help="Owner identity the tokens are bound to."),
    ],
    producer_id: Annotated[
        str,
        typer.Option(
            "--producer-id",
            help="Stable producer identifier, e.g. an app slug.",
        ),
    ],
    writer_bundle_id: Annotated[
        str,
        typer.Option(
            "--writer-bundle-id",
            help="Bundle identifier that writes this producer.",
        ),
    ],
    label: Annotated[
        str,
        typer.Option("--label", help="Human-readable producer and device label."),
    ],
    output_secret: Annotated[
        Path,
        typer.Option(
            "--output-secret",
            help="Private file the one-time intake token JSON is written to.",
        ),
    ],
    rotate: Annotated[
        bool,
        typer.Option(
            "--rotate",
            help=(
                "Replace an existing secret file with a fresh token and revoke "
                "the token it held."
            ),
        ),
    ] = False,
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help=(
                "Receiver endpoint the next steps should smoke-test, e.g. "
                "https://receiver.example:8765. Omit it to have the next steps "
                "print a placeholder to fill in."
            ),
        ),
    ] = None,
    start_option: Annotated[
        list[str] | None,
        typer.Option(
            "--start-option",
            help=(
                "Receiver start option to repeat in the printed restart command, "
                "e.g. --start-option=--service-config=receiver.json. May be "
                "given more than once."
            ),
        ),
    ] = None,
) -> dict[str, object]:
    """Register the producer, issue its intake token, and print the rest.

    Intake context takes several commands in a fixed order, and the order is the
    hard part: a token can only be issued for a producer that is already
    registered, and the token has to reach a private file before it can be
    activated. This command does the database half in that order and prints what
    is left, so the one unsafe mistake (a token pasted into a terminal, a chat or
    a log) is not needed to get set up.

    Every step reuses the single-command paths, so this command never has a
    weaker rule than the commands it replaces: the producer registration is the
    idempotent one, and the token goes through the same write-then-activate
    sequence, which leaves no active credential if the write fails. The token
    itself is never printed. An existing ``--output-secret`` file is refused
    untouched, because overwriting it would destroy a working credential the
    caller cannot re-derive; the file is claimed with an exclusive create, so
    two concurrent runs cannot both be told the path is free and then race to
    replace each other's secret. ``--rotate`` is the explicit way to replace it,
    and it revokes the token the file held so only one credential for this
    producer stays usable: a rotation whose old token cannot be identified from
    the file is refused rather than leaving that credential usable.

    The printed commands carry the paths and the receiver endpoint this run was
    given, shell-quoted, instead of a built-in endpoint that a non-default host,
    port or service config would silently contradict.

    Returns the printed payload so callers and tests can read it without
    re-parsing stdout.
    """
    invalid = _intake_producer_input_error(
        owner_id=owner_id,
        producer_id=producer_id,
        writer_bundle_id=writer_bundle_id,
        label=label,
    )
    if invalid is not None:
        typer.echo(invalid, err=True)
        raise typer.Exit(code=1)

    receiver_url = _intake_setup_receiver_url_or_exit(url)
    extra_start_options = tuple(start_option or ())

    # Checked before any claim, so a symlinked, non-directory or database-shaped
    # destination is refused before anything is created or read.
    _validate_intake_secret_output_path(output_secret, db=db)

    # Read before the write, so the token being replaced is known even though its
    # file is gone afterwards. A file that cannot be named is refused: a rotation
    # that cannot revoke what it replaces would leave that credential usable.
    replaced_prefix = _rotation_prefix_or_exit(db, output_secret) if rotate else None

    # Claimed exclusively, not merely checked, so a second run that starts while
    # this one is still writing is refused instead of replacing the secret this
    # run is about to write and stranding the first token.
    if not rotate and not _reserve_secret_output_path(output_secret):
        typer.echo(
            INTAKE_SETUP_SECRET_EXISTS_MESSAGE.format(path=output_secret),
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        payload = _intake_setup_after_claim(
            db,
            owner_id=owner_id,
            producer_id=producer_id,
            writer_bundle_id=writer_bundle_id,
            label=label,
            output_secret=output_secret,
            replaced_prefix=replaced_prefix,
            url=receiver_url,
            extra_start_options=extra_start_options,
        )
    except BaseException:
        # The claim is not a credential, so a failed run must not leave one
        # behind that blocks the retry.
        _release_secret_output_claim(output_secret, rotate=rotate)
        raise

    typer.echo(json.dumps(payload, sort_keys=True))
    return payload


def _rotation_prefix_or_exit(db: Path, output_secret: Path) -> str | None:
    """The prefix ``--rotate`` must retire, refusing when it cannot be recovered.

    A missing file has nothing to rotate, so a fresh token is simply issued. An
    existing file whose prefix cannot be read, or whose prefix names no token
    this receiver knows, is refused: the write would destroy the only record of
    a credential the rotation promised to revoke.
    """
    if not output_secret.exists():
        return None
    prefix = _existing_secret_token_prefix(output_secret)
    if prefix is None or not _intake_token_prefix_is_known(db, prefix):
        typer.echo(
            INTAKE_SETUP_ROTATE_UNIDENTIFIABLE_MESSAGE.format(path=output_secret),
            err=True,
        )
        raise typer.Exit(code=1)
    return prefix


def _intake_token_prefix_is_known(db: Path, prefix: str) -> bool:
    """True when the prefix has the intake shape and is a token this store holds.

    A read-only listing decides it, so checking a rotation's old token creates
    nothing and revokes nothing. An unreadable store is treated as unknown,
    which refuses the rotation rather than guessing.
    """
    if not prefix.startswith(INTAKE_TOKEN_PREFIX):
        return False
    try:
        return prefix in {token.token_prefix for token in _read_intake_tokens(db)}
    except (sqlite3.Error, OSError, _MissingIntakeTablesError):
        return False


def _reserve_secret_output_path(output_secret: Path) -> bool:
    """Claim the destination for this run, or report that it is already taken.

    ``O_CREAT | O_EXCL`` is the claim: the kernel resolves existence and
    creation in one step, so two concurrent runs cannot both pass. The parent
    directory is created first, because the exclusive create cannot create one.
    """
    ensure_private_directory(output_secret.parent)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(output_secret, flags, PRIVATE_FILE_MODE)
    except FileExistsError:
        return False
    except OSError as exc:
        typer.echo(
            f"Failed to open private token output file: {exc.strerror}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    try:
        # The create mode is masked by the umask, so the owner-only mode is set
        # on the descriptor rather than trusted from the call.
        apply_private_file_mode(descriptor, output_secret)
    finally:
        os.close(descriptor)
    return True


def _release_secret_output_claim(output_secret: Path, *, rotate: bool) -> None:
    """Drop this run's claim so a failed run can be retried on the same path."""
    if rotate:
        return
    try:
        output_secret.unlink(missing_ok=True)
    except OSError:
        typer.echo(
            f"Remove the unused secret file yourself: {output_secret}",
            err=True,
        )


def _intake_setup_receiver_url_or_exit(url: str | None) -> str | None:
    """The endpoint the printed smoke check should use, or a refusal.

    ``None`` becomes a printed placeholder rather than a built-in endpoint: a
    receiver on another host, port or TLS setup would otherwise be smoke-tested
    on the wrong port, against a process that is not the receiver at all.
    """
    if url is None:
        return None
    if url.strip() == "":
        typer.echo(INTAKE_SETUP_URL_MESSAGE, err=True)
        raise typer.Exit(code=1)
    return url.strip()


def _intake_setup_after_claim(  # noqa: PLR0913 -- one identity per setup step.
    db: Path,
    *,
    owner_id: str,
    producer_id: str,
    writer_bundle_id: str,
    label: str,
    output_secret: Path,
    replaced_prefix: str | None,
    url: str | None,
    extra_start_options: tuple[str, ...],
) -> dict[str, object]:
    """The database and secret-file half of setup, run once the path is claimed."""
    record, created = _register_intake_producer_or_exit(
        db,
        owner_id=owner_id,
        producer_id=producer_id,
        writer_bundle_id=writer_bundle_id,
        label=label,
    )

    issued = _issue_intake_token_or_exit(
        db,
        owner_id=owner_id,
        producer_id=producer_id,
        label=label,
        pending=True,
    )
    written = _write_and_activate_intake_token(
        db,
        issued=issued,
        owner_id=owner_id,
        producer_id=producer_id,
        output_secret=output_secret,
        secret_payload={
            "label": issued.label,
            "owner_id": owner_id,
            "producer_id": producer_id,
            "token": issued.token,
            "token_prefix": issued.token_prefix,
            "warning": (
                "Store this token now; it is shown once and only a hash is kept "
                "locally."
            ),
        },
    )
    revoked_prefix = _revoke_replaced_intake_token(db, replaced_prefix)

    return {
        **_intake_producer_payload(record),
        "secret_file": written["secret_file"],
        "status": "registered" if created else "already-registered",
        "token_prefix": written["token_prefix"],
        # True only when a previous token was actually retired, so it is
        # never reported as a rotation that replaced nothing.
        "rotated": revoked_prefix is not None,
        "revoked_token_prefix": revoked_prefix,
        "next_steps": _intake_setup_next_steps(
            db,
            output_secret,
            url=url,
            start_options=extra_start_options,
        ),
    }


def _register_intake_producer_or_exit(
    db: Path,
    *,
    owner_id: str,
    producer_id: str,
    writer_bundle_id: str,
    label: str,
) -> tuple[ProducerRecord, bool]:
    """The `intake-register-producer` path, shared with `intake-setup`."""
    try:
        return register_intake_producer(
            db,
            owner_id=owner_id,
            producer_id=producer_id,
            writer_bundle_id=writer_bundle_id,
            display_label=label,
        )
    except IntakeProducerRevokedError as exc:
        typer.echo(
            PRODUCER_REVOKED_MESSAGE.format(producer_id=producer_id),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except IntakeProducerConflictError as exc:
        typer.echo(
            _intake_producer_conflict_message(
                db,
                owner_id=owner_id,
                producer_id=producer_id,
            ),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except (sqlite3.Error, OSError) as exc:
        typer.echo(INTAKE_STORAGE_UNAVAILABLE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc


def _issue_intake_token_or_exit(
    db: Path,
    *,
    owner_id: str,
    producer_id: str,
    label: str,
    pending: bool,
) -> IssuedIntakeToken:
    """The `intake-create-token` issuance, shared with `intake-setup`.

    ``pending`` selects the state the row is inserted in: a token written to a
    private file is inserted pending and activated only once the secret is on
    disk, while a token the operator asked to print is active immediately because
    there is no file to wait for.
    """
    issue_token = (
        create_pending_intake_token
        if pending
        else create_intake_token_for_active_producer
    )
    try:
        return issue_token(
            db,
            owner_id=owner_id,
            producer_id=producer_id,
            label=label,
        )
    except IntakeProducerInactiveError as exc:
        typer.echo(
            INTAKE_PRODUCER_NOT_ACTIVE_MESSAGE.format(producer_id=producer_id),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (sqlite3.Error, OSError) as exc:
        typer.echo(INTAKE_TOKEN_STORAGE_UNAVAILABLE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc


def _existing_secret_token_prefix(output_secret: Path) -> str | None:
    """The token prefix a rotation is about to replace, when the file names one.

    Only a prefix is read, never the token, and only to revoke the credential
    the file held. A file this command cannot parse simply has no prefix to
    revoke, which is reported rather than guessed at.
    """
    text = _existing_secret_text(output_secret)
    if text is None:
        return None
    document = _intake_json_object(text.encode("utf-8"))
    prefix = None if document is None else document.get("token_prefix")
    return prefix if isinstance(prefix, str) and prefix != "" else None


def _revoke_replaced_intake_token(db: Path, replaced_prefix: str | None) -> str | None:
    """Retire the token a rotated file held, reporting the prefix when it did.

    A revocation that fails is reported as a warning and leaves the prefix out of
    the result: the new token is already active and written, so failing the whole
    command would report a setup that did succeed as broken.
    """
    if replaced_prefix is None:
        return None
    revoke_failed_message = (
        "Could not revoke the intake token this file held ({prefix}); revoke it "
        "with receiver intake-revoke-token."
    )
    already_revoked_message = (
        "The intake token this file held ({prefix}) was already revoked; "
        "nothing was revoked."
    )
    try:
        revoked_count = revoke_active_intake_token(db, replaced_prefix)
    except (sqlite3.Error, OSError):
        typer.echo(revoke_failed_message.format(prefix=replaced_prefix), err=True)
        return None
    if revoked_count == 0:
        typer.echo(already_revoked_message.format(prefix=replaced_prefix), err=True)
        return None
    return replaced_prefix


def _intake_setup_next_steps(
    db: Path,
    output_secret: Path,
    *,
    url: str | None,
    start_options: tuple[str, ...],
) -> list[str]:
    """What is left to do by hand, in the order it has to happen.

    The routes are off until the receiver is restarted with the flag, so the
    smoke check is printed after the restart rather than as if it worked now.

    Every path is shell-quoted and the endpoint is the one this run was given,
    because these lines are meant to be pasted: an unquoted path with a space
    splits into two arguments, and a built-in endpoint would silently smoke-test
    the wrong port or the wrong process.
    """
    restart_command = " ".join(
        [
            "health-bridge receiver start",
            f"--db {shlex.quote(str(db))}",
            "--enable-intake-context",
            *(shlex.quote(option) for option in start_options),
        ],
    )
    restart_step = (
        f"Restart the receiver with the intake routes enabled: {restart_command}"
    )
    routes_off_step = (
        "Intake routes stay off until that flag is passed, so a receiver "
        "started without it answers 404 for them."
    )
    smoke_endpoint = url if url is not None else INTAKE_SETUP_URL_PLACEHOLDER
    smoke_command = " ".join(
        [
            "health-bridge receiver intake-smoke",
            f"--url {shlex.quote(smoke_endpoint)}",
            f"--token-file {shlex.quote(str(output_secret))}",
        ],
    )
    smoke_step = f"Check the receiver serves them: {smoke_command}"
    url_step = (
        f"These steps assume the receiver answers on {url}."
        if url is not None
        else (
            f"Replace {INTAKE_SETUP_URL_PLACEHOLDER} with the URL this receiver "
            "actually answers on, including its scheme, host and port, or re-run "
            "intake-setup with --url to have these steps print it for you."
        )
    )
    move_into_app_step = (
        "Move the token from {secret_file} into the app that uploads intake "
        "context, then delete the file. Never paste it into chat, Git, a wiki "
        "or a log."
    )
    return [
        restart_step,
        routes_off_step,
        smoke_step,
        url_step,
        move_into_app_step.format(secret_file=output_secret),
    ]


def _existing_secret_text(output_secret: Path) -> str | None:
    """The file this issuance is about to replace, so a failure can put it back."""
    try:
        return output_secret.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _restore_secret_file_or_warn(output_secret: Path, replaced: str | None) -> None:
    """Undo this issuance's write: restore the previous secret, or remove ours.

    The write replaces its destination atomically, so a failure after it would
    otherwise delete a credential file that was there before. A previous file we
    could not read back is left in place with a warning instead of being removed.
    """
    if replaced is None and _is_unreadable_regular_file(output_secret):
        typer.echo(
            f"Remove the unused secret file yourself: {output_secret}",
            err=True,
        )
        return
    try:
        if replaced is None:
            output_secret.unlink(missing_ok=True)
        else:
            write_private_text_file(output_secret, replaced)
    except OSError:
        typer.echo(
            f"Restore the previous secret file yourself: {output_secret}",
            err=True,
        )


def _is_unreadable_regular_file(path: Path) -> bool:
    """True when the path holds bytes this command cannot read back or rewrite."""
    try:
        if not path.is_file():
            return False
        _ = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return True
    return False


class _MissingIntakeTablesError(Exception):
    """An existing receiver database predates the intake-context migrations."""


def _read_intake_storage(db: Path, read: Callable[[sqlite3.Connection], None]) -> None:
    """Run a listing query against an EXISTING database, read-only.

    A listing command must never create, migrate or otherwise write the store it
    inspects, so it opens the existing file through the same read-only path the
    MCP tools use and never calls ``initialize_database``. A missing file and a
    database without the intake tables are reported, not repaired.
    """
    try:
        with connect_readonly_database(db) as connection:
            read(connection)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise _MissingIntakeTablesError from exc
        raise


def _read_intake_producers(db: Path) -> tuple[ProducerRecord, ...]:
    records: list[ProducerRecord] = []

    def _read(connection: sqlite3.Connection) -> None:
        records.extend(list_producers(connection))

    _read_intake_storage(db, _read)
    return tuple(records)


def _read_intake_tokens(db: Path) -> tuple[IntakeTokenRecord, ...]:
    tokens: list[IntakeTokenRecord] = []

    def _read(connection: sqlite3.Connection) -> None:
        tokens.extend(list_intake_tokens_on(connection))

    _read_intake_storage(db, _read)
    return tuple(tokens)


def _intake_producer_payload(record: ProducerRecord) -> dict[str, object]:
    return {
        "owner_id": record.owner_id,
        "producer_id": record.producer_id,
        "writer_bundle_id": record.writer_bundle_id,
        "label": record.display_label,
        "registered_at": record.registered_at,
        "revoked_at": record.revoked_at,
    }


def _intake_producer_conflict_message(
    db: Path,
    *,
    owner_id: str,
    producer_id: str,
) -> str:
    """Name the stored writer bundle so the operator can see what differs."""
    stored = ""
    try:
        initialize_database(db)
        with connect_database(db) as connection:
            record = read_producer(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
            )
        if record is not None:
            stored = STORED_WRITER_BUNDLE_MESSAGE.format(
                writer_bundle_id=record.writer_bundle_id,
            )
    except (sqlite3.Error, OSError):
        stored = ""
    return (
        INTAKE_PRODUCER_CONFLICT_MESSAGE.format(
            producer_id=producer_id,
        )
        + stored
    )


def _utc_now() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _intake_producer_input_error(
    *,
    owner_id: str,
    producer_id: str,
    writer_bundle_id: str,
    label: str,
) -> str | None:
    """The one-line reason these producer details are unusable, if any."""
    if owner_id.strip() == "":
        return "Refusing to register a producer with an empty --owner-id."
    if re.match(PRODUCER_ID_PATTERN, producer_id) is None:
        return "Refusing to register a producer with an invalid --producer-id."
    if re.match(BUNDLE_ID_PATTERN, writer_bundle_id) is None:
        return "Refusing to register a producer with an invalid --writer-bundle-id."
    if label.strip() == "":
        return "Refusing to register a producer with an empty --label."
    return None


def register_intake_producer(
    db: Path,
    *,
    owner_id: str,
    producer_id: str,
    writer_bundle_id: str,
    display_label: str,
) -> tuple[ProducerRecord, bool]:
    """Register a producer, or return the identical registration already stored.

    ``register_producer`` compares the whole record, including ``registered_at``,
    so a fresh timestamp would make an identical re-registration look like a
    conflict, and a producer restored from an earlier backup would always conflict
    with itself. Only the identity that actually matters is compared instead --
    owner, producer, writer bundle and display label -- so an unchanged producer
    is idempotent whatever its stored ``registered_at`` says. The read runs inside
    the same immediate transaction as the insert, so a racing registration is
    either seen and returned or serialised behind the insert.
    """
    initialize_database(db)
    with connect_database(db) as connection:
        _ = connection.execute("begin immediate")
        try:
            outcome = _register_producer_in_transaction(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
                writer_bundle_id=writer_bundle_id,
                display_label=display_label,
            )
        except BaseException:
            connection.rollback()
            raise
        connection.commit()
        return outcome


def _register_producer_in_transaction(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
    writer_bundle_id: str,
    display_label: str,
) -> tuple[ProducerRecord, bool]:
    existing = read_producer(
        connection,
        owner_id=owner_id,
        producer_id=producer_id,
    )
    if existing is not None:
        if (
            existing.owner_id == owner_id
            and existing.producer_id == producer_id
            and existing.writer_bundle_id == writer_bundle_id
            and existing.display_label == display_label
        ):
            if existing.revoked_at is not None:
                # Reporting success here would be a dead end: the producer stays
                # revoked, so every later token is refused with no hint why.
                raise IntakeProducerRevokedError(producer_id)
            return existing, False
        raise IntakeProducerConflictError(producer_id)
    record = register_producer(
        connection,
        ProducerRecord(
            owner_id=owner_id,
            producer_id=producer_id,
            writer_bundle_id=writer_bundle_id,
            display_label=display_label,
            registered_at=_utc_now(),
        ),
    )
    return record, True


class IntakeProducerUnavailableError(Exception):
    """No producer row matches the requested owner and producer."""


def revoke_intake_producer(
    db: Path,
    *,
    owner_id: str,
    producer_id: str,
) -> tuple[ProducerRecord, int]:
    """Revoke a producer and every active token it owns in one transaction.

    The producer row and its tokens move together, so a producer can never be
    left active with live credentials. Revoking twice, or naming a producer that
    was never registered, raises ``IntakeProducerUnavailableError`` so the CLI
    can fail instead of silently claiming success.
    """
    initialize_database(db)
    with connect_database(db) as connection:
        _ = connection.execute("begin immediate")
        try:
            record = _active_producer_or_unavailable(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
            )
            revoked_at = _utc_now()
            _ = revoke_producer(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
                revoked_at=revoked_at,
            )
            revoked_token_count = revoke_producer_tokens(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
            )
        except BaseException:
            connection.rollback()
            raise
        connection.commit()
    return replace(record, revoked_at=revoked_at), revoked_token_count


def reactivate_intake_producer(
    db: Path,
    *,
    owner_id: str,
    producer_id: str,
) -> ProducerRecord:
    """Clear a producer's ``revoked_at`` without reviving any of its tokens."""
    initialize_database(db)
    with connect_database(db) as connection:
        _ = connection.execute("begin immediate")
        try:
            record = _revoked_producer_or_unavailable(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
            )
            _ = reactivate_producer(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
            )
        except BaseException:
            connection.rollback()
            raise
        connection.commit()
    return replace(record, revoked_at=None)


def _producer_or_unavailable(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
) -> ProducerRecord:
    record = read_producer(
        connection,
        owner_id=owner_id,
        producer_id=producer_id,
    )
    if record is None:
        raise IntakeProducerUnavailableError(producer_id)
    return record


def _active_producer_or_unavailable(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
) -> ProducerRecord:
    record = _producer_or_unavailable(
        connection,
        owner_id=owner_id,
        producer_id=producer_id,
    )
    if record.revoked_at is not None:
        raise IntakeProducerUnavailableError(producer_id)
    return record


def _revoked_producer_or_unavailable(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
) -> ProducerRecord:
    record = _producer_or_unavailable(
        connection,
        owner_id=owner_id,
        producer_id=producer_id,
    )
    if record.revoked_at is None:
        raise IntakeProducerUnavailableError(producer_id)
    return record


@receiver_app.command("start")
def start(  # noqa: PLR0913 -- Typer exposes independent receiver options.
    db: Annotated[
        Path | None,
        typer.Option("--db", help="User-owned SQLite database path."),
    ] = None,
    host: Annotated[
        str,
        typer.Option("--host", help="Bind host. Keep 127.0.0.1 for local-only."),
    ] = DEFAULT_RECEIVER_HOST,
    port: Annotated[
        int,
        typer.Option("--port", help="Bind port."),
    ] = DEFAULT_RECEIVER_PORT,
    mailbox_root: Annotated[
        Path | None,
        typer.Option(
            "--mailbox-root",
            help="Enable validated macOS iCloud mailbox pairing for this receiver.",
        ),
    ] = None,
    icloud_container_identifier: Annotated[
        str | None,
        typer.Option(
            "--icloud-container-identifier",
            help="Expected iCloud container identifier for the mailbox root.",
        ),
    ] = None,
    service_config: Annotated[
        Path | None,
        typer.Option(
            "--service-config",
            help="Owner-only mailbox LaunchAgent receiver configuration.",
        ),
    ] = None,
    enable_intake_context: Annotated[
        bool,
        typer.Option(
            "--enable-intake-context",
            help="Enable intake-context HTTP routes for this receiver process.",
        ),
    ] = False,
    request_timeout: Annotated[
        float,
        typer.Option(
            "--request-timeout",
            help=(
                "Seconds a single socket read may block before the receiver "
                "gives up on a stalled request. This is an inactivity timeout, "
                "so a client that keeps sending bytes resets it. "
                "Greater than 0 and at most 300."
            ),
        ),
    ] = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> None:
    _validate_request_timeout(request_timeout)
    run_receiver_start(
        ReceiverStartOptions(
            db=db,
            host=host,
            port=port,
            mailbox_root=mailbox_root,
            icloud_container_identifier=icloud_container_identifier,
            service_config=service_config,
            intake_context_enabled=enable_intake_context,
            request_timeout_seconds=request_timeout,
        ),
        ReceiverStartDependencies(
            load_service_config=load_runnable_launch_agent_request,
            select_transport=_select_transport_or_exit,
            create_key_store=MailboxKeyStore.production,
            create_connection_store=MailboxConnectionStore.production,
            serve=serve_receiver,
        ),
    )


@receiver_app.command("smoke")
def smoke(
    input_path: Annotated[
        Path,
        typer.Option("--input", help="health_bridge.batch.v1 JSON payload."),
    ],
    token: Annotated[
        str,
        typer.Option("--token", help="Receiver bearer token."),
    ],
    url: Annotated[
        str,
        typer.Option("--url", help="Receiver /v1/batches endpoint."),
    ] = "http://127.0.0.1:8765/v1/batches",
) -> None:
    _validate_receiver_url(url)
    request = Request(  # noqa: S310 - URL scheme is validated above.
        url,
        data=input_path.read_bytes(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        # `_validate_receiver_url` above restricts this request to HTTP(S).
        with cast(
            "HTTPResponse",
            urlopen(request, timeout=10),  # noqa: S310  # nosec B310
        ) as response:
            body = SMOKE_RESPONSE_ADAPTER.validate_json(response.read())
            status = response.status
    except HTTPError as exc:
        typer.echo(f"Receiver smoke failed: HTTP {exc.code}.", err=True)
        raise typer.Exit(code=1) from exc
    except URLError as exc:
        typer.echo("Receiver smoke failed: receiver was not reachable.", err=True)
        raise typer.Exit(code=1) from exc
    body["http_status"] = status
    typer.echo(json.dumps(body, sort_keys=True))


@receiver_app.command("intake-smoke")
def intake_smoke(
    token_file: Annotated[
        Path,
        typer.Option(
            "--token-file",
            help="Private intake token JSON file written by intake-create-token.",
        ),
    ],
    url: Annotated[
        str,
        typer.Option(
            "--url",
            help="Receiver base URL, e.g. http://127.0.0.1:8765.",
        ),
    ] = "http://127.0.0.1:8765",
) -> None:
    _validate_intake_smoke_url(url)
    token = _read_intake_smoke_token(token_file)
    request = Request(  # noqa: S310 -- URL scheme is validated above.
        _intake_capabilities_url(url),
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        # `_validate_intake_smoke_url` above restricts this request to HTTP(S),
        # and the opener refuses redirects so the bearer token cannot be
        # forwarded to another host or downgraded from https to http.
        with cast(
            "HTTPResponse",
            _intake_smoke_opener().open(
                request,
                timeout=INTAKE_SMOKE_TIMEOUT_SECONDS,
            ),
        ) as response:
            status = response.status
            body = _read_intake_capabilities_body(response)
    except HTTPError as exc:
        # The error carries the open response; close it so the socket is released.
        with suppress(OSError):
            exc.close()
        _echo_intake_smoke_failure(exc.code)
        raise typer.Exit(code=1) from exc
    except (URLError, HTTPException, TimeoutError, OSError) as exc:
        # A read that stalls after the headers raise TimeoutError directly, and
        # a connection that hangs up mid-body raises IncompleteRead, which is an
        # HTTPException rather than an OSError.
        typer.echo(INTAKE_SMOKE_UNREACHABLE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc

    # The token is never echoed; only the status and the capability fields are.
    typer.echo(
        json.dumps(
            {
                "http_status": status,
                **_intake_capabilities_fields(body),
            },
            sort_keys=True,
        )
    )
    if status != INTAKE_SMOKE_OK_STATUS:
        _echo_intake_smoke_failure(status)
        raise typer.Exit(code=1)
    unusable = _intake_capabilities_unusable_reason(body)
    if unusable is not None:
        typer.echo(
            INTAKE_SMOKE_UNUSABLE_CAPABILITIES_MESSAGE.format(reason=unusable), err=True
        )
        raise typer.Exit(code=1)


def _intake_smoke_opener() -> OpenerDirector:
    """An opener that refuses redirects instead of replaying the bearer token.

    ``urllib`` copies the original headers onto every redirect it follows, so an
    automatic redirect would hand the intake token to whatever host the response
    names, including an https-to-http downgrade.
    """

    class _RefuseRedirect(HTTPRedirectHandler):
        @override
        def redirect_request(self, *_args: object, **_kwargs: object) -> None:
            """Refuse the redirect: urllib reads this handler's ``None`` as no."""

    # No ProxyHandler mapping means no proxy: the bearer token never goes to a proxy
    # named in HTTP_PROXY/HTTPS_PROXY, only to the receiver the user configured.
    return build_opener(ProxyHandler({}), _RefuseRedirect)


def _intake_capabilities_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}{INTAKE_CONTEXT_CAPABILITIES_PATH}"


def _read_intake_capabilities_body(response: HTTPResponse) -> bytes:
    """Read at most a capabilities document, and refuse anything larger.

    The socket timeout only bounds silence, so a peer that keeps sending bytes
    would keep this read going while buffering all of them. The advertised
    length is checked first when the receiver provides one, and the read itself
    is capped, so an oversized or endless response fails instead of filling
    memory.
    """
    declared = response.headers.get("Content-Length")
    if (
        declared is not None
        and declared.strip().isdigit()
        and int(declared) > INTAKE_SMOKE_MAX_BODY_BYTES
    ):
        typer.echo(INTAKE_SMOKE_BODY_TOO_LARGE_MESSAGE, err=True)
        raise typer.Exit(code=1)
    body = response.read(INTAKE_SMOKE_MAX_BODY_BYTES + 1)
    if len(body) > INTAKE_SMOKE_MAX_BODY_BYTES:
        typer.echo(INTAKE_SMOKE_BODY_TOO_LARGE_MESSAGE, err=True)
        raise typer.Exit(code=1)
    return body


def _intake_capabilities_fields(body: bytes) -> dict[str, object]:
    """The capability fields worth showing, with anything unexpected dropped."""
    document = _intake_json_object(body)
    if document is None:
        return {}
    return {
        key: value for key, value in document.items() if key in INTAKE_CAPABILITY_FIELDS
    }


def _intake_capabilities_unusable_reason(body: bytes) -> str | None:
    """Why a 200 response cannot be trusted as an intake receiver, or nothing.

    A wrong service, a reverse-proxy fallback page or an incompatible receiver
    can answer this path with 200. Status alone would call that a passing smoke
    check even though intake uploads cannot use the endpoint, so the document has
    to name the intake schema, offer a version this CLI speaks, and carry the
    capability fields an uploader reads.
    """
    document = _intake_json_object(body)
    if document is None:
        return "the response is not a JSON object"
    if document.get("schema") != INTAKE_SCHEMA_NAME:
        return f"schema is {document.get('schema')!r}, not {INTAKE_SCHEMA_NAME!r}"
    versions = document.get("supported_versions")
    if not isinstance(versions, list) or INTAKE_SCHEMA_VERSION not in versions:
        return f"supported versions are {versions!r}, without {INTAKE_SCHEMA_VERSION!r}"
    missing = sorted(REQUIRED_INTAKE_CAPABILITY_FIELDS - document.keys())
    if missing:
        return f"capability fields are missing: {', '.join(missing)}"
    # Presence is not enough: an uploader reads these values, so a null or
    # wrongly typed one means the endpoint cannot be used even at HTTP 200.
    invalid = _invalid_intake_capability_reason(document)
    if invalid is not None:
        return invalid
    return None


def _invalid_intake_capability_reason(document: Mapping[str, object]) -> str | None:
    """The first capability value an uploader could not use, or nothing.

    ``authentication`` must describe the bearer header to send, ``features``
    must be a list of feature names, and both limits must be positive whole
    numbers a client can compare against.
    """
    reasons = (
        _invalid_authentication_reason(document.get("authentication")),
        _invalid_features_reason(document.get("features")),
        _invalid_capability_limit_reason(document, "max_body_bytes"),
        _invalid_capability_limit_reason(document, "max_operations"),
    )
    return next((reason for reason in reasons if reason is not None), None)


def _invalid_authentication_reason(authentication: object) -> str | None:
    """Why the advertised authentication cannot be used, or nothing."""
    if not isinstance(authentication, dict):
        return f"authentication is {authentication!r}, not an object"
    described = cast("Mapping[str, object]", authentication)
    for key in ("scheme", "header"):
        value = described.get(key)
        if not isinstance(value, str) or value.strip() == "":
            return f"authentication.{key} is {value!r}"
    return None


def _invalid_features_reason(features: object) -> str | None:
    """Why the advertised feature list cannot be used, or nothing."""
    if not isinstance(features, list):
        return f"features is {features!r}, not a list of names"
    named = cast("list[object]", features)
    if any(not isinstance(feature, str) or feature.strip() == "" for feature in named):
        return f"features is {features!r}, not a list of names"
    missing = [name for name in INTAKE_SMOKE_REQUIRED_FEATURES if name not in named]
    if missing:
        return f"features lacks {', '.join(missing)}"
    return None


def _invalid_capability_limit_reason(
    document: Mapping[str, object],
    field: str,
) -> str | None:
    """Why a numeric capability limit cannot be used, or nothing."""
    limit = document.get(field)
    if isinstance(limit, bool) or not isinstance(limit, int):
        return f"{field} is {limit!r}, not a whole number"
    if limit <= 0:
        return f"{field} is {limit!r}, not a positive limit"
    return None


def _intake_json_object(body: bytes) -> dict[str, object] | None:
    """Parsed JSON when it is an object, otherwise nothing to report."""
    try:
        document = cast("object", json.loads(body))
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict):
        return None
    return INTAKE_JSON_OBJECT_ADAPTER.validate_python(document)


def _echo_intake_smoke_failure(status: int) -> None:
    if status == INTAKE_SMOKE_NOT_FOUND_STATUS:
        typer.echo(INTAKE_ROUTES_DISABLED_MESSAGE, err=True)
        return
    typer.echo(f"Receiver intake smoke failed: HTTP {status}.", err=True)


def _read_intake_smoke_token(token_file: Path) -> str:
    """The bearer token from an `intake-create-token` file, never printed."""
    try:
        text = token_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # A truncated or wrongly encoded file is a user error, not a crash.
        typer.echo(INTAKE_SMOKE_TOKEN_FILE_MESSAGE, err=True)
        raise typer.Exit(code=1) from exc
    document = _intake_json_object(text.encode("utf-8"))
    token = None if document is None else document.get("token")
    # The whole generated format, not just the prefix: a corrupted or edited file
    # can hold a newline, a carriage return or a non-Latin-1 character, and
    # urllib raises while building or sending the Authorization header for any of
    # those rather than failing the request cleanly.
    if not isinstance(token, str) or INTAKE_TOKEN_PATTERN.fullmatch(token) is None:
        typer.echo(INTAKE_SMOKE_TOKEN_FILE_MESSAGE, err=True)
        raise typer.Exit(code=1)
    return token


def _validate_intake_smoke_url(url: str) -> None:
    """Accept only an http(s) URL urllib can actually open.

    Parsing is defensive because the malformed inputs a user can type raise from
    different places: ``urlparse`` itself rejects an unmatched IPv6 bracket,
    ``.port`` rejects a non-numeric or out-of-range port, and the request would
    otherwise fail later with a host that is empty, holds whitespace, or cannot
    be encoded for a header.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        _ = parsed.port
    except ValueError:
        typer.echo(INTAKE_SMOKE_URL_MESSAGE, err=True)
        raise typer.Exit(code=1) from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or host is None
        or host.strip() == ""
        or not INTAKE_SMOKE_HOST_PATTERN.fullmatch(host)
    ):
        typer.echo(INTAKE_SMOKE_URL_MESSAGE, err=True)
        raise typer.Exit(code=1)


def _validate_receiver_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        typer.echo("Receiver smoke failed: URL must use http or https.", err=True)
        raise typer.Exit(code=1)
