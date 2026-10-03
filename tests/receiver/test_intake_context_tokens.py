import sqlite3
from pathlib import Path
from typing import Final, TypeAlias, cast

import pytest
from pydantic import TypeAdapter

from health_bridge.receiver.intake_tokens import (
    activate_intake_token,
    authenticate_intake_token,
    create_intake_token,
    create_pending_intake_token,
    discard_pending_intake_token,
    intake_token_status,
    revoke_active_intake_token,
    revoke_intake_token,
)
from health_bridge.receiver.tokens import (
    authenticate_receiver_token,
    create_receiver_token,
)
from health_bridge.storage.database import connect_database, initialize_database
from health_bridge.storage.intake_context import (
    PENDING_INTAKE_TOKEN_MARKER,
    ProducerRecord,
    reactivate_producer,
    register_producer,
    revoke_producer,
    revoke_producer_tokens,
)

RevokedAtRow: TypeAlias = tuple[str | None]
REVOKED_AT_ROWS_ADAPTER: Final[TypeAdapter[list[RevokedAtRow]]] = TypeAdapter(
    list[RevokedAtRow]
)


def test_issued_token_authenticates_to_its_owner_and_producer(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    issued = create_intake_token(db, owner_id="o", producer_id="p", label="l")
    principal = authenticate_intake_token(db, issued.token)
    assert principal is not None
    assert (principal.owner_id, principal.producer_id) == ("o", "p")
    assert issued.label == "l"
    assert issued.token_id > 0


def test_prefix_is_distinct_from_batch_tokens(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    issued = create_intake_token(db, owner_id="o", producer_id="p", label="l")
    batch = create_receiver_token(db, label="b")
    assert issued.token.startswith("hri_")
    assert not batch.token.startswith("hri_")
    assert issued.token_prefix.startswith("hri_")


def test_only_a_hash_is_stored(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    issued = create_intake_token(db, owner_id="o", producer_id="p", label="l")
    with sqlite3.connect(db) as connection:
        dump = "\n".join(connection.iterdump())
    assert issued.token not in dump


def test_unknown_and_wrong_tokens_fail(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    issued = create_intake_token(db, owner_id="o", producer_id="p", label="l")
    assert authenticate_intake_token(db, issued.token + "x") is None
    assert authenticate_intake_token(db, "hri_nothing") is None
    assert authenticate_intake_token(db, "") is None


def test_revocation_is_separate_per_token(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    first = create_intake_token(db, owner_id="o", producer_id="p", label="a")
    second = create_intake_token(db, owner_id="o", producer_id="p", label="b")
    revoke_intake_token(db, first.token_prefix)
    assert authenticate_intake_token(db, first.token) is None
    assert authenticate_intake_token(db, second.token) is not None


def test_intake_and_batch_tokens_do_not_cross_authenticate(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    issued = create_intake_token(db, owner_id="o", producer_id="p", label="l")
    batch = create_receiver_token(db, label="b")
    assert not authenticate_receiver_token(db, issued.token)
    assert authenticate_intake_token(db, batch.token) is None


def _token_rows(db: Path) -> int:
    with sqlite3.connect(db) as connection:
        row = cast(
            "tuple[int]",
            connection.execute("select count(*) from intake_context_tokens").fetchone(),
        )
    return row[0]


@pytest.mark.parametrize(
    ("owner_id", "producer_id", "label"),
    [
        ("", "nutrition-app", "l"),
        ("   ", "nutrition-app", "l"),
        ("local", "", "l"),
        ("local", "has space", "l"),
        ("local", "Upper", "l"),
        ("local", "bad\n", "l"),
        ("local", "x" * 65, "l"),
        ("local", "nutrition-app", ""),
        ("local", "nutrition-app", "  "),
    ],
)
def test_invalid_inputs_raise_without_inserting(
    tmp_path: Path, owner_id: str, producer_id: str, label: str
) -> None:
    db = tmp_path / "t.sqlite"
    initialize_database(db)
    with pytest.raises(ValueError, match="invalid") as raised:
        _ = create_intake_token(
            db, owner_id=owner_id, producer_id=producer_id, label=label
        )
    assert producer_id.strip() == "" or producer_id not in str(raised.value)
    assert _token_rows(db) == 0


def test_valid_pair_still_works(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _ = create_intake_token(
        db, owner_id="local", producer_id="nutrition-app", label="l"
    )
    assert _token_rows(db) == 1


def _registered_db(tmp_path: Path) -> Path:
    db = tmp_path / "t.sqlite"
    initialize_database(db)
    with connect_database(db) as connection:
        _ = connection.execute("begin immediate")
        _ = register_producer(
            connection,
            ProducerRecord(
                owner_id="owner-1",
                producer_id="nutrition-app",
                writer_bundle_id="dev.example.nutrition",
                display_label="Nutrition app",
                registered_at="2026-01-01T00:00:00Z",
                revoked_at=None,
            ),
        )
        connection.commit()
    return db


def _revoke(db: Path) -> None:
    with connect_database(db) as connection:
        _ = connection.execute("begin immediate")
        _ = revoke_producer(
            connection,
            owner_id="owner-1",
            producer_id="nutrition-app",
            revoked_at="2026-01-02T00:00:00Z",
        )
        _ = revoke_producer_tokens(
            connection,
            owner_id="owner-1",
            producer_id="nutrition-app",
        )
        connection.commit()


def test_pending_token_activates_for_an_active_producer(tmp_path: Path) -> None:
    # Given
    db = _registered_db(tmp_path)
    issued = create_pending_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="l"
    )

    # When
    activated = activate_intake_token(
        db,
        owner_id="owner-1",
        producer_id="nutrition-app",
        token_prefix=issued.token_prefix,
    )

    # Then
    assert activated == 1
    assert authenticate_intake_token(db, issued.token) is not None


def test_activation_refuses_a_producer_revoked_after_the_pending_insert(
    tmp_path: Path,
) -> None:
    # Given a pending token whose producer is revoked before activation
    db = _registered_db(tmp_path)
    issued = create_pending_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="l"
    )
    _revoke(db)

    # When
    activated = activate_intake_token(
        db,
        owner_id="owner-1",
        producer_id="nutrition-app",
        token_prefix=issued.token_prefix,
    )

    # Then the credential stays unusable
    assert activated == 0
    assert authenticate_intake_token(db, issued.token) is None


def test_activation_refuses_a_pending_row_for_another_owner(tmp_path: Path) -> None:
    # Given a pending token issued for one owner
    db = _registered_db(tmp_path)
    issued = create_pending_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="l"
    )

    # When activation is asked for a different owner
    activated = activate_intake_token(
        db,
        owner_id="owner-2",
        producer_id="nutrition-app",
        token_prefix=issued.token_prefix,
    )

    # Then only the owning producer can activate it
    assert activated == 0
    assert authenticate_intake_token(db, issued.token) is None


def _token_revoked_at_values(db: Path) -> list[str | None]:
    """The raw ``revoked_at`` column of every intake token row, in insert order."""
    with sqlite3.connect(db) as connection:
        rows = REVOKED_AT_ROWS_ADAPTER.validate_python(
            connection.execute(
                "select revoked_at from intake_context_tokens order by intake_token_id",
            ).fetchall(),
        )
    return [row[0] for row in rows]


def test_revoking_a_pending_token_makes_it_never_activate(tmp_path: Path) -> None:
    # Given a pending token whose prefix is discovered and revoked in between
    db = _registered_db(tmp_path)
    issued = create_pending_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="l"
    )
    revoked = revoke_active_intake_token(db, issued.token_prefix)

    # When the issuance that inserted it goes on to activate it
    activated = activate_intake_token(
        db,
        owner_id="owner-1",
        producer_id="nutrition-app",
        token_prefix=issued.token_prefix,
    )

    # Then the explicit revocation wins and the credential never authenticates
    assert revoked == 1
    assert activated == 0
    assert authenticate_intake_token(db, issued.token) is None


def test_a_pending_token_reactivated_after_reactivation_still_stays_revoked(
    tmp_path: Path,
) -> None:
    # Given a pending token revoked through its producer, then reactivated
    db = _registered_db(tmp_path)
    issued = create_pending_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="l"
    )
    _revoke(db)
    with connect_database(db) as connection:
        _ = connection.execute("begin immediate")
        _ = reactivate_producer(
            connection,
            owner_id="owner-1",
            producer_id="nutrition-app",
        )
        connection.commit()

    # When the stale issuance activates it after the producer is active again
    activated = activate_intake_token(
        db,
        owner_id="owner-1",
        producer_id="nutrition-app",
        token_prefix=issued.token_prefix,
    )

    # Then only a still-pending row may activate
    assert activated == 0
    assert authenticate_intake_token(db, issued.token) is None


def test_discarding_a_pending_token_keeps_a_revoked_row(tmp_path: Path) -> None:
    # Given a pending token somebody revoked before the secret write failed
    db = _registered_db(tmp_path)
    issued = create_pending_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="l"
    )
    assert revoke_active_intake_token(db, issued.token_prefix) == 1

    # When a failed issuance drops the row it inserted
    discard_pending_intake_token(db, issued.token_prefix)

    # Then the revoked row keeps its audit trail instead of disappearing
    statuses = _token_revoked_at_values(db)
    assert statuses[0] is not None
    assert statuses[0] != PENDING_INTAKE_TOKEN_MARKER
    assert intake_token_status(statuses[0]) == "revoked"


def test_pending_and_revoked_rows_report_distinct_statuses(tmp_path: Path) -> None:
    # Given one active, one pending and one revoked token row
    db = _registered_db(tmp_path)
    active = create_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="a"
    )
    pending = create_pending_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="p"
    )
    revoked = create_intake_token(
        db, owner_id="owner-1", producer_id="nutrition-app", label="r"
    )
    assert revoke_active_intake_token(db, revoked.token_prefix) == 1

    # When the raw column is mapped to a status
    statuses = [
        intake_token_status(revoked_at) for revoked_at in _token_revoked_at_values(db)
    ]
    assert statuses == ["active", "pending", "revoked"]
    assert active.token != pending.token != revoked.token
