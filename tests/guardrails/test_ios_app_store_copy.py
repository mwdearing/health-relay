"""App Store review copy guardrails for the iOS companion.

The app relays data and does not interpret it, so user-facing strings must not make
medical claims or promise delivery timing iOS does not guarantee.
"""

import plistlib
import re
from pathlib import Path
from typing import cast

IOS = Path("ios/HealthBridgeCompanion")
APP_INFO_PLIST = IOS / "App/Info.plist"
XCODE_PROJECT = IOS / "HealthBridgeCompanion.xcodeproj/project.pbxproj"
CONTENT_VIEW = IOS / "App/ContentView.swift"
SWIFT_ROOTS = (IOS / "App", IOS / "Sources/HealthBridgeCompanionCore")

BANNED_WORDS = (
    r"diagnos(e|es|ed|is|ing)",
    r"detect\w*",
    r"treat\w*",
    r"cure[sd]?",
    r"alert you",
    r"monitor your heart",
    r"continuous\w*",
    r"real[- ]time",
    r"instant\w*",
)
BANNED = re.compile(rf"\b({'|'.join(BANNED_WORDS)})\b", re.IGNORECASE)
# Literals cleared by review: each is exact and is not a health or timing claim.
ALLOWED_LITERALS = frozenset({"Rollback detected"})

STRING_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
PURPOSE_KEYS = (
    "NSCameraUsageDescription",
    "NSHealthShareUsageDescription",
    "NSHealthUpdateUsageDescription",
    "NSLocalNetworkUsageDescription",
)


def _swift_literals(path: Path) -> list[tuple[int, str]]:
    literals: list[tuple[int, str]] = []
    in_block = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.count('"""') % 2 == 1:
            in_block = not in_block
            continue
        if in_block:
            literals.append((number, line.strip()))
            continue
        if line.lstrip().startswith("//"):
            continue
        literals.extend(
            (number, match.group(1)) for match in STRING_LITERAL.finditer(line)
        )
    return literals


def _project_purpose_strings() -> dict[str, set[str]]:
    text = XCODE_PROJECT.read_text(encoding="utf-8")
    return {
        key: set(re.findall(rf'INFOPLIST_KEY_{key} = "((?:[^"\\]|\\.)*)";', text))
        for key in PURPOSE_KEYS
    }


def _plist() -> dict[str, object]:
    with APP_INFO_PLIST.open("rb") as handle:
        return cast("dict[str, object]", plistlib.load(handle))


def test_scanner_reads_app_and_core_sources() -> None:
    files = [path for root in SWIFT_ROOTS for path in sorted(root.rglob("*.swift"))]
    assert CONTENT_VIEW in files
    assert sum(len(_swift_literals(path)) for path in files) > 500


def test_banned_pattern_catches_each_claim() -> None:
    for phrase in (
        "can diagnose",
        "Detects irregular rhythm",
        "treatment",
        "cures",
        "will alert you to",
        "monitor your heart",
        "syncs continuously",
        "real-time",
        "real time",
        "instantly",
    ):
        assert BANNED.search(phrase), phrase
    assert not BANNED.search("Diagnostics")


def test_swift_strings_make_no_medical_or_timing_claims() -> None:
    offenders = [
        f"{path}:{number}: {literal}"
        for root in SWIFT_ROOTS
        for path in sorted(root.rglob("*.swift"))
        for number, literal in _swift_literals(path)
        if BANNED.search(literal) and literal not in ALLOWED_LITERALS
    ]
    assert offenders == []


def test_purpose_strings_make_no_medical_or_timing_claims() -> None:
    plist = _plist()
    project = _project_purpose_strings()
    for key in PURPOSE_KEYS:
        value = plist.get(key)
        if value is not None:
            assert isinstance(value, str)
            assert not BANNED.search(value), key
        for text in project[key]:
            assert not BANNED.search(text), key


def test_project_purpose_strings_match_info_plist() -> None:
    plist = _plist()
    project = _project_purpose_strings()
    for key in PURPOSE_KEYS[:3]:
        assert project[key] == {plist[key]}, key


def test_health_read_purpose_names_sensitive_types_and_per_type_choice() -> None:
    text = _plist()["NSHealthShareUsageDescription"]
    assert isinstance(text, str)
    required = (
        "only the",
        "you choose to allow",
        "ECG",
        "medication",
        "your own receiver",
        "developer",
    )
    for phrase in required:
        assert phrase in text, phrase


def test_settings_states_relay_only_and_doctor_line() -> None:
    literals = [literal for _number, literal in _swift_literals(CONTENT_VIEW)]
    assert any(
        "does not interpret" in literal and "doctor" in literal for literal in literals
    )


def test_no_medical_symbol_for_app_diagnostics() -> None:
    assert "stethoscope" not in CONTENT_VIEW.read_text(encoding="utf-8")
