from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW_MODEL = ROOT / "ios/HealthBridgeCompanion/App/HealthBridgeCompanionViewModel.swift"


def test_flush_stops_sync_now_only_for_server_rejections() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    start = source.index("} else if summary.failedCount > 0 {")
    end = source.index("} else {", start)
    branch = source[start:end]

    stop = branch.index("return false")
    guard = branch.index("CompanionSyncNowCompletion.isServerRejection(")
    # A transport failure leaves uploads queued; Sync Now goes on to collect data.
    assert guard < stop
    assert branch.count("return false") == 1
