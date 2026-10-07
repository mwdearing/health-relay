"""Guardrails for the manual signed-upload workflow.

The workflow signs with an App Store Connect API key held in a protected GitHub
Environment. The repository is public, so what keeps the key safe is the shape of
the workflow: it can start only by hand, it needs a reviewer's approval, it never
reads secrets outside its own steps, and it never writes a secret to a log or an
artifact.
"""

import re
from pathlib import Path
from typing import cast

import yaml

WORKFLOW = Path(".github/workflows/signed-beta.yml")
SECRET_NAMES = ("ASC_KEY_P8", "ASC_KEY_ID", "ASC_ISSUER_ID", "APPLE_TEAM_ID")
PINNED_ACTION = re.compile(r"^\s*uses:\s*[^#\s]+@(?P<sha>[0-9a-f]{40})(?:\s+#.*)?$")


def _load() -> dict[str, object]:
    raw = cast("dict[object, object]", yaml.safe_load(WORKFLOW.read_text()))
    # YAML 1.1 reads the bare key `on` as True; give it its real name.
    return {("on" if key is True else str(key)): value for key, value in raw.items()}


def _jobs() -> dict[str, dict[str, object]]:
    return cast("dict[str, dict[str, object]]", _load()["jobs"])


def _steps() -> list[dict[str, object]]:
    (job,) = _jobs().values()
    return cast("list[dict[str, object]]", job["steps"])


def test_it_starts_only_by_hand() -> None:
    triggers = _load()["on"]
    assert isinstance(triggers, dict)
    assert set(cast("dict[str, object]", triggers)) == {"workflow_dispatch"}


def test_it_runs_in_the_protected_environment_with_read_only_permissions() -> None:
    (job,) = _jobs().values()
    assert job["environment"] == "testflight"
    assert _load()["permissions"] == {"contents": "read"}
    assert "permissions" not in job or job["permissions"] == {"contents": "read"}


def test_it_runs_on_a_hosted_macos_runner_and_pins_every_action() -> None:
    (job,) = _jobs().values()
    assert str(job["runs-on"]).startswith("macos-")
    text = WORKFLOW.read_text()
    uses_lines = [line for line in text.splitlines() if "uses:" in line]
    assert uses_lines
    assert all(PINNED_ACTION.match(line) for line in uses_lines)
    checkout = next(s for s in _steps() if "actions/checkout" in str(s.get("uses")))
    assert cast("dict[str, object]", checkout["with"])["persist-credentials"] is False


def test_secrets_are_read_only_through_step_environments() -> None:
    text = WORKFLOW.read_text()
    for name in SECRET_NAMES:
        assert f"secrets.{name}" in text
        for line in text.splitlines():
            if f"secrets.{name}" in line:
                assert re.match(
                    rf"^\s+{name}: \$\{{\{{ secrets\.{name} \}}\}}\s*$", line
                )
    for step in _steps():
        run = str(step.get("run", ""))
        assert "secrets." not in run
        references = cast("list[str]", re.findall(r"\$\{\{(.*?)\}\}", run))
        assert all(reference.strip().startswith("github.") for reference in references)


def test_no_step_prints_or_uploads_a_secret() -> None:
    text = WORKFLOW.read_text()
    assert "set -x" not in text
    assert "actions/upload-artifact" not in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("echo ", "printf ", "cat ")) and "$ASC_KEY_P8" in line:
            assert ">" in line  # only ever redirected into the key file
    for name in SECRET_NAMES:
        assert not re.search(
            rf"echo[^\n]*\$\{{?{name}\}}?(?!\w)[^\n>|]*$", text, re.MULTILINE
        )


def test_the_key_file_is_private_and_always_removed() -> None:
    steps = _steps()
    write_step = next(s for s in steps if "ASC_KEY_P8" in str(s.get("env", {})))
    run = str(write_step["run"])
    assert "umask 077" in run
    cleanup = steps[-1]
    assert cleanup.get("if") == "${{ always() }}"
    assert "rm -f" in str(cleanup["run"])


def test_it_checks_the_secret_formats_without_printing_them() -> None:
    check = next(s for s in _steps() if "Check the signing secrets" in str(s["name"]))
    run = str(check["run"])
    assert "BEGIN PRIVATE KEY" in run
    assert "{10}" in run  # Team ID and Key ID are ten characters
    assert "[0-9a-fA-F]{8}-" in run  # the Issuer ID is a UUID
    assert "echo" not in run.replace('echo "::error::', "")


def test_it_signs_with_the_api_key_and_uploads_directly() -> None:
    text = WORKFLOW.read_text()
    for needle in (
        "xcodebuild archive",
        "-allowProvisioningUpdates",
        "-authenticationKeyPath",
        "-authenticationKeyID",
        "-authenticationKeyIssuerID",
        "-exportArchive",
        "<key>destination</key>",
        "<string>upload</string>",
        "<key>method</key>",
        "app-store-connect",
        "CURRENT_PROJECT_VERSION=$GITHUB_RUN_NUMBER",
    ):
        assert needle in text
    assert "CODE_SIGNING_ALLOWED=NO" not in text


def test_the_bundle_id_is_an_input_and_no_identity_is_written_into_the_file() -> None:
    text = WORKFLOW.read_text()
    inputs = cast(
        "dict[str, object]",
        cast("dict[str, object]", _load()["on"])["workflow_dispatch"],
    )
    assert "bundle_id" in cast("dict[str, object]", inputs["inputs"])
    assert "com.mwdearing" not in text
    assert not re.search(
        r"\b[A-Z0-9]{10}\b(?<![A-Z]{10})", re.sub(r"[A-Z_]{3,}", "", text)
    )
