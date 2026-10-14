"""First-run sync consent gates every send path in the iOS companion."""

import re
from pathlib import Path

APP = Path("ios/HealthBridgeCompanion/App")
CONSENT_VIEW = APP / "SyncConsentView.swift"
CORE = Path("ios/HealthBridgeCompanion/Sources/HealthBridgeCompanionCore")
IOS = Path("ios/HealthBridgeCompanion")
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


# --- Positive recognition of egress -------------------------------------------------
# Every network or mailbox-publish primitive in App/ and Sources/ must be one of the
# recognised shapes below. An unrecognised egress token fails the test, so a new
# primitive cannot appear without an explicit decision here.

EGRESS_ROOTS = (
    Path("ios/HealthBridgeCompanion/App"),
    Path("ios/HealthBridgeCompanion/Sources"),
)
PERSISTED_CONSENT_CHECK = "requirePersistedSyncConsent("
# Call tokens: the persisted check must appear before the call in its function.
EGRESS_CALL_TOKENS = (
    re.compile(r"\bsession\.data\("),
    re.compile(r"\bsession\.uploadTask\("),
    re.compile(r"\bsession\.dataTask\("),
    re.compile(r"\bpublisher\.publish\("),
)
# Definition tokens: the persisted check must appear inside the function body.
EGRESS_DEFINITION_TOKENS = (
    re.compile(r"\bfunc\s+publishPendingFIFOHead\b"),
    re.compile(r"\bfunc\s+deliverPending\b"),
)
FUNC_DECL = re.compile(r"\bfunc\s+(\w+)")
# Non-health egress, allowed by exact file and function name only.
NON_HEALTH_EGRESS = {
    ("ReceiverClient.swift", "healthCheck"),
    ("ReceiverClient.swift", "redeem"),
}


def _matching(source: str, open_index: int, open_char: str, close_char: str) -> int:
    depth = 0
    for index in range(open_index, len(source)):
        if source[index] == open_char:
            depth += 1
        elif source[index] == close_char:
            depth -= 1
            if depth == 0:
                return index
    message = "unbalanced braces"
    raise AssertionError(message)


def _function_span(source: str, decl: re.Match[str]) -> tuple[int, int] | None:
    paren = source.index("(", decl.end())
    close = _matching(source, paren, "(", ")")
    brace = source.find("{", close)
    if brace == -1:
        return None
    between = source[close:brace]
    if "func " in between or "\n    }" in between or "var " in between:
        return None  # protocol requirement without a body
    return brace, _matching(source, brace, "{", "}")


def _enclosing_function(source: str, index: int) -> tuple[str, int, int] | None:
    best: tuple[str, int, int] | None = None
    for decl in FUNC_DECL.finditer(source):
        span = _function_span(source, decl)
        if span is None or not (span[0] <= index <= span[1]):
            continue
        if best is None or (span[1] - span[0]) < (best[2] - best[1]):
            best = (decl.group(1), span[0], span[1])
    return best


def test_every_egress_primitive_is_recognised_and_persisted_consent_gated() -> None:
    recognised = 0
    failures: list[str] = []
    for root in EGRESS_ROOTS:
        for path in sorted(root.rglob("*.swift")):
            source = path.read_text(encoding="utf-8")
            for pattern in EGRESS_CALL_TOKENS + EGRESS_DEFINITION_TOKENS:
                is_definition = pattern in EGRESS_DEFINITION_TOKENS
                for match in pattern.finditer(source):
                    where = f"{path.name}:{pattern.pattern}"
                    if is_definition:
                        decl = FUNC_DECL.match(source, match.start())
                        span = (
                            _function_span(source, decl) if decl is not None else None
                        )
                        enclosing = (
                            (decl.group(1), *span)
                            if decl is not None and span is not None
                            else None
                        )
                    else:
                        enclosing = _enclosing_function(source, match.start())
                    if enclosing is None:
                        failures.append(f"{where} has no enclosing function")
                        continue
                    name, body_start, body_end = enclosing
                    if (path.name, name) in NON_HEALTH_EGRESS:
                        recognised += 1
                        continue
                    if is_definition:
                        checked = PERSISTED_CONSENT_CHECK in source[body_start:body_end]
                    else:
                        checked = (
                            PERSISTED_CONSENT_CHECK
                            in source[body_start : match.start()]
                        )
                    if checked:
                        recognised += 1
                    else:
                        message = f"{path.name}:{name} lacks {PERSISTED_CONSENT_CHECK}"
                        failures.append(f"{message} before {pattern.pattern}")
    assert not failures, "\n".join(failures)
    assert recognised >= 6, recognised


def _sources_phase_file_names(phase_id: str) -> list[str]:
    project = (IOS / "HealthBridgeCompanion.xcodeproj/project.pbxproj").read_text(
        encoding="utf-8"
    )
    build_file_pattern = r"\t\t(\w+) /\* (\S+) in Sources \*/ = \{isa = PBXBuildFile;"
    build_files: dict[str, str] = {
        str(m.group(1)): str(m.group(2))
        for m in re.finditer(build_file_pattern, project)
    }
    phase_pattern = (
        phase_id
        + r" /\* Sources \*/ = \{isa = PBXSourcesBuildPhase;[^\n]*files = \(([^)]*)\)"
    )
    match = re.search(phase_pattern, project)
    assert match is not None, phase_id
    ids = [str(m.group(1)) for m in re.finditer(r"(\w+) /\*", match.group(1))]
    return [build_files[i] for i in ids]


def test_every_target_that_compiles_a_consent_user_also_compiles_the_store() -> None:
    swift_files = {
        path.name: path
        for root in (IOS / "App", IOS / "Sources/HealthBridgeCompanionCore")
        for path in root.rglob("*.swift")
    }
    for phase_id in (
        "B0193229F5D633567D732C36",
        "A25174000000000000000001",
        "A27174000000000000000001",
    ):
        names = _sources_phase_file_names(phase_id)
        users = [
            name
            for name in names
            if name in swift_files
            and name != "SyncConsentStore.swift"
            and re.search(
                r"SyncConsentStore|requirePersistedSyncConsent|SyncConsentRefusal",
                swift_files[name].read_text(encoding="utf-8"),
            )
        ]
        if users:
            assert "SyncConsentStore.swift" in names, (phase_id, users)


def test_app_tests_build_receiver_clients_with_an_accepted_store() -> None:
    app_tests = IOS / "Tests/HealthBridgeCompanionAppTests"
    tests = (app_tests / "HealthBridgeCompanionResetAdmissionTests.swift").read_text(
        encoding="utf-8"
    )
    raw = re.findall(r"(?<![A-Za-z])ReceiverClient\(", tests)
    assert len(raw) == 1, "only admittingReceiverClient may construct a ReceiverClient"
    assert "func admittingReceiverClient(" in tests
