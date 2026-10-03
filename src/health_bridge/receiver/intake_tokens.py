"""Intake-only bearer tokens: a separate store and prefix from batch tokens."""

import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, cast

from pydantic import TypeAdapter

from health_bridge.contract.intake_context_v1 import PRODUCER_ID_PATTERN
from health_bridge.receiver.tokens import hash_receiver_token
from health_bridge.storage.database import connect_database, initialize_database

INTAKE_TOKEN_PREFIX: Final = "hri_"  # noqa: S105 - public token prefix, not a secret.
INTAKE_TOKEN_PREFIX_LENGTH: Final = 12
GENERATED_TOKEN_BYTES: Final = 32


def _sql(*parts: str) -> str:
    return " ".join(parts)


INSERT_INTAKE_TOKEN_SQL: Final = _sql(
    "insert into intake_context_tokens",
    "(owner_id, producer_id, label, token_hash, token_prefix)",
    "values (?, ?, ?, ?, ?)",
)
SELECT_ACTIVE_INTAKE_TOKENS_SQL: Final = _sql(
    "select token_hash, owner_id, producer_id from intake_context_tokens",
    "where token_prefix = ? and revoked_at is null",
)
REVOKE_INTAKE_TOKEN_SQL: Final = _sql(
    "update intake_context_tokens",
    "set revoked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
    "where token_prefix = ? and revoked_at is null",
)
SELECT_INTAKE_TOKENS_SQL: Final = _sql(
    "select token_prefix, owner_id, producer_id, label, created_at, revoked_at",
    "from intake_context_tokens",
    "order by intake_token_id",
)
IntakeTokenRow: TypeAlias = tuple[str, str, str, str, str, str | None]
INTAKE_TOKEN_ROWS_ADAPTER: Final[TypeAdapter[list[IntakeTokenRow]]] = TypeAdapter(
    list[IntakeTokenRow]
)


@dataclass(frozen=True)
class IntakeTokenRecord:
    """One intake token row without the token itself or its hash."""

    token_prefix: str
    owner_id: str
    producer_id: str
    label: str
    created_at: str
    revoked_at: str | None


@dataclass(frozen=True)
class IssuedIntakeToken:
    token_id: int
    label: str
    token: str
    token_prefix: str


@dataclass(frozen=True)
class IntakeTokenPrincipal:
    owner_id: str
    producer_id: str


def create_intake_token(
    db_path: Path,
    *,
    owner_id: str,
    producer_id: str,
    label: str,
) -> IssuedIntakeToken:
    _validate_identity(owner_id=owner_id, producer_id=producer_id, label=label)
    initialize_database(db_path)
    token = f"{INTAKE_TOKEN_PREFIX}{secrets.token_urlsafe(GENERATED_TOKEN_BYTES)}"
    token_prefix = token[:INTAKE_TOKEN_PREFIX_LENGTH]
    with connect_database(db_path) as connection:
        cursor = connection.execute(
            INSERT_INTAKE_TOKEN_SQL,
            (owner_id, producer_id, label, hash_receiver_token(token), token_prefix),
        )
        token_id = cursor.lastrowid
    if token_id is None:
        message = "Intake token insert did not return a row id."
        raise sqlite3.IntegrityError(message)
    return IssuedIntakeToken(
        token_id=token_id,
        label=label,
        token=token,
        token_prefix=token_prefix,
    )


def _validate_identity(*, owner_id: str, producer_id: str, label: str) -> None:
    if owner_id.strip() == "":
        message = "invalid owner_id"
        raise ValueError(message)
    if re.match(PRODUCER_ID_PATTERN, producer_id) is None:
        message = "invalid producer_id"
        raise ValueError(message)
    if label.strip() == "":
        message = "invalid label"
        raise ValueError(message)


def revoke_intake_token(db_path: Path, token_prefix: str) -> None:
    initialize_database(db_path)
    with connect_database(db_path) as connection:
        _ = connection.execute(REVOKE_INTAKE_TOKEN_SQL, (token_prefix,))


def revoke_active_intake_token(db_path: Path, token_prefix: str) -> int:
    """Revoke every active token with this prefix and report how many changed.

    ``revoke_intake_token`` stays the fire-and-forget form used elsewhere. A
    caller that must fail when the prefix matched nothing needs the affected row
    count, which an already-revoked or unknown prefix reports as zero.
    """
    initialize_database(db_path)
    with connect_database(db_path) as connection:
        return int(
            connection.execute(
                REVOKE_INTAKE_TOKEN_SQL,
                (token_prefix,),
            ).rowcount
        )


def list_intake_tokens(db_path: Path) -> tuple[IntakeTokenRecord, ...]:
    """Every intake token row, oldest first, never the token or its hash."""
    initialize_database(db_path)
    with connect_database(db_path) as connection:
        rows = INTAKE_TOKEN_ROWS_ADAPTER.validate_python(
            connection.execute(SELECT_INTAKE_TOKENS_SQL).fetchall(),
        )
    return tuple(
        IntakeTokenRecord(
            token_prefix=row[0],
            owner_id=row[1],
            producer_id=row[2],
            label=row[3],
            created_at=row[4],
            revoked_at=row[5],
        )
        for row in rows
    )


def authenticate_intake_token(
    db_path: Path,
    token: str,
) -> IntakeTokenPrincipal | None:
    if not token.startswith(INTAKE_TOKEN_PREFIX):
        return None
    initialize_database(db_path)
    token_hash = hash_receiver_token(token)
    with connect_database(db_path) as connection:
        rows = connection.execute(
            SELECT_ACTIVE_INTAKE_TOKENS_SQL,
            (token[:INTAKE_TOKEN_PREFIX_LENGTH],),
        ).fetchall()
    found: IntakeTokenPrincipal | None = None
    for row in cast("list[tuple[str, str, str]]", rows):
        stored_hash, owner_id, producer_id = row
        if hmac.compare_digest(stored_hash, token_hash):
            found = IntakeTokenPrincipal(owner_id=owner_id, producer_id=producer_id)
    return found
