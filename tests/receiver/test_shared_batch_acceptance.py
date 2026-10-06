from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import TypeAdapter

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.receiver import batch_acceptance
from health_bridge.receiver.invitations import (
    create_pairing_invitation,
    redeem_pairing_invitation,
)
from health_bridge.receiver.source_binding import SourcePrincipalMismatchError
from health_bridge.receiver.tokens import create_receiver_token
from tests.contract.delivery_v1_support import BATCH
from tests.receiver.delivery_acceptance_support import (
    RequestSpec,
    opened_receipt,
    request,
    service,
)
from tests.receiver.test_legacy_http_contract import (
    FIXTURE_PATH,
    LEGACY_TOKEN,
    post_raw_batch,
    running_receiver,
)

if TYPE_CHECKING:
    from health_bridge.contract.batch_v1 import (
        Electrocardiogram,
        MedicationDoseEvent,
    )
    from health_bridge.receiver.tokens import ReceiverTokenPrincipal

INSTALLATION_A: Final = "00000000-0000-4000-8000-000000000001"
DEVICE_TOKEN: Final = "hb_" + "e" * 64
FOREIGN_SOURCE_KEY: Final = f"apple_health.phone.{'1' * 64}"
LEGACY_SOURCE_KEY: Final = "apple_health.phone"
MISMATCH_BODY: Final = b'{"error":"source_principal_mismatch"}'
PRINCIPAL_MISMATCH_RECEIPT: Final = "principal_mismatch"

# RELAY-R1: the two record families that carry a source_key but were missing from
# source_binding.py's ownership check and canonical-key rewrite. Both are covered
# here so a future omission in either cannot come back silently.
FAMILY_FIXTURES: Final[dict[str, str]] = {
    "electrocardiogram": (
        "fixtures/health_bridge_batch_v1.electrocardiogram.synthetic.json"
    ),
    "medication_dose_event": (
        "fixtures/health_bridge_batch_v1.medication.synthetic.json"
    ),
}
# family -> the batch's row field for it. The stored table name is the same string,
# so one mapping serves both the fixture load and the post-ingest assertion.
FAMILY_ROW_FIELDS: Final[dict[str, str]] = {
    "electrocardiogram": "electrocardiograms",
    "medication_dose_event": "medication_dose_events",
}
COUNT_ROW_ADAPTER: Final[TypeAdapter[tuple[int]]] = TypeAdapter(tuple[int])
SOURCE_ROWS_ADAPTER: Final[TypeAdapter[list[tuple[int, str]]]] = TypeAdapter(
    list[tuple[int, str]]
)
SOURCE_IDS_ADAPTER: Final[TypeAdapter[list[tuple[int]]]] = TypeAdapter(list[tuple[int]])


def test_direct_and_envelope_flows_share_the_batch_binding_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    direct_db = tmp_path / "direct.sqlite"
    envelope_db = tmp_path / "envelope.sqlite"
    _ = create_receiver_token(
        direct_db,
        label="shared-core-red",
        token=LEGACY_TOKEN,
    )
    envelope_acceptance = service(envelope_db)

    def reject_at_shared_boundary(
        batch: HealthBridgeBatchV1,
        _principal: ReceiverTokenPrincipal,
    ) -> HealthBridgeBatchV1:
        raise SourcePrincipalMismatchError(batch.sources[0].source_key)

    monkeypatch.setattr(
        batch_acceptance,
        "bind_batch_to_principal",
        reject_at_shared_boundary,
    )

    # When
    with running_receiver(direct_db) as port:
        direct = post_raw_batch(port, LEGACY_TOKEN, FIXTURE_PATH.read_bytes())
    envelope = envelope_acceptance.accept(request(RequestSpec(payload=BATCH)))

    # Then
    assert direct.status == 403
    assert opened_receipt(envelope.ack_bytes).error_code == PRINCIPAL_MISMATCH_RECEIPT


def canonical_source_key(installation_id: str) -> str:
    """Mirrors _source_belongs_to_installation on the receiver side."""
    digest = hashlib.sha256(
        f"health-bridge-pairing:installation:{installation_id}".encode()
    ).hexdigest()
    return f"{LEGACY_SOURCE_KEY}.{digest}"


def paired_device_token(db_path: Path) -> str:
    """A device-bound (v2) token, so the ownership check actually runs."""
    invitation = create_pairing_invitation(
        db_path,
        label="iphone-shared-boundary",
        receiver_url="https://health.example.test/v1/batches",
        invitation_secret=f"hbi_shared_boundary_{'s' * 8}_secret",
        invitation_code="ABCDE-FGHJK-MNPQR",
    )
    _ = redeem_pairing_invitation(
        db_path,
        installation_id=INSTALLATION_A,
        device_credential=DEVICE_TOKEN,
        platform="ios",
        invitation_code=invitation.invitation_code,
    )
    return DEVICE_TOKEN


def family_batch(
    family: str,
    *,
    source_key: str,
    record_source_key: str | None = None,
) -> HealthBridgeBatchV1:
    """Load the family fixture and repoint every source_key at ``source_key``.

    The fixtures declare synthetic.* keys, which no device-bound token owns, so
    rewriting all of them is what isolates the family under test.
    ``record_source_key`` overrides only the key the family's rows carry, which is
    what lets a test hold the declared ``sources`` key at something the token owns
    while the rows claim a foreign key -- otherwise the check could pass on the
    sources tuple alone without ever looking at the family.
    """
    fixture = HealthBridgeBatchV1.model_validate_json(
        Path(FAMILY_FIXTURES[family]).read_bytes()
    )
    rewritten = fixture.model_copy(
        update={
            "sources": (
                fixture.sources[0].model_copy(update={"source_key": source_key}),
            ),
            "samples": tuple(
                sample.model_copy(update={"source_key": source_key})
                for sample in fixture.samples
            ),
            "workouts": tuple(
                workout.model_copy(update={"source_key": source_key})
                for workout in fixture.workouts
            ),
            "sleep_sessions": tuple(
                session.model_copy(update={"source_key": source_key})
                for session in fixture.sleep_sessions
            ),
            "deleted_records": tuple(
                deleted.model_copy(update={"source_key": source_key})
                for deleted in fixture.deleted_records
            ),
            "lab_results": tuple(
                lab_result.model_copy(update={"source_key": source_key})
                for lab_result in fixture.lab_results
            ),
            "sync": fixture.sync.model_copy(
                update={
                    "cursors": tuple(
                        cursor.model_copy(update={"source_key": source_key})
                        for cursor in fixture.sync.cursors
                    )
                }
            ),
        }
    )
    row_key = source_key if record_source_key is None else record_source_key
    return rewritten.model_copy(update=_family_rows(rewritten, family, row_key))


def _family_rows(
    batch: HealthBridgeBatchV1,
    family: str,
    source_key: str,
) -> dict[str, tuple[Electrocardiogram, ...] | tuple[MedicationDoseEvent, ...]]:
    """Repoint just this family's rows, leaving every other family alone."""
    if family == "electrocardiogram":
        rows = batch.electrocardiograms
        assert rows, "the electrocardiogram fixture carries no rows"
        return {
            "electrocardiograms": tuple(
                row.model_copy(update={"source_key": source_key}) for row in rows
            )
        }
    rows = batch.medication_dose_events
    assert rows, "the medication fixture carries no rows"
    return {
        "medication_dose_events": tuple(
            row.model_copy(update={"source_key": source_key}) for row in rows
        )
    }


@pytest.mark.parametrize("family", sorted(FAMILY_FIXTURES))
def test_foreign_installation_key_is_rejected_for_every_record_family(
    tmp_path: Path,
    *,
    family: str,
) -> None:
    """RELAY-R1: a device-bound token must not be able to send medication or ECG
    rows under another installation's source_key. Those families' upsert conflict
    key is (source_id, client_record_id), so an accepted foreign key lets one
    installation inject or overwrite another's rows."""
    # Given
    db_path = tmp_path / "receiver.sqlite"
    token = paired_device_token(db_path)
    # The declared sources key is one the token owns; only the family's rows claim
    # the foreign key, so a 403 proves the family itself is covered.
    body = family_batch(
        family,
        source_key=canonical_source_key(INSTALLATION_A),
        record_source_key=FOREIGN_SOURCE_KEY,
    ).model_dump_json(exclude_none=True)

    # When
    with running_receiver(db_path) as port:
        observation = post_raw_batch(port, token, body.encode())

    # Then
    assert observation.status == 403
    assert observation.body == MISMATCH_BODY
    with sqlite3.connect(db_path) as connection:
        table = FAMILY_ROW_FIELDS[family]
        (row_count,) = COUNT_ROW_ADAPTER.validate_python(
            connection.execute(
                f"select count(*) from {table}"  # noqa: S608
            ).fetchone()
        )
    assert row_count == 0, f"{family} rows were ingested despite the 403"


@pytest.mark.parametrize("family", sorted(FAMILY_FIXTURES))
def test_legacy_key_family_batch_ingests_under_the_canonical_source(
    tmp_path: Path,
    *,
    family: str,
) -> None:
    """RELAY-R1: the app may still send the legacy apple_health.phone key. sources
    is rewritten to the canonical key, so if the family's own rows are not, then
    source_id() cannot resolve them: /v1/batches 500s and the outbox retries
    forever."""
    # Given
    db_path = tmp_path / "receiver.sqlite"
    token = paired_device_token(db_path)
    body = family_batch(family, source_key=LEGACY_SOURCE_KEY).model_dump_json(
        exclude_none=True
    )
    canonical = canonical_source_key(INSTALLATION_A)

    # When
    with running_receiver(db_path) as port:
        observation = post_raw_batch(port, token, body.encode())

    # Then
    assert observation.status == 202, observation.body
    with sqlite3.connect(db_path) as connection:
        stored = {
            source_key: source_id
            for source_id, source_key in SOURCE_ROWS_ADAPTER.validate_python(
                connection.execute(
                    "select source_id, source_key from sources"
                ).fetchall()
            )
        }
        table = FAMILY_ROW_FIELDS[family]
        source_ids = {
            source_id
            for (source_id,) in SOURCE_IDS_ADAPTER.validate_python(
                connection.execute(
                    f"select distinct source_id from {table}"  # noqa: S608
                ).fetchall()
            )
        }
    assert len(source_ids) == 1, (
        f"{family} rows are not all bound to one source: {sorted(source_ids)}"
    )
    assert canonical in stored, f"canonical source row missing: {sorted(stored)}"
    assert source_ids == {stored[canonical]}, (
        f"{family} rows are bound to {sorted(source_ids)}, "
        f"not the canonical {stored[canonical]}"
    )
