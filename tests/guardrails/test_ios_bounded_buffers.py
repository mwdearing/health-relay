"""Guardrails for bounded reads in the iOS companion's zip import and receiver client.

The behaviour is proven by the XCTests in ios/HealthBridgeCompanion/Tests; these checks
only pin the structural facts that the XCTests cannot see on a Linux host.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "ios/HealthBridgeCompanion/Sources/HealthBridgeCompanionCore"
ZIP_READER = CORE / "MinimalZipReader.swift"
IMPORTER = CORE / "AppleHealthExportLabImporter.swift"
RECEIVER_CLIENT = CORE / "ReceiverClient.swift"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_receiver_client_never_buffers_unbounded_session_data() -> None:
    source = _text(RECEIVER_CLIENT)
    assert "session.data(for:" not in source
    assert source.count("boundedData(") >= 4  # helper definition plus three call sites
    assert "session.bytes(for:" in source


def test_zip_reader_checks_declared_sizes_before_reading_or_inflating() -> None:
    source = _text(ZIP_READER)
    assert "static let maximumEntryCompressedBytes" in source
    assert "static let maximumEntryUncompressedBytes" in source
    body = source.split("static func readEntryData", 1)[1].split(
        "// MARK: - End of central directory", 1
    )[0]
    check = body.find("checkDeclaredSizes(entry)")
    assert check != -1, "readEntryData must call checkDeclaredSizes"
    assert check < body.find("handle.read(upToCount: entry.compressedSize)")
    assert check < body.find("inflateRawDeflate(compressed")


def test_importer_caps_members_and_cumulative_bytes_and_checks_each_member() -> None:
    source = _text(IMPORTER)
    assert "static let maximumClinicalRecordMembers" in source
    assert "static let maximumClinicalRecordTotalBytes" in source
    loop = source.split("for entry in entries {", 1)[1]
    assert loop.find("checkDeclaredSizes(entry)") < loop.find(
        "readEntryData(entry, from: url)"
    )
    assert "archiveLimitExceeded" in source
