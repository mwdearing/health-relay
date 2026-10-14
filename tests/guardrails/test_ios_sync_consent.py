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


def _function_body(source: str, signature: str, length: int = 1400) -> str:
    start = source.index(signature)
    return source[start : start + length]


def test_withdraw_closes_background_continuation_admission() -> None:
    body = _function_body(
        VIEW_MODEL.read_text(encoding="utf-8"), "func withdrawSyncConsent()"
    )
    assert "#if os(iOS)" in body
    assert "setAutomaticContinuationAdmissionOpen(false)" in body


def test_activate_opens_continuation_admission_only_with_consent() -> None:
    runtime = AUTOMATIC_SYNC.read_text(encoding="utf-8")
    body = _function_body(runtime, "func activateIfReady(", 700)
    opened = body.index("setAutomaticContinuationAdmissionOpen(true)")
    assert "viewModel.syncConsentAdmitsSend" in body[:opened]


def test_upload_and_connection_test_check_consent_before_any_request() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    assert "requireSyncConsent()" in _function_body(
        source, "private func uploadPendingOutbox(", 700
    )
    assert CHECK in _function_body(source, "func sendConnectionTestBatch()", 300)


def test_consent_prompt_can_be_declined_without_locking_out_settings() -> None:
    content = CONTENT_VIEW.read_text(encoding="utf-8")
    view = CONSENT_VIEW.read_text(encoding="utf-8")
    assert "$viewModel.syncConsentPromptIsPresented" in content
    assert "get: { !viewModel.syncConsentAdmitsSend }" not in content
    assert "onNotNow" in view
    assert "Not Now" in view


def test_consent_cancel_is_not_gated_by_user_action_admission() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    consent_cancel = _function_body(
        source, "func cancelBackgroundUploadsForConsent(", 900
    )
    assert "terminalUserActionAdmissionIsOpen" not in consent_cancel
    assert (
        "BackgroundURLSessionOutboxUploader.shared.cancelPendingUploads()"
        in consent_cancel
    )
    withdraw = _function_body(source, "func withdrawSyncConsent()", 1400)
    assert "cancelBackgroundUploadsForConsent()" in withdraw
    bootstrap = _function_body(source, "private func performBootstrap()", 200)
    assert "cancelBackgroundUploadsForConsent()" in bootstrap


def _between(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start + len(start_marker))
    return source[start:end]


def test_upload_loop_rechecks_consent_before_each_send_and_after_each_await() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    upload = _between(
        source,
        "private func uploadPendingOutbox(",
        "private func makeProductionMailboxDelivery()",
    )
    assert upload.count("requireSyncConsent()") >= 4
    loop = upload[upload.index("for item in pendingItems") :]
    send = loop.index("receiverClient.upload(")
    assert "requireSyncConsent()" in loop[:send]
    assert "requireSyncConsent()" in loop[send:]
    mailbox = upload[: upload.index("guard let receiverIdentity")]
    assert mailbox.index("requireSyncConsent()") < mailbox.index("deliverPending()")


def test_mailbox_reconciliation_and_foreground_retry_check_consent() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    foreground = _between(
        source,
        "func runForegroundMailboxReconciliationIfNeeded()",
        "func noteSceneLeftActive()",
    )
    assert CHECK in foreground[:600]
    reconcile = _between(
        source, "private func reconcileMailboxDeliveryIfNeeded(", "private func "
    )
    assert "syncConsentAdmitsSend" in reconcile[:800]
    assert "requireSyncConsent()" in reconcile
    scheduled = _between(
        source,
        "private func schedulePendingBackgroundOutboxUploadsNow(",
        "private func ",
    )
    assert "requireSyncConsent()" in scheduled
