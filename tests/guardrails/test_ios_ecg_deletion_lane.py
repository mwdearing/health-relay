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
