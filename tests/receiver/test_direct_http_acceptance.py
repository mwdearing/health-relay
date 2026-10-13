from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from typing import TYPE_CHECKING, cast

from health_bridge.contract.batch_v1 import MAX_BATCH_ENTRIES
from health_bridge.receiver.batch_acceptance import BatchAcceptanceCore
from health_bridge.receiver.server import MAX_BATCH_BYTES
from health_bridge.receiver.tokens import create_receiver_token
from tests.receiver.test_legacy_http_contract import (
    FIXTURE_PATH,
    LEGACY_TOKEN,
    HttpObservation,
    post_raw_batch,
    running_receiver,
)

MEDICATION_FIXTURE_PATH = Path(
    "fixtures/health_bridge_batch_v1.medication.synthetic.json"
)

if TYPE_CHECKING:
    import pytest

    from health_bridge.receiver.batch_acceptance import PreparedBatch
    from health_bridge.storage.models import IngestResult


def test_direct_http_preserves_exact_five_megabyte_framing_boundary(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "framing.sqlite"
    _ = create_receiver_token(db_path, label="framing", token=LEGACY_TOKEN)
    exact_limit_body = b" " * (MAX_BATCH_BYTES - 2) + b"{}"

    # When
    with running_receiver(db_path) as port:
        at_limit = post_raw_batch(port, LEGACY_TOKEN, exact_limit_body)
        with closing(HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
            connection.request(
                "POST",
                "/v1/batches",
                body=b"",
                headers={
                    "Authorization": f"Bearer {LEGACY_TOKEN}",
                    "Content-Length": str(MAX_BATCH_BYTES + 1),
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            above_limit = HttpObservation(
                status=response.status,
                content_type=response.getheader("Content-Type"),
                content_length=response.getheader("Content-Length"),
                www_authenticate=response.getheader("WWW-Authenticate"),
                body=response.read(),
            )

    # Then
    assert at_limit == HttpObservation(
        status=422,
        content_type="application/json",
        content_length="64",
        www_authenticate=None,
        body=b'{"error":"payload does not match health_bridge.batch.v1 schema"}',
    )
    assert above_limit == HttpObservation(
        status=413,
        content_type="application/json",
        content_length="27",
        www_authenticate=None,
        body=b'{"error":"batch_too_large"}',
    )


def test_direct_http_preserves_exact_storage_failure_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    db_path = tmp_path / "storage.sqlite"
    _ = create_receiver_token(db_path, label="storage", token=LEGACY_TOKEN)

    def fail_commit(
        _db_path: Path,
        _prepared: PreparedBatch,
        _source_name: str,
    ) -> IngestResult:
        message = "synthetic direct storage failure"
        raise sqlite3.OperationalError(message)

    monkeypatch.setattr(BatchAcceptanceCore, "commit", fail_commit)

    # When
    with running_receiver(db_path) as port:
        observed = post_raw_batch(port, LEGACY_TOKEN, FIXTURE_PATH.read_bytes())

    # Then
    assert observed == HttpObservation(
        status=500,
        content_type="application/json",
        content_length="39",
        www_authenticate=None,
        body=b'{"error":"records could not be stored"}',
    )


def test_direct_http_rejects_batch_over_documented_entry_maximum(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "entry-cap.sqlite"
    _ = create_receiver_token(db_path, label="entry-cap", token=LEGACY_TOKEN)
    document = cast(
        "dict[str, object]",
        json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
    )
    samples = cast("list[object]", document["samples"])
    document["samples"] = [samples[0]] * (MAX_BATCH_ENTRIES + 1)
    oversized_body = json.dumps(document).encode("utf-8")

    # When
    with running_receiver(db_path) as port:
        observation = post_raw_batch(port, LEGACY_TOKEN, oversized_body)

    # Then
    assert observation == HttpObservation(
        status=422,
        content_type="application/json",
        content_length="64",
        www_authenticate=None,
        body=b'{"error":"payload does not match health_bridge.batch.v1 schema"}',
    )
    with closing(sqlite3.connect(db_path)) as connection:
        stored = cast(
            "tuple[int]",
            connection.execute("select count(*) from samples").fetchone(),
        )
    assert stored == (0,)


def test_direct_http_refuses_out_of_range_integer_as_payload_error(
    tmp_path: Path,
) -> None:
    # Given
    db_path = tmp_path / "int64.sqlite"
    _ = create_receiver_token(db_path, label="int64", token=LEGACY_TOKEN)
    payload = cast(
        "dict[str, object]",
        json.loads(MEDICATION_FIXTURE_PATH.read_text(encoding="utf-8")),
    )
    events = cast("list[dict[str, object]]", payload["medication_dose_events"])
    events[0]["status_raw"] = 2**63
    body = json.dumps(payload).encode("utf-8")

    # When
    with running_receiver(db_path) as port:
        observed = post_raw_batch(port, LEGACY_TOKEN, body)

    # Then
    assert observed == HttpObservation(
        status=422,
        content_type="application/json",
        content_length="64",
        www_authenticate=None,
        body=b'{"error":"payload does not match health_bridge.batch.v1 schema"}',
    )
    with sqlite3.connect(db_path) as connection:
        sync_runs = cast(
            "tuple[int]",
            connection.execute("select count(*) from sync_runs").fetchone(),
        )
        assert sync_runs == (0,)
