"""App Store build configuration guardrails (R1, guideline 5.1.3(ii)).

The App Store build must not store health data in iCloud. It is built from a dedicated
``AppStore`` configuration that defines ``HEALTHRELAY_APPSTORE``, uses an entitlements
file with no iCloud or ubiquity keys, uses an Info.plist without the iCloud document
container, and routes every transport decision through one build-aware function.
"""

import plistlib
import re
from pathlib import Path
from typing import cast

IOS = Path("ios/HealthBridgeCompanion")
PBXPROJ = IOS / "HealthBridgeCompanion.xcodeproj/project.pbxproj"
BETA_ENTITLEMENTS = IOS / "App/HealthBridgeCompanion.entitlements"
APPSTORE_ENTITLEMENTS = IOS / "App/HealthBridgeCompanionAppStore.entitlements"
BETA_INFO = IOS / "App/Info.plist"
APPSTORE_INFO = IOS / "App/Info-AppStore.plist"
VIEW_MODEL = IOS / "App/HealthBridgeCompanionViewModel.swift"
CONTENT_VIEW = IOS / "App/ContentView.swift"
APP_ROOT = IOS / "App"

CONDITION = "HEALTHRELAY_APPSTORE"
ICLOUD_PREFIXES = ("com.apple.developer.icloud", "com.apple.developer.ubiquity")
HELPER_CALL = re.compile(r"(?<![\w.])appBuildEffectiveTransport\((?:\w+\.)*$")


def _load_plist(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", plistlib.loads(path.read_bytes()))


def _appstore_target_block() -> tuple[str, str] | None:
    """Return (config id, buildSettings) of the app target AppStore configuration."""
    text = PBXPROJ.read_text(encoding="utf-8")
    pattern = re.compile(
        r"""
        (?P<id>[0-9A-F]{24})\ /\*\ AppStore\ \*/\ =\ \{\s*isa\ =\ XCBuildConfiguration;
        \s*buildSettings\ =\ \{(?P<settings>.*?)\};\s*name\ =\ AppStore;\s*\};
        """,
        re.DOTALL | re.VERBOSE,
    )
    for match in pattern.finditer(text):
        settings = match.group("settings")
        if "PRODUCT_BUNDLE_IDENTIFIER = com.example.HealthBridgeCompanion;" in settings:
            return match.group("id"), settings
    return None


def test_appstore_configuration_is_wired_to_the_app_target() -> None:
    # Given: the project file and the app target's configuration list.
    text = PBXPROJ.read_text(encoding="utf-8")
    found = _appstore_target_block()
    assert found is not None, "app target has no AppStore build configuration"
    config_id, settings = found
    list_line = next(
        line
        for line in text.splitlines()
        if 'Build configuration list for PBXNativeTarget "HealthBridgeCompanion"'
        in line
        and "isa = XCConfigurationList" in line
    )

    # Then: the app target's list includes the AppStore configuration.
    assert config_id in list_line

    # Then: the configuration defines the compile condition, entitlements and plist.
    assert CONDITION in settings
    assert (
        "CODE_SIGN_ENTITLEMENTS = App/HealthBridgeCompanionAppStore.entitlements;"
        in settings
    )
    assert "INFOPLIST_FILE = App/Info-AppStore.plist;" in settings


def test_appstore_entitlements_have_no_icloud_or_ubiquity_keys() -> None:
    # Given: the App Store entitlements file.
    assert APPSTORE_ENTITLEMENTS.exists(), "App Store entitlements file is missing"
    entitlements = _load_plist(APPSTORE_ENTITLEMENTS)
    raw = APPSTORE_ENTITLEMENTS.read_text(encoding="utf-8")

    # Then: no iCloud or ubiquity key, and no iCloud value, survives.
    icloud_keys = [key for key in entitlements if key.startswith(ICLOUD_PREFIXES)]
    assert icloud_keys == []
    assert "iCloud" not in raw
    assert "$(HEALTH_BRIDGE_ICLOUD_CONTAINER_IDENTIFIER)" not in raw

    # Then: HealthKit stays, because the App Store build still reads Health data.
    assert entitlements["com.apple.developer.healthkit"] is True
    assert entitlements["com.apple.developer.healthkit.background-delivery"] is True


def test_beta_entitlements_still_carry_the_icloud_mailbox() -> None:
    # Given: the beta entitlements, which the App Store build must not inherit.
    beta = _load_plist(BETA_ENTITLEMENTS)

    # Then: the beta file keeps its iCloud keys (D1: beta keeps the mailbox).
    assert "com.apple.developer.icloud-services" in beta
    assert "com.apple.developer.ubiquity-container-identifiers" in beta


def test_appstore_info_plist_only_drops_the_ubiquitous_container() -> None:
    # Given: the beta Info.plist and the App Store copy.
    beta = _load_plist(BETA_INFO)
    appstore = _load_plist(APPSTORE_INFO)

    # Then: the iCloud document container is gone and nothing else changed.
    assert "NSUbiquitousContainers" in beta
    assert "NSUbiquitousContainers" not in appstore
    expected = {
        key: value for key, value in beta.items() if key != "NSUbiquitousContainers"
    }
    assert expected == appstore


def test_transport_selection_goes_through_one_build_aware_function() -> None:
    # Given: the view model source, which defines the single build-aware helper.
    source = VIEW_MODEL.read_text(encoding="utf-8")

    # Then: the helper returns the mailbox transport only outside the App Store build.
    helper = re.search(
        r"""
        func\ appBuildEffectiveTransport\(_\ stored:\ ReceiverTransportKind\?\)
        \ ->\ ReceiverTransportKind\?\ \{(?P<body>.*?)\n\}
        """,
        source,
        re.DOTALL | re.VERBOSE,
    )
    assert helper is not None, "appBuildEffectiveTransport helper is missing"
    body = str(helper.group("body"))
    assert f"#if {CONDITION}" in body
    assert (
        "return stored == .mailbox ? ReceiverTransportKind.directHTTP : stored" in body
    )


def test_no_transport_decision_reads_the_raw_stored_transport() -> None:
    # Given: every App source file, including the view model that defines the helper.
    sources = {
        path: path.read_text(encoding="utf-8") for path in APP_ROOT.rglob("*.swift")
    }

    # Then: every raw read of settingsStore.activeTransport is passed into the helper.
    for path, text in sources.items():
        for match in re.finditer(r"settingsStore\.activeTransport", text):
            prefix = text[: match.start()]
            assert HELPER_CALL.search(prefix), (
                f"{path.name} reads the raw transport outside the helper"
            )


def test_icloud_mailbox_copy_is_compiled_out_of_the_appstore_build() -> None:
    # Given: the content view source.
    source = CONTENT_VIEW.read_text(encoding="utf-8")

    # Then: the iCloud mailbox copy and key section sit inside the guard.
    marker = f"#if !{CONDITION}"
    bodies = [part.split("#endif", 1)[0] for part in source.split(marker)[1:]]
    assert any("Encrypted iCloud Mailbox (Beta)" in body for body in bodies)
    assert any("Mailbox connection key" in body for body in bodies)


def test_ios_ci_builds_the_app_store_configuration_unsigned() -> None:
    workflow = Path(".github/workflows/ios.yml").read_text()
    step = workflow.split("- name: Build unsigned App Store configuration", 1)
    assert len(step) == 2
    body = step[1].split("- name:", 1)[0]
    assert "-configuration AppStore" in body
    assert "-sdk iphoneos" in body
    assert "CODE_SIGNING_ALLOWED=NO" in body


# mailboxKeyBlocksSync is itself checked to require usesMailboxTransport.
GATE_TOKENS = (
    "usesMailboxTransport",
    "appBuildEffectiveTransport(",
    "mailboxKeyBlocksSync",
    "appBuildShowsMailboxPairing(",
)
MAILBOX_CONTENT_NEEDLES = (
    "mailbox connection key is lost",
    "Mailbox delivery",
    "mailboxKeyDiagnosticState",
    "destination.privacySummary",
)


def _ungated_lines(source: str, needle: str) -> list[int]:
    """Return 1-based lines with needle that are neither excluded nor gated."""
    lines = source.splitlines()
    frames: list[bool] = []
    ungated: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#if"):
            frames.append(stripped == f"#if !{CONDITION}")
        elif stripped.startswith("#endif") and frames:
            del frames[-1]
        if needle in line:
            window = "\n".join(lines[max(0, index - 3) : index + 1])
            in_guard = any(frames)
            if not in_guard and not any(token in window for token in GATE_TOKENS):
                ungated.append(index + 1)
    return ungated


def test_mailbox_strings_in_contentview_are_gated_by_the_effective_transport() -> None:
    # Given: the content view source.
    source = CONTENT_VIEW.read_text(encoding="utf-8")

    # Then: each mailbox-only string or state read is App Store excluded or gated.
    for needle in MAILBOX_CONTENT_NEEDLES:
        assert _ungated_lines(source, needle) == [], f"{needle} is not gated"


def test_pairing_recovery_retry_depends_on_the_effective_mailbox_transport() -> None:
    # Given: the content view source.
    source = CONTENT_VIEW.read_text(encoding="utf-8")

    # Then: the mailbox-key condition is one property that needs mailbox transport.
    helper = re.search(
        r"""
        private\ var\ mailboxKeyBlocksSync:\ Bool\ \{\s*
        viewModel\.usesMailboxTransport\s&&\sviewModel\.mailboxKeyDiagnosticState\s==\s\.lost
        """,
        source,
        re.VERBOSE,
    )
    assert helper is not None, "mailboxKeyBlocksSync must require usesMailboxTransport"

    # Then: the copy and the Retry button both read that property, so the App Store
    # never shows the mailbox-key branch and keeps the Retry action.
    copy = "Automatic sync is paused because the mailbox connection key is lost"
    assert re.search(r"mailboxKeyBlocksSync\s*\?\s*\"" + re.escape(copy), source)
    assert re.search(r"if !mailboxKeyBlocksSync \{\s*Button\(\"Retry Pairing\"", source)
