"""Destination review accessibility, privacy-manifest API coverage and entitlements."""

import plistlib
import re
from pathlib import Path
from typing import cast

IOS = Path("ios/HealthBridgeCompanion")
CONTENT_VIEW = IOS / "App/ContentView.swift"
PRIVACY_MANIFEST = IOS / "App/PrivacyInfo.xcprivacy"
ENTITLEMENTS = (
    IOS / "App/HealthBridgeCompanion.entitlements",
    IOS / "MailboxQA/HealthBridgeCompanionMailboxQA.entitlements",
    IOS / "PublicDocumentsQA/HealthBridgeCompanionPublicDocumentsQA.entitlements",
)
QA_INFO_PLISTS = (
    IOS / "MailboxQA/Info.plist",
    IOS / "PublicDocumentsQA/Info.plist",
)
DISK_SPACE_SYMBOLS = re.compile(
    r"\b(statfs|statvfs|fstatfs|fstatvfs|systemFreeSize|systemSize)\b|volumeAvailableCapacity\w*|volumeTotalCapacityKey"
)


def _plist(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        return cast("dict[str, object]", plistlib.load(handle))


def test_destination_review_does_not_replace_the_spoken_receiver_host() -> None:
    source = CONTENT_VIEW.read_text(encoding="utf-8")
    view = source.split("private struct PairingDestinationReviewView", 1)[1]
    view = view.split("\n}\n", 1)[0]
    chain = view.split("Text(destination.receiverAuthority)", 1)[1].split("}", 1)[0]
    assert ".accessibilityLabel(" not in chain
    assert "ScrollView {" in view


def test_disk_space_apis_are_declared_in_the_privacy_manifest_or_absent() -> None:
    entries = cast(
        "list[dict[str, object]]", _plist(PRIVACY_MANIFEST)["NSPrivacyAccessedAPITypes"]
    )
    declared = {str(entry["NSPrivacyAccessedAPIType"]) for entry in entries}
    offenders = [
        f"{path}: {match.group(0)}"
        for root in (IOS / "Sources", IOS / "App", IOS / "MailboxQA")
        for path in root.rglob("*.swift")
        for match in DISK_SPACE_SYMBOLS.finditer(path.read_text(encoding="utf-8"))
    ]
    if "NSPrivacyAccessedAPICategoryDiskSpace" not in declared:
        assert offenders == []


def test_unused_icloud_key_value_store_entitlement_is_not_granted() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (IOS / "Sources", IOS / "App")
        for path in root.rglob("*.swift")
    )
    assert "NSUbiquitousKeyValueStore" not in sources
    for path in ENTITLEMENTS:
        assert "com.apple.developer.ubiquity-kvstore-identifier" not in _plist(path)


def test_qa_targets_do_not_bypass_app_transport_security() -> None:
    for path in QA_INFO_PLISTS:
        ats = cast("dict[str, object]", _plist(path)["NSAppTransportSecurity"])
        assert ats.get("NSAllowsArbitraryLoads") is not True
