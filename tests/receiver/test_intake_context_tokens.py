import sqlite3
from pathlib import Path
from typing import cast

import pytest

from health_bridge.receiver.intake_tokens import (
    authenticate_intake_token,
    create_intake_token,
    revoke_intake_token,
)
from health_bridge.receiver.tokens import (
    authenticate_receiver_token,
    create_receiver_token,
)
from health_bridge.storage.database import initialize_database


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
