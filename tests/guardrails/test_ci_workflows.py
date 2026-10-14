import re
from pathlib import Path

PYTHON_WORKFLOW = Path(".github/workflows/python.yml")
IOS_WORKFLOW = Path(".github/workflows/ios.yml")
BUILD_WORKFLOW = Path(".github/workflows/build-ipa.yml")
DEPENDABOT_CONFIG = Path(".github/dependabot.yml")
PINNED_ACTION = re.compile(r"^\s*uses:\s*[^#\s]+@(?P<sha>[0-9a-f]{40})(?:\s+#.*)?$")


def test_ci_workflows_use_minimal_permissions_and_immutable_actions() -> None:
    workflows = (PYTHON_WORKFLOW, IOS_WORKFLOW)
    for workflow in workflows:
        text = workflow.read_text()
        assert "permissions:\n  contents: read" in text
        assert "npm" not in text.lower()
        uses_lines = [line for line in text.splitlines() if "uses:" in line]
        assert uses_lines
        assert all(PINNED_ACTION.match(line) for line in uses_lines)


def test_python_ci_runs_all_release_facing_python_gates() -> None:
    text = PYTHON_WORKFLOW.read_text()
    build_command = " ".join(  # noqa: FLY002
        (
            "uv build --build-constraints build-constraints.txt",
            "--require-hashes --out-dir dist",
        )
    )
    assert "runs-on: ubuntu-24.04" in text
    for command in (
        "uv sync --all-extras --dev --locked",
        "uv run python scripts/public-release-audit.py --strict",
        "uv run ruff format --check .",
        "uv run ruff check .",
        "uv run basedpyright",
        "uv run bandit -r src -q",
        "uv run pip-audit --local --skip-editable",
        "uv run pytest -q",
        "rm -rf dist",
        build_command,
        "uv run python scripts/package-smoke.py --dist-dir dist",
    ):
        assert command in text


def test_ios_ci_runs_swift_tests_and_unsigned_simulator_build() -> None:
    text = IOS_WORKFLOW.read_text()
    assert "runs-on: macos-26" in text
    assert "swift test" in text
    assert "xcodebuild -project HealthBridgeCompanion.xcodeproj" in text
    assert "-sdk iphonesimulator" in text
    assert "-sdk iphoneos" in text
    assert "CODE_SIGNING_ALLOWED=NO" in text


def test_dependabot_keeps_security_updates_without_version_prs() -> None:
    text = DEPENDABOT_CONFIG.read_text()

    assert "version: 2" in text
    assert 'package-ecosystem: "pip"' in text
    assert 'package-ecosystem: "github-actions"' in text
    assert text.count('interval: "monthly"') == 2
    assert text.count("open-pull-requests-limit: 0") == 2
    assert "cooldown:" not in text
    assert "groups:" not in text


RETIRED_RELEASE_WORKFLOWS = (
    Path(".github/workflows/publish-ipa-release.yml"),
    Path(".github/workflows/promote-ipa-release.yml"),
)


RELEASE_WRITE_MARKERS = ("gh release", "action-gh-release", "/releases")


def test_unsigned_ipa_release_workflows_stay_retired() -> None:
    # SECURITY.md says GitHub Releases do not carry IPA files (#127): no workflow
    # that handles an IPA may also touch GitHub Releases, in any step.
    for path in RETIRED_RELEASE_WORKFLOWS:
        assert not path.exists(), path
    for path in sorted(Path(".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8").lower()
        if ".ipa" not in text:
            continue
        for marker in RELEASE_WRITE_MARKERS:
            assert marker not in text, f"{path} handles an IPA and uses {marker}"
