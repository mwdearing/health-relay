import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW_MODEL = ROOT / "ios/HealthBridgeCompanion/App/HealthBridgeCompanionViewModel.swift"
LANE = re.compile(
    r"var sentDeletionCount = 0\n.*?\n(?=\s*guard ForegroundSyncUploadPolicy)",
    re.DOTALL,
)
CANCELLATION = re.compile(
    r"\} catch is CancellationError \{\n\s*throw CancellationError\(\)\n\s*\}"
)
ANCHOR = re.compile(
    r"\} catch is HealthKitAnchorCursorCodecError \{\n(?P<body>.*?)\n {12}\} catch \{",
    re.DOTALL,
)
GENERIC = re.compile(r"\} catch \{\n(?P<body>(?:(?! {12}\}\n).*\n)*?) {12}\}\n")


# Patterns split across lines; joined where used.
RESET_CALL = (
    r"resetElectrocardiogramDeletionAnchor\(\s*in: cursorStore,",
    r"\s*receiverBindingID: receiverBindingID\s*\)",
)
RESET_SAVE = (
    r'try\? cursorStore\.saveCursorValue\(\s*"",',
    r"\s*receiverBindingID: receiverBindingID,",
    r'\s*sourceKey: "apple_health\.phone",\s*cursorKind: ',
    r"ElectrocardiogramSyncBatchFactory\.deletionAnchorCursorKind\s*\)",
)
FAILURE_LOG = (
    r'appendActivityLog\(\s*"\[ECG\] Deletion check failed: ',
    r"\\\(describe\(error\)\)",
)


def _lane() -> str:
    match = LANE.search(VIEW_MODEL.read_text(encoding="utf-8"))
    assert match is not None
    return match.group(0)


def test_ecg_deletion_lane_does_not_swallow_cancellation() -> None:
    # Stale receiver scope and connection generation also surface as CancellationError.
    lane = _lane()
    assert CANCELLATION.search(lane)
    assert lane.index("catch is CancellationError") < lane.index("} catch {")


def test_ecg_deletion_lane_resets_an_unreadable_anchor() -> None:
    match = ANCHOR.search(_lane())
    assert match is not None
    body = match.group("body")
    assert re.search(
        "".join(RESET_CALL),
        body,
    )
    helper = re.search(
        r"private func resetElectrocardiogramDeletionAnchor\(.*?\n    \}\n",
        VIEW_MODEL.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert helper is not None
    assert re.search(
        "".join(RESET_SAVE),
        helper.group(0),
    )
    assert "appendActivityLog(" in body
    assert "retries" not in body


def test_ecg_deletion_lane_logs_other_errors() -> None:
    lane = _lane()
    generic = lane[lane.index("} catch {") :]
    match = GENERIC.match(generic)
    assert match is not None
    assert re.search(
        "".join(FAILURE_LOG),
        match.group("body"),
    )
