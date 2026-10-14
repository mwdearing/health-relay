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
SURNAME = "Dear" + "ing"
# Case-insensitive and matched anywhere in a word, so account-style identifiers
# (an underscore or a following capital) are caught too.
# The surname is matched at a word start, so `M. <surname>` and `<surname>:` are
# caught; the public GitHub handle that ends in it (in every repository URL, by
# design) is account identity, not attribution, and is not matched.
NAME_PATTERN = re.compile(PERSON_NAME + r"|\b" + SURNAME, re.IGNORECASE)

# (path, exact-line regex) pairs for legitimate attribution. Nothing else is exempt.
ALLOWLIST: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        # Copyright holder line of the project NOTICE file, required by Apache-2.0.
        "NOTICE",
        re.compile(r"^Copyright \(c\) 2026 " + PERSON_NAME + r" " + SURNAME + r"$"),
    ),
    (
        # Mirror of the NOTICE copyright line in the About screen text; a guardrail
        # test requires the two to stay identical.
        "ios/HealthBridgeCompanion/App/ContentView.swift",
        re.compile(r"^\s*Copyright \(c\) 2026 " + PERSON_NAME + r" " + SURNAME + r"$"),
    ),
)


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


def _decode_text(path: str) -> str:
    return decode_tracked_bytes((ROOT / path).read_bytes())


def decode_tracked_bytes(data: bytes) -> str:
    """Return the text the guard scans for one tracked file; nothing is skipped.

    Plain UTF-8 and BOM-marked UTF-16 decode normally. Anything else (binary, or
    UTF-16 without a byte-order mark) is scanned under every decoding the name
    could hide in, so an unrecognised encoding fails closed instead of passing.
    """
    if data.startswith(UTF16_BOMS):
        return data.decode("utf-16", errors="replace")
    if b"\0" not in data:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return "\n".join(
        data.decode(encoding, errors="ignore")
        for encoding in ("utf-8", "utf-16-le", "utf-16-be")
    )


def test_public_repository_names_no_private_feedback_source() -> None:
    # Given / When
    offenders: list[str] = []
    for path in _tracked_files():
        text = _decode_text(path)
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
        f"// {SURNAME}: synthetic remark",
        f"// (M. {SURNAME}, 2000-01-01)",
    )

    # When / Then every shape is flagged
    for line in shapes:
        assert find_offending_lines("src/example.py", line) == [(1, line)], line

    # The allowlisted NOTICE copyright line is not flagged ...
    notice_line = "Copyright (c) 2026 " + PERSON_NAME + " " + SURNAME
    assert find_offending_lines("NOTICE", notice_line) == []

    # The public GitHub handle in repository URLs is not attribution.
    handle_line = "https://github.com/mw" + SURNAME.lower() + "/health-relay"
    assert find_offending_lines("pyproject.toml", handle_line) == []

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

    # Then its line is flagged rather than skipped as binary
    assert find_offending_lines("docs/utf16.txt", text) == [
        (1, "// " + PERSON_NAME + ": synthetic")
    ]


def test_guard_scans_bom_less_utf16_and_binary_content() -> None:
    line = "// " + PERSON_NAME + ": synthetic"
    for encoding in ("utf-16-le", "utf-16-be"):
        text = decode_tracked_bytes(line.encode(encoding))
        assert find_offending_lines("docs/x.txt", text) != [], encoding
    binary = b"\x89PNG\0\0" + PERSON_NAME.encode() + b"\0\xff"
    assert find_offending_lines("assets/x.png", decode_tracked_bytes(binary)) != []
