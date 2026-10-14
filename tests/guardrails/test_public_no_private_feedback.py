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
# Case-insensitive and matched anywhere in a word, so account-style identifiers
# (an underscore or a following capital) are caught too.
NAME_PATTERN = re.compile(PERSON_NAME, re.IGNORECASE)

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


UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _decode_text(path: str) -> str | None:
    return decode_tracked_bytes((ROOT / path).read_bytes())


def decode_tracked_bytes(data: bytes) -> str | None:
    """Decode a tracked file as text, or return None for binary content."""
    # UTF-16 text has NUL bytes but is still text: decode it rather than skip it.
    encoding = "utf-16" if data.startswith(UTF16_BOMS) else "utf-8"
    if encoding == "utf-8" and b"\0" in data:
        return None
    try:
        return data.decode(encoding)
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
        # Report locations only: the offending line may carry the private text itself.
        offenders.extend(
            f"{path}:{number}" for number, _line in find_offending_lines(path, text)
        )

    # Then
    assert offenders == []


def test_guard_flags_private_feedback_shapes_and_allows_attribution() -> None:
    # Given synthetic lines in each shape that quotes a person's feedback
    shapes = (
        f'// ({PERSON_NAME}, 2000-01-01: "synthetic quote")',
        f"// {PERSON_NAME}: synthetic remark",
        f"// (from {PERSON_NAME})",
        f"// the example {PERSON_NAME} mentioned",
        f"// {PERSON_NAME} 2000-01-01 synthetic note",
        f"Thanks, {PERSON_NAME}, for the example",
        f"// {PERSON_NAME.lower()}: synthetic remark in lower case",
        f"// {PERSON_NAME.upper()}: synthetic remark in upper case",
        f"// reported by @{PERSON_NAME.lower()}_example",
        f"// reported by {PERSON_NAME}Example",
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


def test_guard_decodes_utf16_text(tmp_path: Path) -> None:
    # Given a UTF-16 file naming the person
    sample = tmp_path / "utf16.txt"
    _ = sample.write_bytes(("// " + PERSON_NAME + ": synthetic\n").encode("utf-16"))

    # When it is decoded the way the repository scan does
    text = decode_tracked_bytes(sample.read_bytes())
    assert text is not None

    # Then its line is flagged rather than skipped as binary
    assert find_offending_lines("docs/utf16.txt", text) == [
        (1, "// " + PERSON_NAME + ": synthetic")
    ]
