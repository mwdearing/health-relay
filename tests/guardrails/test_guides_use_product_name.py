"""Guides name this product HealthRelay; upstream names appear only as attribution.

The upstream names "Apple Health AI Bridge" and "Health Bridge for AI" are not used for
the product (docs/brand.md). Every remaining occurrence in tracked docs and README.md
must match an allowlist entry below; each entry states why the name is correct there.
"""

import re
import subprocess
from pathlib import Path

FULL_UPSTREAM_NAMES = re.compile(r"Apple Health AI Bridge|Health Bridge for AI")

# (path, line-regex, reason). The line-regex is searched in the line text.
ALLOWLIST: list[tuple[str, str, str]] = [
    # README attribution and fork relationship.
    (
        "README.md",
        r"HealthRelay is a fork of Apple Health AI Bridge",
        "fork attribution",
    ),
    (
        "README.md",
        r"builds on Apple Health AI Bridge, the open-source project behind",
        "upstream credit",
    ),
    (
        "README.md",
        r"the upstream product \(Health Bridge for AI, on the App Store\)",
        "distinguishes the upstream app from HealthRelay",
    ),
    (
        "README.md",
        r"If you previously used the upstream Health Bridge for AI app",
        "instruction about the upstream app, not this product",
    ),
    (
        "README.md",
        r"Upstream project \(attribution",
        "footer that explicitly labels upstream links as attribution",
    ),
    (
        "README.md",
        r"Apple Health AI Bridge is an independent open-source project",
        "upstream project's affiliation notice, kept as attribution",
    ),
    # Brand rule itself.
    (
        "docs/brand.md",
        r"HealthRelay is a fork of Apple Health AI Bridge",
        "fork attribution in the brand rule",
    ),
    (
        "docs/brand.md",
        r'"Apple Health AI Bridge", "Health Bridge for AI" and "Health Bridge"',
        "the brand rule quotes the forbidden names",
    ),
    # Upstream release, distribution and roadmap history (not HealthRelay's process).
    (
        "docs/icloud-mailbox-service.md",
        r"published by the upstream Apple Health AI Bridge project",
        "attribution: upstream publishes these assets (mailbox text is out of scope)",
    ),
    (
        "docs/maintainer-guide.md",
        r"inherited from upstream Apple Health AI Bridge",
        "states the guide is upstream's and not used by HealthRelay",
    ),
    (
        "docs/maintainer-guide.md",
        r"public triage, merge, and release process for Apple Health AI Bridge",
        "describes the upstream project's process, not HealthRelay's",
    ),
    (
        "docs/maintainers/README.md",
        r"describe the upstream Apple Health AI Bridge release process",
        "states the directory describes upstream's release process",
    ),
    (
        "docs/maintainers/app-review-notes-template.example.md",
        r"^Apple Health AI Bridge is a local-first companion",
        "upstream App Store review template, not used by HealthRelay",
    ),
    (
        # "re:" marks a path regex. The file is an upstream release checklist.
        r"re:docs/maintainers/[a-z]+-readiness\.md",
        r"prepare an official \w+ beta for Apple Health AI Bridge",
        "upstream release checklist, not used by HealthRelay",
    ),
    (
        "docs/roadmap.md",
        r"^# Apple Health AI Bridge Roadmap",
        "title of the upstream roadmap kept as history",
    ),
    (
        "docs/roadmap.md",
        r"HealthRelay is a fork of Apple Health AI Bridge",
        "fork attribution",
    ),
    (
        "docs/roadmap.md",
        r"The sections below are the upstream Apple Health AI Bridge roadmap",
        "labels the sections as upstream history",
    ),
    (
        "docs/upstream-sync.md",
        r"^# Syncing from upstream Apple Health AI Bridge",
        "the procedure syncs from upstream",
    ),
    (
        "docs/upstream-sync.md",
        r"contain the strings `Health Bridge for AI`",
        "quotes the forbidden string inside a guard description",
    ),
    (
        "docs/upstream-sync.md",
        r"requires `HealthRelay is a fork of Apple Health AI Bridge`",
        "fork attribution required by the guard",
    ),
    (
        "docs/versioning.md",
        r"describe Apple Health AI Bridge's own process",
        "describes upstream's release process, distinguished from HealthRelay's",
    ),
]


def _tracked_guide_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "docs", "README.md"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line.endswith(".md")]


def _path_matches(entry_path: str, path: str) -> bool:
    if entry_path.startswith("re:"):
        return re.fullmatch(entry_path[3:], path) is not None
    return entry_path == path


def _is_allowed(path: str, line: str) -> bool:
    return any(
        _path_matches(entry_path, path) and re.search(pattern, line)
        for entry_path, pattern, _reason in ALLOWLIST
    )


def test_guides_do_not_use_upstream_product_names_outside_allowlist() -> None:
    violations: list[str] = []
    for path in _tracked_guide_files():
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if FULL_UPSTREAM_NAMES.search(line) and not _is_allowed(
                path.as_posix(), line
            ):
                violations.append(f"{path.as_posix()}:{number}: {line.strip()}")
    assert violations == [], "upstream product name outside allowlist:\n" + "\n".join(
        violations
    )


def test_allowlist_entries_still_match_a_line() -> None:
    files = _tracked_guide_files()
    for entry_path, pattern, reason in ALLOWLIST:
        assert reason.strip(), f"allowlist entry for {entry_path} needs a reason"
        matched = any(
            _path_matches(entry_path, path.as_posix())
            and any(
                re.search(pattern, line)
                for line in path.read_text(encoding="utf-8").splitlines()
            )
            for path in files
        )
        assert matched, f"stale allowlist entry: {entry_path} /{pattern}/"
