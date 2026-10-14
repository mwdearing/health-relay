import re
from pathlib import Path
from typing import cast

import yaml

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


def _permission_blocks(workflow: dict[object, object]) -> list[object]:
    blocks: list[object] = []
    if "permissions" in workflow:
        blocks.append(workflow["permissions"])
    jobs = cast("dict[object, object]", workflow.get("jobs") or {})
    job_maps = [
        cast("dict[object, object]", job)
        for job in jobs.values()
        if isinstance(job, dict)
    ]
    blocks.extend(job["permissions"] for job in job_maps if "permissions" in job)
    return blocks


def _write_scopes(block: object, name: str) -> set[str]:
    """Scopes a permissions value grants write on; anything unrecognised fails."""
    if block is None:
        return set()
    if isinstance(block, str):
        assert block.strip().lower() == "read-all", f"{name}: permissions {block!r}"
        return set()
    assert isinstance(block, dict), f"{name}: permissions {block!r}"
    scopes: set[str] = set()
    for scope, value in cast("dict[object, object]", block).items():
        level = str(value).strip().strip("'\"").lower()
        assert level in {"read", "write", "none"}, f"{name}: {scope}: {value!r}"
        if level == "write":
            scopes.add(str(scope).strip().lower())
    return scopes


def test_only_the_receiver_release_pipeline_can_write_releases() -> None:
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        name = path.name
        loaded = cast("object", yaml.safe_load(text))
        assert isinstance(loaded, dict), name
        workflow = cast("dict[object, object]", loaded)
        # A workflow without top-level permissions gets the repository default.
        assert "permissions" in workflow, name
        assert "write-all" not in text.lower(), name
        scopes: set[str] = set()
        for block in _permission_blocks(workflow):
            scopes |= _write_scopes(block, name)
        allowed = ALLOWED_WRITE_SCOPES.get(name, frozenset())
        assert scopes <= allowed, f"{name} write scopes {sorted(scopes - allowed)}"
        # Every expression GitHub evaluates (comments dropped): secrets only by plain
        # name and only allowlisted ones, however the expression is written.
        evaluated = " ".join(_yaml_strings(workflow))
        expressions = " ".join(_expressions(workflow))
        assert len(re.findall(r"\bsecrets\b", expressions)) == len(
            SECRET_REF.findall(expressions)
        ), f"{name} uses secrets other than secrets.<NAME>"
        assert "secrets" not in _yaml_keys(workflow), f"{name} passes secrets on"
        secrets = {match["name"] for match in SECRET_REF.finditer(expressions)}
        allowed_secrets = ALLOWED_SECRETS.get(name, frozenset())
        assert secrets <= allowed_secrets, (
            f"{name} secrets {sorted(secrets - allowed_secrets)}"
        )
        if name in RELEASE_WRITERS:
            assert not IPA_TOKEN.search(evaluated), (
                f"{name} can write releases and mentions an IPA"
            )


def _yaml_strings(node: object) -> list[str]:
    """Every key and scalar in a parsed workflow, as text."""
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in cast("dict[object, object]", node).items():
            out.append(str(key))
            out.extend(_yaml_strings(value))
        return out
    if isinstance(node, list):
        return [
            text for item in cast("list[object]", node) for text in _yaml_strings(item)
        ]
    return [str(node)]


def _yaml_keys(node: object) -> set[str]:
    if isinstance(node, dict):
        mapping = cast("dict[object, object]", node)
        keys = {str(key) for key in mapping}
        for value in mapping.values():
            keys |= _yaml_keys(value)
        return keys
    if isinstance(node, list):
        return {key for item in cast("list[object]", node) for key in _yaml_keys(item)}
    return set()


def _template_expressions(text: str) -> list[str]:
    """Bodies of ${{ }} in a string, honouring single-quoted expression literals."""
    bodies: list[str] = []
    start = text.find("${{")
    while start != -1:
        index, quoted = start + 3, False
        while index < len(text):
            if text[index] == "'":
                quoted = not quoted
            elif not quoted and text.startswith("}}", index):
                break
            index += 1
        bodies.append(text[start + 3 : index])
        start = text.find("${{", index)
    return bodies


def _expressions(node: object) -> list[str]:
    """Every expression a workflow evaluates: ${{ }} bodies and bare `if:` values."""
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in cast("dict[object, object]", node).items():
            if key == "if" and isinstance(value, str):
                out.append(value)
            out.extend(_expressions(value))
    elif isinstance(node, list):
        for item in cast("list[object]", node):
            out.extend(_expressions(item))
    elif isinstance(node, str):
        out.extend(_template_expressions(node))
    return out


IPA_TOKEN = re.compile(r"(?<![a-z])ipa(?![a-z])", re.IGNORECASE)


def test_secret_and_ipa_checks_see_through_expression_tricks() -> None:
    text = (
        "env:\n"
        "  T: ${{ format('}}') && secrets.RELEASE_PAT }}\n"
        "  U: ${{ secrets['X'] }}\n"
        "  V: the signing secrets are masked\n"
    )
    found = " ".join(_expressions(cast("object", yaml.safe_load(text))))
    assert {m["name"] for m in SECRET_REF.finditer(found)} == {"RELEASE_PAT"}
    assert len(re.findall(r"\bsecrets\b", found)) == 2
    assert IPA_TOKEN.search("dist/HealthRelay_ipa.zip")
    assert not IPA_TOKEN.search("participate")


def test_write_scope_parser_recognises_yaml_variants() -> None:
    for text in (
        "permissions: {contents: write}",
        "permissions:\n  contents: 'write'",
        'permissions:\n  "contents": write',
        "permissions:\n  contents : WRITE",
    ):
        loaded = cast("dict[str, object]", yaml.safe_load(text))
        assert _write_scopes(loaded["permissions"], "probe") == {"contents"}, text


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
