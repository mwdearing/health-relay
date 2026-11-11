from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Final, Literal

from health_bridge.launchd.models import (
    MAX_TCP_PORT,
    LaunchdServiceCode,
    LaunchdServiceError,
    LaunchdServiceErrorCode,
    LaunchdServiceRequest,
    LaunchdServiceResult,
)
from health_bridge.private_files import require_private_parent

_INVALID: Final = LaunchdServiceErrorCode.INVALID_CONFIGURATION
_CONTROL_CHARACTERS: Final = frozenset(chr(value) for value in (*range(32), 127))


def validate_launch_agent(request: LaunchdServiceRequest) -> LaunchdServiceResult:
    _ = validate_launch_agent_structure(request)
    _validate_existing_path(
        request.executable,
        field="executable",
        kind="executable",
        allow_root_owner=True,
        owner_only=False,
    )
    _validate_existing_path(
        request.db_path,
        field="db",
        kind="file",
        allow_root_owner=False,
        owner_only=True,
    )
    _validate_existing_path(
        request.mailbox_root,
        field="mailbox_root",
        kind="directory",
        allow_root_owner=False,
        owner_only=True,
    )
    return LaunchdServiceResult(code=LaunchdServiceCode.VALID)


def validate_launch_agent_structure(
    request: LaunchdServiceRequest,
) -> LaunchdServiceResult:
    _validate_request_values(request)
    _validate_existing_path(
        request.home,
        field="home",
        kind="directory",
        allow_root_owner=False,
        owner_only=False,
    )
    _validate_expected_mailbox_topology(request)
    _validate_artifact_topology(request)
    return LaunchdServiceResult(code=LaunchdServiceCode.VALID)


def _error(
    code: LaunchdServiceErrorCode, field: str, reason: str
) -> LaunchdServiceError:
    return LaunchdServiceError(code, field=field, reason=reason)


def _validate_request_values(request: LaunchdServiceRequest) -> None:
    if request.uid < 0 or request.uid != os.geteuid():
        raise _error(_INVALID, "uid", "not_current_user")
    if request.host != "127.0.0.1":
        raise _error(_INVALID, "host", "not_loopback")
    if not 1 <= request.port <= MAX_TCP_PORT:
        raise _error(_INVALID, "port", "out_of_range")
    for field, value in (
        ("executable", str(request.executable)),
        ("db", str(request.db_path)),
        ("mailbox_root", str(request.mailbox_root)),
        ("home", str(request.home)),
        ("icloud_container_identifier", request.icloud_container_identifier),
    ):
        if not value or any(character in _CONTROL_CHARACTERS for character in value):
            raise _error(_INVALID, field, "empty_or_control_characters")
    for field, path in (
        ("executable", request.executable),
        ("db", request.db_path),
        ("mailbox_root", request.mailbox_root),
        ("home", request.home),
    ):
        if not path.is_absolute():
            raise _error(_INVALID, field, "not_absolute")


def _validate_existing_path(
    path: Path,
    *,
    field: str,
    kind: Literal["directory", "executable", "file"],
    allow_root_owner: bool,
    owner_only: bool,
) -> None:
    try:
        resolved = path.resolve(strict=True)
        entry = path.lstat()
        require_private_parent(path.parent)
    except PermissionError as exc:
        raise _error(
            LaunchdServiceErrorCode.UNSAFE_PERMISSIONS, field, "not_accessible"
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise _error(
            LaunchdServiceErrorCode.UNSAFE_FILESYSTEM, field, "missing_or_unsafe_parent"
        ) from exc
    if stat.S_ISLNK(entry.st_mode):
        raise _error(LaunchdServiceErrorCode.UNSAFE_FILESYSTEM, field, "symlink")
    if path != resolved:
        raise _error(
            LaunchdServiceErrorCode.UNSAFE_FILESYSTEM, field, "symlink_in_path"
        )
    expected_owner = {os.geteuid(), 0} if allow_root_owner else {os.geteuid()}
    prohibited_mode = 0o077 if owner_only else 0o022
    if entry.st_uid not in expected_owner:
        raise _error(LaunchdServiceErrorCode.UNSAFE_PERMISSIONS, field, "wrong_owner")
    if bool(entry.st_mode & prohibited_mode):
        raise _error(
            LaunchdServiceErrorCode.UNSAFE_PERMISSIONS, field, "group_or_world_access"
        )
    if kind == "directory" and not stat.S_ISDIR(entry.st_mode):
        raise _error(LaunchdServiceErrorCode.UNSAFE_FILESYSTEM, field, "not_directory")
    if kind in {"file", "executable"} and not stat.S_ISREG(entry.st_mode):
        raise _error(
            LaunchdServiceErrorCode.UNSAFE_FILESYSTEM, field, "not_regular_file"
        )
    if kind == "executable" and not bool(entry.st_mode & 0o111):
        raise _error(
            LaunchdServiceErrorCode.UNSAFE_PERMISSIONS, field, "not_executable"
        )


def _validate_expected_mailbox_topology(request: LaunchdServiceRequest) -> None:
    component = request.icloud_container_identifier.replace(".", "~")
    if Path(component).name != component or component in {"", ".", ".."}:
        raise _error(_INVALID, "icloud_container_identifier", "invalid_component")
    expected = (
        request.home
        / "Library/Mobile Documents"
        / component
        / "Documents/HealthBridgeMailbox/v1"
    )
    if request.mailbox_root != expected:
        raise _error(_INVALID, "mailbox_root", "unexpected_location")


def _validate_artifact_topology(request: LaunchdServiceRequest) -> None:
    for path, owner_only in (
        (request.paths.state_dir, True),
        (request.paths.manifest.parent, False),
    ):
        if not path.exists():
            continue
        try:
            entry = path.lstat()
        except OSError as exc:
            raise _error(
                LaunchdServiceErrorCode.UNSAFE_FILESYSTEM,
                "service_artifacts",
                "unreadable",
            ) from exc
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
            raise _error(
                LaunchdServiceErrorCode.UNSAFE_FILESYSTEM,
                "service_artifacts",
                "not_plain_directory",
            )
        prohibited_mode = 0o077 if owner_only else 0o022
        if entry.st_uid != os.geteuid() or bool(entry.st_mode & prohibited_mode):
            raise _error(
                LaunchdServiceErrorCode.UNSAFE_PERMISSIONS,
                "service_artifacts",
                "wrong_owner_or_mode",
            )
