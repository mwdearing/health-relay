"""Guard: the public repository never quotes a named person's private feedback.

Code comments and docs must describe technical facts only. The only places the
name may appear are the explicit attribution lines in ALLOWLIST below.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]

# Built from pieces so this file does not trip its own guard.
PERSON_NAME = "Mich" + "ael"
NAME_PATTERN = re.compile(r"\b" + PERSON_NAME + r"\b")

# (path, exact-line regex) pairs for legitimate attribution. Nothing else is exempt.
ALLOWLIST: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        # Copyright holder line of the project NOTICE file, required by Apache-2.0.
        "NOTICE",
        re.compile(r"^Copyright \(c\) 2026 " + PERSON_NAME + r" Dearing$"),
    ),
    (
        # Mirror of the NOTICE copyright line in the About screen text; a guardrail
        # test requires the two to stay identical.
        "ios/HealthBridgeCompanion/App/ContentView.swift",
        re.compile(r"^\s*Copyright \(c\) 2026 " + PERSON_NAME + r" Dearing$"),
    ),
)

# Files that are not scanned at all: the lock file is generated and has no prose.
SKIPPED_PATHS = frozenset({"uv.lock"})


def _is_allowlisted(path: str, line: str) -> bool:
    return any(
        path == allowed_path and pattern.search(line) is not None
        for allowed_path, pattern in ALLOWLIST
    )


def find_offending_lines(path: str, text: str) -> list[tuple[int, str]]:
    """Return (line number, line) for each non-allowlisted line naming the person."""
    offenders: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if NAME_PATTERN.search(line) and not _is_allowlisted(path, line):
            offenders.append((number, line))
    return offenders


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [item for item in result.stdout.decode("utf-8").split("\0") if item]


def _decode_text(path: str) -> str | None:
    data = (ROOT / path).read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def test_public_repository_names_no_private_feedback_source() -> None:
    # Given / When
    offenders: list[str] = []
    for path in _tracked_files():
        if path in SKIPPED_PATHS:
            continue
        text = _decode_text(path)
        if text is None:
            continue
        for number, line in find_offending_lines(path, text):
            offenders.append(f"{path}:{number}: {line.strip()}")

    # Then
    assert offenders == []


def test_guard_flags_private_feedback_shapes_and_allows_attribution() -> None:
    # Given synthetic lines in each shape that quotes a person's feedback
    shapes = (
        f'// ({PERSON_NAME}, 2026-09-29: "Cancel never shows up")',
        f"// {PERSON_NAME}: the button does nothing",
        f"// (from {PERSON_NAME})",
        f"// the bug {PERSON_NAME} saw on the phone",
        f"// {PERSON_NAME} 2026-09-28 reported the version did not change",
        f"Thanks, {PERSON_NAME}, for the report",
    )

    # When / Then every shape is flagged
    for line in shapes:
        assert find_offending_lines("src/example.py", line) == [(1, line)], line

    # The allowlisted NOTICE copyright line is not flagged ...
    notice_line = "Copyright (c) 2026 " + PERSON_NAME + " Dearing"
    assert find_offending_lines("NOTICE", notice_line) == []

    # ... but the same line in any other file, or another line in NOTICE, is.
    assert find_offending_lines("docs/other.md", notice_line) == [(1, notice_line)]
    assert find_offending_lines("NOTICE", "Written by " + PERSON_NAME) == [
        (1, "Written by " + PERSON_NAME)
    ]
