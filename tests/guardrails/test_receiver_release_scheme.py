"""Guardrails for the pinned receiver release scheme.

A pinned receiver release is identified by a `healthrelay-receiver-<date>` tag on
an immutable commit, never by a package version bump, and that tag must not
trigger any upstream release workflow. These tests are stdlib only and read
files under the repository root.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
TAG = "healthrelay-receiver-2026.10.04"
NOTES = ROOT / ".github/release/notes-healthrelay-receiver-2026.10.04.md"
VERSIONING = ROOT / "docs/versioning.md"
WORKFLOWS = ROOT / ".github/workflows"
PACKAGE_VERSION = "1.1.1"


def tag_globs(text: str) -> list[str]:
    """Return every tag filter a workflow declares, inline or as a list."""
    globs: list[str] = []
    for inline_match in re.finditer(r"tags:\s*\[([^\]]*)\]", text):
        inline = inline_match.group(1)
        globs += [
            item.strip().strip("\"'") for item in inline.split(",") if item.strip()
        ]
    for block_match in re.finditer(r"tags:\s*\n((?:[ \t]*-[ \t]*\S.*\n)+)", text):
        block = block_match.group(1)
        globs += [
            line.split("-", 1)[1].strip().strip("\"'")
            for line in block.splitlines()
            if line.strip()
        ]
    return globs


def test_no_workflow_tag_trigger_matches_the_receiver_release_tag() -> None:
    workflows = sorted(WORKFLOWS.glob("*.y*ml"))
    assert workflows, "no workflows found; the tag filter parser must stay honest"

    triggers = {
        path.name: tag_globs(path.read_text(encoding="utf-8")) for path in workflows
    }
    assert any(triggers.values()), "no workflow declares a tag filter; parser broken"

    offenders = sorted(
        f"{name}: {glob}"
        for name, globs in triggers.items()
        for glob in globs
        if fnmatch.fnmatch(TAG, glob)
    )
    assert not offenders, (
        f"{TAG} must not trigger any workflow release; filters: {offenders}"
    )


def test_release_notes_name_the_tag_install_command_and_intake_tool() -> None:
    assert NOTES.is_file(), f"missing release notes: {NOTES.relative_to(ROOT)}"

    notes = NOTES.read_text(encoding="utf-8")
    assert TAG in notes
    install = (
        f'uv tool install "git+https://github.com/mwdearing/health-relay.git@{TAG}"'
    )
    assert install in notes, "release notes must teach the pinned install command"
    assert "get_intake_evidence_v1" in notes
    assert "--enable-intake-context" in notes
    assert "health_bridge.batch.v1" in notes
    assert re.search(r"\b[0-9a-f]{7,40}\b", notes) is not None, "notes name the commit"


def test_versioning_documents_the_scheme_without_a_version_bump() -> None:
    versioning = VERSIONING.read_text(encoding="utf-8")
    assert "healthrelay-receiver-" in versioning
    assert "Latest" in versioning
    assert "Mailbox" in versioning
    pinned = (
        'uv tool install "git+https://github.com/mwdearing/health-relay.git'
        "@healthrelay-receiver-"
    )
    assert pinned in versioning
    assert "The receiver has no separate release step" not in versioning
    assert "receiver-v*" in versioning
    assert "ios-v*" in versioning

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = cast("dict[str, object]", tomllib.loads(pyproject)["project"])
    assert project["version"] == PACKAGE_VERSION

    init = (ROOT / "src/health_bridge/__init__.py").read_text(encoding="utf-8")
    assert f'__version__: Final = "{PACKAGE_VERSION}"' in init
