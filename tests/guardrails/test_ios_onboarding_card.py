"""The getting-started card disappears once setup is done.

Optional steps never hold it open.
"""

from pathlib import Path

IOS = Path("ios/HealthBridgeCompanion")
CONTENT_VIEW = IOS / "App/ContentView.swift"
VIEW_MODEL = IOS / "App/HealthBridgeCompanionViewModel.swift"
UX_STATE = IOS / "Sources/HealthBridgeCompanionCore/CompanionUXState.swift"


def test_setup_completion_ignores_optional_steps() -> None:
    source = UX_STATE.read_text(encoding="utf-8")
    marker = "static func setupIsComplete("
    assert marker in source
    body = source.split(marker, 1)[1].split("\n    }", 1)[0]
    assert ".complete" in body
    assert ".optional" in body


def test_view_model_exposes_setup_completion() -> None:
    source = VIEW_MODEL.read_text(encoding="utf-8")
    assert "var onboardingIsComplete: Bool" in source
    assert "CompanionOnboardingPresentation.setupIsComplete(onboardingSteps)" in source


def test_getting_started_card_is_hidden_once_setup_is_complete() -> None:
    source = CONTENT_VIEW.read_text(encoding="utf-8")
    body = source.split("var body: some View {", 1)[1].split("private var ", 1)[0]
    assert "if !viewModel.onboardingIsComplete {" in body
    gated = body.split("if !viewModel.onboardingIsComplete {", 1)[1].split("}", 1)[0]
    assert "firstRunProgress" in gated
    assert body.count("firstRunProgress") == 1
