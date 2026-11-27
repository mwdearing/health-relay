from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Never

from health_bridge.contract import HealthBridgeBatchV1
from health_bridge.contract.batch_v1 import SyncCursor
from health_bridge.receiver import delivery_acceptance as acceptance_module
from tests.receiver.delivery_acceptance_support import (
    RequestSpec,
    counts,
    opened_receipt,
    request,
    service,
)

if TYPE_CHECKING:
    import pytest


def test_authenticated_storage_failure_returns_closed_retryable_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    db_path = tmp_path / "unavailable.sqlite"
    acceptance = service(db_path)

    def unavailable(_db_path: Path) -> Never:
        raise OSError

    monkeypatch.setattr(acceptance_module, "connect_database", unavailable)
    # When
    result = acceptance.accept(request())
    # Then
    receipt = opened_receipt(result.ack_bytes)
    assert (receipt.result, receipt.error_code) == (
        "retryable",
        "storage_unavailable",
    )
    assert counts(db_path) == (0, 0, 0)


def _sleep_epoch_batch(epoch: int) -> bytes:
    batch = HealthBridgeBatchV1.model_validate_json(
        Path("fixtures/health_bridge_batch_v1.synthetic.json").read_bytes()
    )
    sleep = batch.sleep_sessions[0]
    cursors = (
        SyncCursor(
            source_key=sleep.source_key,
            cursor_kind="anchored_sleep_sync",
            cursor_value=f"anchor-{epoch}",
        ),
        SyncCursor(
            source_key=sleep.source_key,
            cursor_kind="anchored_sleep_baseline_reset",
            cursor_value=f"v2:{epoch}",
        ),
    )
    return (
        batch.model_copy(
            update={
                "samples": (),
                "workouts": (),
                "sleep_sessions": (sleep,),
                "deleted_records": (),
                "sync": batch.sync.model_copy(update={"cursors": cursors}),
            }
        )
        .model_dump_json(by_alias=True)
        .encode()
    )


def test_stale_sleep_reset_epoch_returns_terminal_receipt_with_epoch_floor(
    tmp_path: Path,
) -> None:
    # Given
    acceptance = service(tmp_path / "stale.sqlite")
    newer = acceptance.accept(
        request(RequestSpec(payload=_sleep_epoch_batch(200), envelope_byte=1))
    )
    assert opened_receipt(newer.ack_bytes, envelope_byte=1).result == "committed"
    # When
    stale = acceptance.accept(
        request(RequestSpec(payload=_sleep_epoch_batch(100), envelope_byte=2))
    )
    # Then
    receipt = opened_receipt(stale.ack_bytes, envelope_byte=2)
    assert (receipt.result, receipt.error_code, receipt.minimum_reset_epoch) == (
        "terminal",
        "sleep_baseline_reset_epoch_stale",
        200,
    )
