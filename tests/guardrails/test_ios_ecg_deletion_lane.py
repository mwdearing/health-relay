from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW_MODEL = ROOT / "ios/HealthBridgeCompanion/App/HealthBridgeCompanionViewModel.swift"


def test_ecg_deletion_lane_does_not_swallow_cancellation() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    start = source.index("var sentDeletionCount = 0")
    end = source.index("guard ForegroundSyncUploadPolicy.shouldUpload(batch)", start)
    lane = source[start:end]

    cancellation = lane.index("catch is CancellationError")
    assert "throw" in lane[cancellation : cancellation + 120]
    assert cancellation < lane.index("} catch {")


def test_ecg_deletion_lane_logs_non_cancellation_errors() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    start = source.index("var sentDeletionCount = 0")
    end = source.index("guard ForegroundSyncUploadPolicy.shouldUpload(batch)", start)
    lane = source[start:end]

    generic = lane.index("} catch {")
    assert "appendActivityLog(" in lane[generic:]
    assert "[ECG] Deletion check failed" in lane[generic:]


def test_ecg_deletion_lane_does_not_promise_a_retry_for_an_unreadable_anchor() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    start = source.index("var sentDeletionCount = 0")
    end = source.index("guard ForegroundSyncUploadPolicy.shouldUpload(batch)", start)
    lane = source[start:end]

    anchor = lane.index("catch is HealthKitAnchorCursorCodecError")
    generic = lane.index("} catch {")
    assert anchor < generic
    assert "retries" not in lane[anchor:generic]
