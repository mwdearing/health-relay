"""First-run sync consent gates every send path in the iOS companion."""

import re
from pathlib import Path

APP = Path("ios/HealthBridgeCompanion/App")
CONSENT_VIEW = APP / "SyncConsentView.swift"
CORE = Path("ios/HealthBridgeCompanion/Sources/HealthBridgeCompanionCore")
CONSENT_STORE = CORE / "SyncConsentStore.swift"
VIEW_MODEL = APP / "HealthBridgeCompanionViewModel.swift"
AUTOMATIC_SYNC = APP / "AutomaticSyncRuntime.swift"
CONTENT_VIEW = APP / "ContentView.swift"
PBXPROJ = (
    Path("ios/HealthBridgeCompanion")
    / "HealthBridgeCompanion.xcodeproj"
    / "project.pbxproj"
)
CHECK = "syncConsentAdmitsSend"


def _body_after(source: str, signature: str, window: int = 600) -> str:
    start = source.index(signature)
    return source[start : start + window]


def test_consent_view_and_store_files_exist() -> None:
    assert CONSENT_VIEW.is_file()
    assert CONSENT_STORE.is_file()


def test_consent_policy_is_a_pure_may_send_function() -> None:
    source = CONSENT_STORE.read_text(encoding="utf-8")
    assert "enum SyncConsentPolicy" in source
    assert "static func maySend(" in source
    assert "UserDefaults" in source


def test_new_swift_files_are_registered_in_the_xcode_project() -> None:
    project = PBXPROJ.read_text(encoding="utf-8")
    assert "SyncConsentView.swift in Sources" in project
    assert "SyncConsentStore.swift in Sources" in project


def test_view_model_sync_entry_points_check_consent_first() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    for signature in (
        "func syncAllNow()",
        "func flushPendingOutbox()",
        "func checkConnection()",
        "func schedulePendingBackgroundOutboxUploadsIfAllowed()",
        "private func schedulePendingBackgroundOutboxUploadsAndWaitIfAllowed()",
    ):
        assert CHECK in _body_after(source, signature, 500), signature


def test_automatic_sync_runtime_checks_consent_before_enqueuing() -> None:
    source = AUTOMATIC_SYNC.read_text(encoding="utf-8")
    assert f"viewModel.{CHECK}" in source
    for signature in (
        "func runAutomaticSync(",
        "func runManualSync()",
        "func handleBackgroundRefresh()",
    ):
        assert f"viewModel.{CHECK}" in _body_after(source, signature, 500), signature


def test_consent_copy_names_the_disclosures_and_the_health_revocation_path() -> None:
    source = CONSENT_VIEW.read_text(encoding="utf-8")
    assert "AI agent" in source
    assert "paired" in source.lower()
    assert re.search(r"Health app.*Privacy.*Apps.*HealthRelay", source, re.DOTALL)
    assert "disconnect" in source.lower()


def test_settings_has_a_consent_review_row() -> None:
    source = CONTENT_VIEW.read_text(encoding="utf-8")
    assert "SyncConsentReviewView(" in source
