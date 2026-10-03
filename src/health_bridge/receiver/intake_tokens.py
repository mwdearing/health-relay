"""Intake-only bearer tokens: a separate store and prefix from batch tokens."""

import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
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
    "(owner_id, producer_id, label, token_hash, token_prefix, revoked_at)",
    "values (?, ?, ?, ?, ?, ?)",
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
ACTIVATE_INTAKE_TOKEN_SQL: Final = _sql(
    "update intake_context_tokens",
    "set revoked_at = null",
    "where token_prefix = ? and revoked_at is not null",
)
DELETE_INTAKE_TOKEN_SQL: Final = _sql(
    "delete from intake_context_tokens",
    "where token_prefix = ? and revoked_at is not null",
)
SELECT_INTAKE_TOKENS_SQL: Final = _sql(
    "select token_prefix, owner_id, producer_id, label, created_at, revoked_at",
    "from intake_context_tokens",
    "order by intake_token_id",
)
SELECT_ACTIVE_PRODUCER_SQL: Final = _sql(
    "select revoked_at from intake_producers",
    "where owner_id = ? and producer_id = ?",
)
IntakeTokenRow: TypeAlias = tuple[str, str, str, str, str, str | None]
INTAKE_TOKEN_ROWS_ADAPTER: Final[TypeAdapter[list[IntakeTokenRow]]] = TypeAdapter(
    list[IntakeTokenRow]
)
ProducerRevokedRow: TypeAlias = tuple[str | None] | None
PRODUCER_REVOKED_ROW_ADAPTER: Final[TypeAdapter[ProducerRevokedRow]] = TypeAdapter(
    ProducerRevokedRow
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


class IntakeProducerInactiveError(Exception):
    """The producer is not registered for this owner, or its registration is revoked."""

    def __init__(self, producer_id: str) -> None:
        super().__init__("intake producer is not registered or is revoked")
        self.producer_id: str = producer_id


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
            (
                owner_id,
                producer_id,
                label,
                hash_receiver_token(token),
                token_prefix,
                None,
            ),
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


def create_intake_token_for_active_producer(
    db_path: Path,
    *,
    owner_id: str,
    producer_id: str,
    label: str,
) -> IssuedIntakeToken:
    """Issue an intake token only if the producer is registered and active.

    The producer check and the token insert share one immediate transaction, so
    a producer revoked between a separate pre-check and this call cannot receive
    a token: either the row is inserted while the producer is still active, or
    nothing is written at all.
    """
    _validate_identity(owner_id=owner_id, producer_id=producer_id, label=label)
    initialize_database(db_path)
    token = f"{INTAKE_TOKEN_PREFIX}{secrets.token_urlsafe(GENERATED_TOKEN_BYTES)}"
    token_prefix = token[:INTAKE_TOKEN_PREFIX_LENGTH]
    with connect_database(db_path) as connection:
        _ = connection.execute("begin immediate")
        try:
            inserted_id = _insert_token_for_active_producer(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
                label=label,
                token=token,
                token_prefix=token_prefix,
            )
        except BaseException:
            connection.rollback()
            raise
        connection.commit()
    token_id = inserted_id
    if token_id is None:
        message = "Intake token insert did not return a row id."
        raise sqlite3.IntegrityError(message)
    return IssuedIntakeToken(
        token_id=token_id,
        label=label,
        token=token,
        token_prefix=token_prefix,
    )


def create_pending_intake_token(
    db_path: Path,
    *,
    owner_id: str,
    producer_id: str,
    label: str,
) -> IssuedIntakeToken:
    """Issue a token row that cannot authenticate until it is activated.

    The row is written already revoked, so a secret nobody managed to store
    never authenticates. The caller activates it with
    :func:`activate_intake_token` only once the secret is safely written, which
    keeps a failed write from leaving an active credential behind even when the
    database cannot be reached to revoke one afterwards.
    """
    _validate_identity(owner_id=owner_id, producer_id=producer_id, label=label)
    initialize_database(db_path)
    token = f"{INTAKE_TOKEN_PREFIX}{secrets.token_urlsafe(GENERATED_TOKEN_BYTES)}"
    token_prefix = token[:INTAKE_TOKEN_PREFIX_LENGTH]
    with connect_database(db_path) as connection:
        _ = connection.execute("begin immediate")
        try:
            inserted_id = _insert_token_for_active_producer(
                connection,
                owner_id=owner_id,
                producer_id=producer_id,
                label=label,
                token=token,
                token_prefix=token_prefix,
                revoked=True,
            )
        except BaseException:
            connection.rollback()
            raise
        connection.commit()
    token_id = inserted_id
    if token_id is None:
        message = "Intake token insert did not return a row id."
        raise sqlite3.IntegrityError(message)
    return IssuedIntakeToken(
        token_id=token_id,
        label=label,
        token=token,
        token_prefix=token_prefix,
    )


def activate_intake_token(db_path: Path, token_prefix: str) -> int:
    """Make a pending token usable and report how many rows changed.

    Only a row this module inserted as pending (already revoked) can change
    here, so an activation can never revive a token revoked on purpose.
    """
    initialize_database(db_path)
    with connect_database(db_path) as connection:
        return int(
            connection.execute(
                ACTIVATE_INTAKE_TOKEN_SQL,
                (token_prefix,),
            ).rowcount
        )


def discard_pending_intake_token(db_path: Path, token_prefix: str) -> None:
    """Remove a pending token whose secret was never written.

    The row cannot authenticate either way, so dropping it keeps the token list
    free of credentials nobody holds.
    """
    initialize_database(db_path)
    with connect_database(db_path) as connection:
        _ = connection.execute(DELETE_INTAKE_TOKEN_SQL, (token_prefix,))


def _insert_token_for_active_producer(  # noqa: PLR0913 -- one bound pair per insert column.
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    producer_id: str,
    label: str,
    token: str,
    token_prefix: str,
    revoked: bool = False,
) -> int | None:
    """Check the producer and insert the token row inside the caller's transaction."""
    producer_revoked_at = PRODUCER_REVOKED_ROW_ADAPTER.validate_python(
        connection.execute(
            SELECT_ACTIVE_PRODUCER_SQL,
            (owner_id, producer_id),
        ).fetchone(),
    )
    if producer_revoked_at is None or producer_revoked_at[0] is not None:
        raise IntakeProducerInactiveError(producer_id)
    cursor = connection.execute(
        INSERT_INTAKE_TOKEN_SQL,
        (
            owner_id,
            producer_id,
            label,
            hash_receiver_token(token),
            token_prefix,
            _utc_now() if revoked else None,
        ),
    )
    return cursor.lastrowid


def _utc_now() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def list_intake_tokens_on(
    connection: sqlite3.Connection,
) -> tuple[IntakeTokenRecord, ...]:
    """Every intake token row on an existing connection, read-only.

    A listing command opens the store read-only and never initialises it, so this
    takes the connection instead of a path. The hash column is not selected, so
    no caller can leak it by accident.
    """
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
