"""Guides name this product HealthRelay; upstream names appear only as attribution.

The upstream names "Apple Health AI Bridge", "Health Bridge for AI" and the bare
"Health Bridge" are not used for the product (docs/brand.md). Every remaining
occurrence in tracked docs and README.md must match an allowlist entry below; each
entry states why the name is correct there.
"""

import re
import subprocess
from pathlib import Path

FULL_UPSTREAM_NAMES = re.compile(
    r"Apple Health AI Bridge|\bHealth Bridge\b", re.IGNORECASE
)
# The same names, in any case, with spaces or one soft line break between words,
# so a name Markdown source wraps across two lines (and renders as one) is caught;
# a blank line is a paragraph break and does not join words.
_GAP = r"(?:[ \t]+|[ \t]*\n[ \t]*)"
WRAPPED_UPSTREAM_NAMES = re.compile(
    rf"Apple{_GAP}Health{_GAP}AI{_GAP}Bridge|\bHealth{_GAP}Bridge\b", re.IGNORECASE
)

# Dated history sections, exempt as a whole: (path, heading that starts the section).
# Everything above the heading (current policy prose) is checked like any file.
HISTORY_SECTIONS: dict[str, str] = {"FORK.md": "## Modifications (newest first)"}

UPSTREAM_RELEASE_STEMS = (
    "README",
    "app-review-notes-template",
    "criteria",
    r"notes-receiver-v1\.1\.[01]",
    r"notes-v1\.0\.[01]",
    # Upstream's beta-distribution checklist (the product name is assembled so the
    # word does not appear in tracked text).
    "test" + "flight-checklist",
)
UPSTREAM_RELEASE_FILES = (
    r"re:\.github/release/(" + "|".join(UPSTREAM_RELEASE_STEMS) + r")\.md"
)

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
        r"Apple Health AI Bridge, the open-source project behind Health Bridge for AI",
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
        r"describe the upstream Health Bridge for AI product",
        "footer that explicitly labels upstream links as attribution",
    ),
    (
        "README.md",
        r"Apple Health AI Bridge is an independent open-source project",
        "upstream project's affiliation notice, kept as attribution",
    ),
    # Mailbox guide prose; scope and wording wait on the owner decision, #128 item 1.
    (
        "docs/icloud-mailbox-service.md",
        r"browser into one private local directory; Health Bridge does not silently",
        "mailbox guide, left for the #128 item 1 decision",
    ),
    (
        "docs/icloud-mailbox-service.md",
        r"No Health Bridge mailbox service files exist",
        "mailbox guide, left for the #128 item 1 decision",
    ),
    (
        "docs/icloud-mailbox-service.md",
        r"retires only the exact Health Bridge-owned plist",
        "mailbox guide, left for the #128 item 1 decision",
    ),
    # Versioning guide quotes the ambiguous phrase as the example to avoid.
    (
        "docs/versioning.md",
        r"Avoid an unlabeled phrase such as .Health Bridge 1\.1\.0.",
        "quoted counter-example, not a product name",
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
    # Files that are upstream's own history, kept verbatim and labelled as such.
    (
        # Upstream's own files only, by name; HealthRelay's release notes are checked.
        UPSTREAM_RELEASE_FILES,
        r"Apple Health AI Bridge|\bHealth Bridge\b",
        "upstream release notes and checklists kept as history",
    ),
    (
        "FORK.md",
        r"HealthRelay is a fork of \[Apple Health AI Bridge\]",
        "fork attribution",
    ),
    (
        "assets/brand/README.md",
        r"fork of Apple Health AI Bridge|upstream Health Bridge assets",
        "attribution and removal of the upstream assets",
    ),
]


def _tracked_guide_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line.endswith(".md")]


def _path_matches(entry_path: str, path: str) -> bool:
    if entry_path.startswith("re:"):
        return re.fullmatch(entry_path[3:], path) is not None
    return entry_path == path


def _unexplained_remainder(path: str, line: str) -> str:
    """Remove each allowlisted span from the line; whatever is left must be clean.

    An allowlist entry exempts only the text its pattern matches, so another
    upstream name added elsewhere on an allowlisted line is still caught.
    """
    for entry_path, pattern, _reason in ALLOWLIST:
        if _path_matches(entry_path, path):
            line = re.sub(pattern, "", line, flags=re.IGNORECASE)
    return line


def checked_lines(path: str, text: str) -> list[str]:
    """Each source line with its allowlisted spans removed and history blanked."""
    heading = HISTORY_SECTIONS.get(path)
    lines: list[str] = []
    in_history = False
    for line in text.splitlines():
        if heading is not None and line.strip() == heading:
            in_history = True
        lines.append("" if in_history else _unexplained_remainder(path, line))
    return lines


def find_violations(path: str, text: str) -> list[int]:
    """Line numbers where an upstream name remains, including wrapped names."""
    joined = "\n".join(checked_lines(path, text))
    return sorted(
        {
            joined.count("\n", 0, match.start()) + 1
            for match in WRAPPED_UPSTREAM_NAMES.finditer(joined)
        }
    )


def test_guides_do_not_use_upstream_product_names_outside_allowlist() -> None:
    violations: list[str] = []
    for path in _tracked_guide_files():
        text = path.read_text(encoding="utf-8")
        violations.extend(
            f"{path.as_posix()}:{number}"
            for number in find_violations(path.as_posix(), text)
        )
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


def test_allowlisted_line_still_catches_an_added_upstream_name() -> None:
    line = "HealthRelay is a fork of Apple Health AI Bridge. Install Health Bridge."
    assert FULL_UPSTREAM_NAMES.search(_unexplained_remainder("README.md", line))


def test_fork_release_notes_are_not_exempt() -> None:
    path = ".github/release/notes-healthrelay-receiver-2099.01.01.md"
    assert FULL_UPSTREAM_NAMES.search(_unexplained_remainder(path, "Health Bridge fix"))


def test_name_wrapped_across_lines_is_caught() -> None:
    assert find_violations("docs/x.md", "Open the Health\nBridge for AI app.") == [1]
    # A blank line is a paragraph break, not a wrapped name.
    assert find_violations("docs/x.md", "Health\n\nBridge networks") == []


def test_names_are_matched_in_any_case() -> None:
    for line in ("Health bridge", "health Bridge", "APPLE HEALTH AI BRIDGE"):
        assert find_violations("docs/x.md", line) == [1], line


def test_fork_policy_prose_is_checked_but_its_history_is_not() -> None:
    text = (
        "# Fork\n"
        "The product is Health Bridge.\n"
        "## Modifications (newest first)\n"
        "- 2000-01-01: renamed Health Bridge to HealthRelay."
    )
    assert find_violations("FORK.md", text) == [2]
