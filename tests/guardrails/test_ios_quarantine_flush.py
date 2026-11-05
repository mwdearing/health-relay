from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW_MODEL = ROOT / "ios/HealthBridgeCompanion/App/HealthBridgeCompanionViewModel.swift"
FILE_OUTBOX = (
    ROOT
    / "ios/HealthBridgeCompanion/Sources/HealthBridgeCompanionCore/FileOutbox.swift"
)


def test_flush_quarantine_failure_does_not_escape_the_upload_catch() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    start = source.index("// Resending the same bytes can never succeed")
    failed = "outbox.noteDiagnosticDelivery(itemID: item.id, outcome: .failed)"
    end = source.index(failed, start)
    block = source[start:end]

    assert "try? outbox.quarantineItem(" in block
    assert "try outbox.quarantineItem(" not in block


def test_duplicate_quarantine_returns_the_stored_record() -> None:
    source = FILE_OUTBOX.read_text(encoding="utf-8")
    start = source.index("public func quarantineItem(")
    end = source.index("public func quarantinedItems()", start)

    assert "return duplicate ?? record" in source[start:end]
