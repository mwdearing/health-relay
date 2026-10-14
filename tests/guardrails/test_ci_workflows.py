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


WORKFLOWS_DIR = Path(".github/workflows")
WRITE_SCOPE = re.compile(r"^\s*(?P<scope>[a-z-]+):\s*write\b", re.MULTILINE)
SECRET_REF = re.compile(r"secrets\.(?P<name>[A-Za-z0-9_]+)")
# Every write scope a workflow token may hold. Writing a GitHub Release needs
# `contents: write`, which only the signed receiver release pipeline has, so no
# other workflow can attach an IPA to a release or promote one to Latest (#127).
ALLOWED_WRITE_SCOPES: dict[str, frozenset[str]] = {
    "codeql.yml": frozenset({"security-events"}),
    "publish-release.yml": frozenset({"contents", "id-token", "attestations"}),
    "release.yml": frozenset({"contents", "id-token", "attestations"}),
}
RELEASE_WRITERS = frozenset(
    name for name, scopes in ALLOWED_WRITE_SCOPES.items() if "contents" in scopes
)
# Repository secrets a workflow may read. None is a GitHub token, so the only token
# that could write a release is the job token bounded above.
ALLOWED_SECRETS: dict[str, frozenset[str]] = {
    "signed-beta.yml": frozenset(
        {
            "APPLE_TEAM_ID",
            "ASC_ISSUER_ID",
            "ASC_KEY_ID",
            "ASC_KEY_P8",
            "BUNDLE_ID",
            "SIGNING_CERT_P12_BASE64",
            "SIGNING_CERT_PASSWORD",
        }
    ),
}


def _workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS_DIR.glob("*.y*ml"))
    assert files, "no workflow files found; run from the repository root"
    return files


def test_unsigned_ipa_release_workflows_stay_retired() -> None:
    for path in RETIRED_RELEASE_WORKFLOWS:
        assert not path.exists(), path


def test_only_the_receiver_release_pipeline_can_write_releases() -> None:
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        name = path.name
        # A workflow without top-level permissions gets the repository default.
        assert re.search(r"^permissions:", text, re.MULTILINE), name
        assert "write-all" not in text, name
        scopes = {match["scope"] for match in WRITE_SCOPE.finditer(text)}
        allowed = ALLOWED_WRITE_SCOPES.get(name, frozenset())
        assert scopes <= allowed, f"{name} write scopes {sorted(scopes - allowed)}"
        secrets = {match["name"] for match in SECRET_REF.finditer(text)}
        allowed_secrets = ALLOWED_SECRETS.get(name, frozenset())
        assert secrets <= allowed_secrets, (
            f"{name} secrets {sorted(secrets - allowed_secrets)}"
        )
        if name in RELEASE_WRITERS:
            assert "ipa" not in re.findall(r"\.(\w+)\b", text.lower()), (
                f"{name} can write releases and handles an IPA"
            )


def test_build_ipa_workflow_stays_manual_and_read_only() -> None:
    text = BUILD_WORKFLOW.read_text(encoding="utf-8")
    lines = text.splitlines()
    on_index = lines.index("on:")
    triggers: list[str] = []
    for line in lines[on_index + 1 :]:
        if line and not line.startswith(" "):
            break
        if re.match(r"^  \S", line):
            triggers.append(line.strip())
    assert triggers == ["workflow_dispatch:"]
    perm_index = lines.index("permissions:")
    block: list[str] = []
    for line in lines[perm_index + 1 :]:
        if not line.startswith(" "):
            break
        block.append(line.strip())
    assert [line for line in block if line] == ["contents: read"]
    # No job-level permissions widen it.
    assert text.count("permissions:") == 1
