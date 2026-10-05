"""Guardrails for the pinned receiver release scheme.

A pinned receiver release is identified by a `healthrelay-receiver-<date>` tag on
an immutable commit, never by a package version bump, and that tag must not
trigger any upstream release workflow.

The release-trigger guard in this module is a **positive whitelist that fails
closed**. It is not a YAML parser, and it refuses to become one:

1. no file under `.github/workflows` (recursively) may contain the literal prefix
   `healthrelay-receiver-` at all -- including inside a comment, because "this
   workflow will never fire for that prefix" written in a comment is exactly the
   sort of claim that goes stale;
2. no file under `.github/workflows` may fail to decode as UTF-8 text, because a
   file the guard cannot read is a file the guard cannot clear;
3. every workflow's trigger block must be *locatable* unambiguously, and inside
   it every trigger must be positively recognised as unable to fire for a pinned
   receiver tag. A trigger is cleared only when it is an event named in
   `CLEARABLE_EVENTS`, or a `push` filtered to `branches`/`branches-ignore` in
   block-list form with no `tags`/`tags-ignore` key anywhere in the trigger
   block. Everything else is reported: `push` carrying any tag key, `create`,
   `release`, `delete`, an event name with no rule, and any construct the guard
   cannot place with certainty (a quoted `"on"`, an anchor, a flow sequence or
   flow mapping, an `on:` value with trailing content).

**The asymmetry is the entire design.** Reporting a safe workflow costs a human
thirty seconds; clearing an unsafe one ships a receiver release into a foreign
release pipeline. An earlier round grew the guard the other way -- teach it one
shape at a time -- and every shape it was taught was a shape it could be wrong
about; eight of them failed open, silently clearing triggers GitHub would have
fired for a `healthrelay-receiver-*` ref (issue #87). So the guard never grows by
impression: it clears a trigger it was explicitly given a rule for, and sends
everything else to a human.

Do not "improve" this back into a shape parser. To let a real workflow through,
either name its event in `CLEARABLE_EVENTS`, or record its tag filter in
`REVIEWED_TAG_ALLOWANCES` with a stated reason, or ask a human to look.

**The published-ref checks fail in the same direction.** A documented ref that
does not exist ships a receiver that cannot install, and a documented ref whose
notes were never written ships one with no release record; both are reported.
Nothing here is narrowed to make a check pass, and each boundary is defined by
what a ref *cannot* contain rather than by an enumeration of delimiters, because
an enumeration failed open: `healthrelay-receiver-2026.10.04-typo!` matched no
pattern at all and so satisfied no assertion anywhere. A ref's alphabet is spelled
out in ASCII — digits, ASCII letters, `_`, `-` and `.` — so **every non-ASCII
character ends a ref and none of them joins it**, in a document written in any
language.

These tests are stdlib only and read files under the repository root.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast
from unittest import mock

if TYPE_CHECKING:
    from collections.abc import Generator

ROOT = Path(__file__).resolve().parents[2]
PINNED_TAG_PREFIX = "healthrelay-receiver-"
# The tag itself. Anchored at neither end on purpose when scanning prose: a
# mention is malformed if the *ref* runs on past the date, so the tag must not be
# allowed to satisfy a check by matching only its own prefix. `-typo`, a fourth
# digit group and a dotted digit are all continuations; a closing backtick, a
# quote, a slash into a docs path and `.md` in a filename are not, and matching
# those would report every correctly-written reference in the repository.
PINNED_TAG_RE = re.compile(
    r"healthrelay-receiver-\d{4}\.\d{2}\.\d{2}(?![0-9]|-[A-Za-z0-9]|\.[0-9])"
)
WORKFLOWS = ROOT / ".github/workflows"
RELEASE_NOTES = ROOT / ".github/release"
VERSIONING = ROOT / "docs/versioning.md"
PACKAGE_VERSION = "1.1.1"

# Markdown that publishes an install command and so can name a pinned tag, beyond
# the documents named by hand: the remaining public documents at the repository
# root and the templates and policy files under `.github`. Naming them
# individually was the bug — a new public document, or a pinned tag added to
# SUPPORT.md or SECURITY.md, would be invisible to the "every documented tag has
# release notes" assertion, which is exactly the assertion meant to catch a
# release that was published without them. Discovery asks git which Markdown is
# tracked rather than walking the working tree, and the release notes are composed
# in separately by `_release_note_sources()`; see the comment above that function
# for why the two sets are kept apart.
#
# This tuple is the fallback scan root, used only when git cannot answer.
PINNED_TAG_SCAN_ROOTS = (ROOT,)

# Reviewed exceptions for rule 1: (path relative to the repository root, reason).
# Empty by default. A deliberate appearance of the pinned prefix in a workflow
# is a code change that has to state why, not a deleted assertion.
ALLOWED_PREFIX_APPEARANCES: tuple[tuple[str, str], ...] = ()

# Rule 3 whitelist. This is the whole of the guard's knowledge of GitHub's event
# catalogue: an event whose name is not listed here is reported, whatever its
# filter says, because "this event cannot fire for a tag push" is a claim about
# GitHub that a human has to make.
CLEARABLE_EVENTS = frozenset(
    {
        "issue_comment",
        "issues",
        "pull_request",
        "pull_request_review",
        "pull_request_target",
        "schedule",
        "workflow_call",
        "workflow_dispatch",
    }
)

TAG_REF_KEYS = ("tags", "tags-ignore")
BRANCH_REF_KEYS = ("branches", "branches-ignore")
# Keys a `push` filter may carry. `paths` only ever narrows a trigger further, so
# it is accepted; `tags`/`tags-ignore` are handled separately and never cleared
# without a reviewed allowance.
PUSH_FILTER_KEYS = ("branches", "branches-ignore", "paths", "paths-ignore")


class ReviewedTagAllowance(NamedTuple):
    """A tag filter a human has read and confirmed cannot reach the pinned prefix.

    `path` is relative to the repository root, `literals` is the exact set of tag
    patterns that path is allowed to declare under a `tags` key, and `reason`
    states why none of them can match a tag beginning with the pinned prefix. The
    guard compares literal for literal: it never evaluates a pattern, so an
    allowance cannot widen itself, and the pattern must be spelled exactly as
    recorded here.

    An allowance covers `tags` and never `tags-ignore`. The two keys mean opposite
    things — `tags` selects the refs that run, `tags-ignore` excludes them — so a
    pattern that cannot match a pinned tag under `tags` (it never selects one) is
    exactly a pattern that leaves the pinned tag running under `tags-ignore` (it
    is not excluded). An allowance that did not name its key would hand its safety
    to the inverted key the moment someone changed one word.
    """

    path: str
    literals: tuple[str, ...]
    reason: str


_IOS_TAG_REASON = (
    "The only tag filter in this file is the pattern `ios-v*`. A GitHub ref "
    "filter is matched against the ref name, where `*` stands for any run of "
    "characters other than `/`, so the pattern matches only names beginning "
    "with the literal text `ios-v`, and every pinned receiver tag name begins "
    "with `healthrelay-`. Recorded rather than guessed at, so that widening "
    "this filter is a deliberate edit."
)

_PYTHON_TAG_REASON = (
    "The only tag filter in this file is the pattern `ios-v*`, which matches "
    "only names beginning with the literal text `ios-v`, while every pinned "
    "receiver tag name begins with `healthrelay-`. Recorded so the CI workflow "
    "is not reported on every run, and so that widening its filter cannot pass "
    "unnoticed."
)

_RELEASE_TAG_REASON = (
    "The only tag filter in this file is the pattern `receiver-v*`, which "
    "matches only names beginning with the literal text `receiver-v`, while "
    "every pinned receiver tag name begins with `healthrelay-`. Recorded so "
    "the receiver release workflow is not reported on every run, and so that "
    "widening its filter cannot pass unnoticed."
)

REVIEWED_TAG_ALLOWANCES: tuple[ReviewedTagAllowance, ...] = (
    ReviewedTagAllowance(".github/workflows/ios.yml", ("ios-v*",), _IOS_TAG_REASON),
    ReviewedTagAllowance(
        ".github/workflows/python.yml", ("ios-v*",), _PYTHON_TAG_REASON
    ),
    ReviewedTagAllowance(
        ".github/workflows/release.yml", ("receiver-v*",), _RELEASE_TAG_REASON
    ),
)


class UnreadableWorkflowError(Exception):
    """A workflow file could not be decoded as UTF-8 text.

    Carries the offending path rather than a formatted message, so the wording
    lives with the class instead of at each raise site.
    """

    def __init__(self, path: object) -> None:
        self.path: object = path
        super().__init__(f"{path}: not decodable as UTF-8 text")


# --------------------------------------------------------------------------
# Pinned release tag discovery
# --------------------------------------------------------------------------


def pinned_release_tags(notes_dir: Path = RELEASE_NOTES) -> tuple[str, ...]:
    """Every pinned receiver tag that has release notes in `notes_dir`.

    Discovered from the notes filenames, never hard-coded to one literal, so a
    second pinned release is picked up without editing this module.

    A file whose name starts with the pinned prefix but is not a well-formed tag
    is a **problem**, not something to skip: `notes-healthrelay-receiver-2026.10.5.md`
    looks exactly like a release whose date lost a zero, and silently dropping it
    would let that release ship with none of the heading, install-command and
    content checks below ever running.
    """
    tags: list[str] = []
    malformed: list[str] = []
    for path in sorted(notes_dir.glob(f"notes-{PINNED_TAG_PREFIX}*.md")):
        candidate = path.name[len("notes-") : -len(".md")]
        if PINNED_TAG_RE.fullmatch(candidate):
            tags.append(candidate)
        else:
            malformed.append(path.name)
    assert not malformed, (
        f"these files look like pinned receiver releases but are not named like one: "
        f"{malformed}. A pinned tag is {PINNED_TAG_PREFIX}<YYYY.MM.DD>; if the date is "
        "wrong, rename the file rather than leaving it unchecked."
    )
    return tuple(sorted(tags))


def release_notes_path(tag: str, notes_dir: Path = RELEASE_NOTES) -> Path:
    return notes_dir / f"notes-{tag}.md"


_SCAN_EXCLUDED_DIRS = frozenset({".git", ".venv", "build", "dist", "__pycache__"})


# A character a ref is made of. Spelled out in ASCII rather than as `\w`, which is
# Unicode-aware in Python: `\w` counts every Unicode letter, mark and number as part
# of a ref, so a boundary written as its complement let a non-ASCII character be
# *absorbed* into a ref instead of ending it. Both directions of that were wrong.
# A run followed by `!` was reported correctly, but a run followed by a CJK
# character, `é` or `т` was not reported at all — the regex backtracked off the
# whole broken suffix, matched the clean prefix before it, and that prefix is a
# well-formed tag, so the broken ref satisfied no assertion anywhere. The same
# Unicode-aware complement also truncated a *correct* ref: `…2026.10.04²` was read
# as `…2026.10` and reported as malformed.
# `test_a_non_ascii_character_ends_a_ref_without_joining_it` pins both.
REF_CHAR = r"[0-9A-Za-z_-]"

# A whole ref run after the prefix: at least one digit, then characters from the
# ref alphabet, plus any `.` groups that are themselves part of the ref. The `(?!md\b)`
# keeps `notes-healthrelay-receiver-2026.10.04.md` correct — `.md` is a filename
# extension that follows the ref, not part of it — and the body cannot end in `.`, so
# `healthrelay-receiver-2026.10.04.` captures the bare tag and stays green.
REF_RUN = rf"[0-9]{REF_CHAR}*(?:\.(?!md\b){REF_CHAR}+)*"

# ...and the two things that could still continue it, which is therefore the
# boundary. See the comment on `ANY_PINNED_REF_RUN` for why this is not `\w`.
REF_CONTINUATION = rf"{REF_CHAR}|\.(?!md\b){REF_CHAR}"

# A run of the pinned prefix that is not a bare, complete tag at all — including
# one that never reaches a full date (`2026.10.4`, the same lost-zero shape this
# module rejects in a release-notes filename). This is the catch-all, and whether
# a run is reported is decided afterwards by asking whether it is a complete tag
# in full. Two things about the boundary decide which refs are ever seen:
#
# - **The body cannot end in `.`.** A sentence-ending period is punctuation, not
#   part of the ref, so `healthrelay-receiver-2026.10.04.` captures the bare tag
#   and stays green. The previous character class allowed a trailing `.` and
#   swallowed it, so a correctly written reference ending a sentence was reported
#   as malformed.
# - **The boundary is "a character that cannot be part of a ref", not a list of
#   terminators.** The version before this enumerated `` ` ``, quotes, whitespace,
#   `,`, `;`, `:`, `)`, `]` — and a run followed by `!`, `?` or `>` matched
#   *nothing at all*. `healthrelay-receiver-2026.10.04-typo!` therefore satisfied
#   no assertion anywhere: the broken-ref check passed because the ref was
#   invisible, not because it was judged well-formed. That is the fail-open this
#   guard exists to prevent, and an enumeration of delimiters cannot close it —
#   the next punctuation mark someone writes would reopen it.
#
# The boundary is therefore the complement of `REF_CHAR`, plus the one `.` case the
# body treats as part of a ref: a `.` followed by a ref character. Writing it out
# states the rule in one place and keeps the guarantee independent of the
# enumeration; it does not currently change any result, because the body is greedy
# and so already ends where a ref character cannot continue. It is kept for the
# same reason the rest of this guard states its rules rather than relying on what a
# regex happens to do: a later edit that loosened the body cannot silently reopen
# the fail-open this comment describes.
ANY_PINNED_REF_RUN = re.compile(rf"{PINNED_TAG_PREFIX}{REF_RUN}(?!{REF_CONTINUATION})")

# Marks a ref can legitimately be followed by in prose, in Markdown, or in a URL.
# The regex above does not consult this list — it recognises "a character that
# cannot be part of a ref" — so this list is a test fixture, not a specification
# the guard depends on. It is here so a boundary regression is reported against a
# set of marks someone actually writes, including the three the previous
# delimiter-enumerating version could not see (`!`, `?`, `>`).
#
# These are ASCII marks only. A non-ASCII character is covered separately, by
# `test_a_non_ascii_character_ends_a_ref_without_joining_it`, because it is the
# one class of terminator that used to be invisible to the guard.
PUNCTUATION_AFTER_A_REF: tuple[str, ...] = (
    "!",
    "?",
    ">",
    ".",
    ",",
    ";",
    ":",
    ")",
    "]",
    "`",
    '"',
    "'",
    " ",
    "\n",
)

# Refs `test_every_malformed_ref_shape_is_recognised` asserts on, each of which was a
# real gap in an earlier version of that check. Module constants rather than locals
# because a local literal is narrowed to its own length by the type checker, which
# would then report the non-emptiness guard below as unreachable.
MALFORMED_REFS: tuple[str, ...] = (
    "healthrelay-receiver-2026.10.4",
    "healthrelay-receiver-2026.10.041",
    "healthrelay-receiver-2026.10.04-typo",
    "healthrelay-receiver-2026.10.04typo",
    "healthrelay-receiver-2026.1.04",
    # A date short a digit anywhere, in either component.
    "healthrelay-receiver-2026.1.4",
    "healthrelay-receiver-202.10.04",
    # The date runs on past the day it names.
    "healthrelay-receiver-2026.10.04.5",
)
WELL_FORMED_REFS: tuple[str, ...] = (
    "healthrelay-receiver-2026.10.04",
    "notes-healthrelay-receiver-2026.10.04.md",
    "/blob/healthrelay-receiver-2026.10.04/docs/pairing.md",
    "`healthrelay-receiver-2026.10.04`",
    "healthrelay-receiver-2026.10.04/docs",
)


def malformed_pinned_refs() -> list[tuple[str, str]]:
    """Every mention of the pinned prefix in the docs that is not a bare tag.

    Returns `(path, ref)` pairs. A correctly written reference is followed by a
    character that cannot be part of a ref, so a well-formed tag is exactly a run
    that `PINNED_TAG_RE` matches in full. Anything else is reported, whether it is
    a complete date with a bad suffix or a date that is short a digit.
    """
    found: list[tuple[str, str]] = []
    for path in _markdown_sources() + _release_note_sources():
        text = path.read_text(encoding="utf-8")
        for match in ANY_PINNED_REF_RUN.finditer(text):
            ref = match.group(0)
            if PINNED_TAG_RE.fullmatch(ref):
                continue
            found.append((str(path.relative_to(ROOT)), ref))
    return found


# Two source sets, with different questions and different answers, composed per
# check below. They are deliberately separate rather than one filtered list,
# because each check needs an exclusion the other must not have:
#
# - `_markdown_sources()` answers **"which documents are public?"** — git-tracked,
#   non-generated Markdown, so a draft pinned ref in `.tmp/` or a `.build`
#   dependency tree cannot fail the release guard for a reason that has nothing
#   to do with any release. Asked of `git` rather than guessed from a directory
#   list, so a newly ignored directory is covered without editing this module.
# - `_release_note_sources()` answers **"which files are release notes?"** — the
#   notes themselves, which are published documentation like any other.
#
# Why they differ: an ordinary public release note (`notes-v1.1.1.md`) naming a
# valid new pinned ref is a claim that the ref is published, so tag discovery has
# to read it — and a mistyped `/blob/healthrelay-receiver-.../...` link inside a
# published note is a broken ref, so the malformed-ref check has to read it too.
# A single exclusion set cannot serve both: dropping the notes from discovery
# (as an earlier version did) made a documented-but-unnoted ref invisible, and
# reconciling that by dropping them from the malformed scan too hid a mistyped
# link in a file that was already published.


def _markdown_sources() -> list[Path]:
    """Public documentation: git-tracked, non-generated Markdown, excluding notes.

    Git-tracked rather than merely not-ignored, so a file that exists only in this
    working tree — a scratch document, a dependency tree checked out under
    `.build`, a stale `.pytest_cache` — is never read as something a reader could
    be shown. Falls back to walking the tree with the ignored set removed if git
    cannot answer, which leaves the scan stricter rather than looser: an
    untracked file would then be reported, never silently skipped.
    """
    tracked = _git_tracked_paths()
    paths: list[Path] = []
    if tracked is not None:
        paths = [path for path in tracked if path.suffix == ".md"]
    else:
        ignored = _git_ignored_paths()
        for root in PINNED_TAG_SCAN_ROOTS:
            for path in sorted(root.rglob("*.md")):
                relative = path.relative_to(root)
                if relative.parts and relative.parts[0] in _SCAN_EXCLUDED_DIRS:
                    continue
                if path.resolve() not in ignored:
                    paths.append(path)
    notes = RELEASE_NOTES.resolve()
    return [p for p in paths if notes not in p.resolve().parents and p.is_file()]


def _release_note_sources() -> list[Path]:
    """Every release note, which is published documentation and must be scanned."""
    if not RELEASE_NOTES.is_dir():
        return []
    return sorted(p for p in RELEASE_NOTES.rglob("*.md") if p.is_file())


def _git_tracked_paths() -> list[Path] | None:
    """Absolute paths git reports as tracked, or None if git cannot answer."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return [
        (ROOT / line).resolve() for line in result.stdout.split("\0") if line.strip()
    ]


def _git_ignored_paths() -> frozenset[Path]:
    """Absolute paths `git` reports as ignored.

    Used only by the fallback in `_markdown_sources`, and asserted directly by
    `test_an_ignored_file_is_not_public_documentation`. Falls back to an empty set
    if git cannot answer, which leaves that scan stricter rather than looser.
    """
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--others", "-i", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return frozenset()
    return frozenset(
        (ROOT / line).resolve() for line in result.stdout.splitlines() if line.strip()
    )


def pinned_tags_named_in_docs() -> set[str]:
    """Pinned tags mentioned in any published Markdown in the repository.

    Scans the public documents *and* the release notes, rather than a hand-listed
    set of sources, so a pinned tag published in a new file — or added to a release
    note, which is published documentation like any other — still requires release
    notes of its own. Excluded are git-ignored and untracked paths, which are not
    publishable, and the usual build and virtualenv directories.
    """
    named: set[str] = set()
    for path in _markdown_sources() + _release_note_sources():
        named.update(PINNED_TAG_RE.findall(path.read_text(encoding="utf-8")))
    return named


# --------------------------------------------------------------------------
# Locating the trigger block
# --------------------------------------------------------------------------

KEY_LINE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_-]*):(?P<rest>.*)")


@dataclass(frozen=True)
class _Line:
    """One non-blank line, with its indentation measured in leading spaces."""

    number: int
    indent: int
    content: str


@dataclass(frozen=True)
class _Node:
    """One key (or sequence item) of a mapping, with the lines nested under it."""

    line: _Line
    name: str
    value: str
    body: tuple[_Line, ...]
    children: tuple[_Node, ...]


@dataclass(frozen=True)
class _Located:
    """A trigger block the guard was able to find without guessing."""

    number: int
    body: tuple[_Line, ...]


class _TagScan(NamedTuple):
    problems: list[str]
    literals: tuple[str, ...]
    saw_tag_key: bool
    cleared: bool


def _yaml_lines(text: str) -> tuple[tuple[_Line, ...] | None, str]:
    """Every non-blank line with its indentation, or a reason it cannot be read."""
    lines: list[_Line] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        margin = raw[: len(raw) - len(raw.lstrip())]
        if "\t" in margin:
            return (
                None,
                f"line {number} is indented with a tab, which the guard cannot place",
            )
        content = raw.strip()
        # A YAML comment carries no structure, so it must not be able to end the
        # block it sits in. A comment at column zero inside an `on:` mapping used
        # to stop the scan there, hiding every trigger that followed it -- an
        # unfiltered `push:` or a `release:` was then never reported. Comments are
        # dropped from the structural view and kept in the text for rule 1, which
        # is textual on purpose.
        if content.startswith("#"):
            continue
        lines.append(_Line(number=number, indent=len(margin), content=content))
    return tuple(lines), ""


def _body_after(lines: tuple[_Line, ...], index: int, indent: int) -> tuple[_Line, ...]:
    """The lines nested under `lines[index]`.

    A line at the same indentation belongs to the enclosing mapping (or is the
    next sequence item), so only deeper lines are nested content.
    """
    body: list[_Line] = []
    for line in lines[index + 1 :]:
        if line.indent <= indent:
            break
        body.append(line)
    return tuple(body)


def _children(lines: tuple[_Line, ...]) -> tuple[tuple[_Node, ...], list[str]]:
    """The keys at `lines`' own indentation, plus anything the guard cannot place."""
    if not lines:
        return (), []
    indent = min(line.indent for line in lines)
    nodes: list[_Node] = []
    problems: list[str] = []
    for index, line in enumerate(lines):
        if line.indent != indent:
            continue
        is_item = line.content == "-" or line.content.startswith("- ")
        content = line.content[1:].strip() if is_item else line.content
        match = KEY_LINE.match(content)
        if match is None and not is_item:
            problems.append(
                f"line {line.number}: the guard cannot place {line.content!r}"
            )
            continue
        body = _body_after(lines, index, indent)
        if match is None:
            # A sequence item that is not a mapping key is a plain scalar. It is
            # opaque here: only a `tags`/`tags-ignore` key carries a rule.
            nodes.append(
                _Node(line=line, name="", value=content, body=body, children=())
            )
            continue
        child_nodes, child_problems = _children(body)
        nodes.append(
            _Node(
                line=line,
                name=match["name"],
                value=match["rest"].strip(),
                body=body,
                children=child_nodes,
            )
        )
        problems += child_problems
    return tuple(nodes), problems


def _locate_trigger_block(text: str) -> tuple[_Located | None, str]:
    """Find the top-level `on:` key, or say why it cannot be placed with certainty.

    Anything other than a top-level `on:` with an empty value is refused: a
    quoted key, an anchor, a flow sequence, a flow mapping and a bare scalar with
    trailing content are all real YAML that this guard has no rule for, and a
    guess about any of them is exactly how the last round failed open.
    """
    lines, reason = _yaml_lines(text)
    if lines is None:
        return None, reason
    found = [
        index
        for index, line in enumerate(lines)
        if line.indent == 0
        and (match := KEY_LINE.match(line.content)) is not None
        and match["name"] == "on"
    ]
    if not found:
        return None, "no top-level `on:` key was found"
    if len(found) > 1:
        return None, "more than one top-level `on:` key was found"
    line = lines[found[0]]
    match = KEY_LINE.match(line.content)
    value = match["rest"].strip() if match is not None else ""
    if value:
        return None, (
            f"line {line.number}: `on: {value}` does not use the block form"
            " the guard reads, so its trigger cannot be placed with certainty"
        )
    return _Located(number=line.number, body=_body_after(lines, found[0], 0)), ""


# --------------------------------------------------------------------------
# Reading the triggers inside a located block
# --------------------------------------------------------------------------

# A scalar the guard can state exactly. Characters that could mean an anchor, an
# alias, a nesting, a negation or an escape are refused: the guard reads a literal
# or it reads nothing, because it never evaluates a pattern.
_OPAQUE_SCALAR_CHARS = frozenset("[]{}*&!|>'\"#%@`,?:")


def _scalar_literal(text: str) -> str | None:
    """The scalar `text` states exactly, or None if the guard cannot read it that."""
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        inner = text[1:-1]
        if '"' not in inner and "\\" not in inner:
            return inner
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        inner = text[1:-1]
        if "'" not in inner:
            return inner
    if text and not _OPAQUE_SCALAR_CHARS & set(text):
        return text
    return None


def _flow_scalars(value: str) -> tuple[tuple[str, ...], str | None]:
    """The scalars of a single-line flow sequence, or a reason they are unreadable."""
    if not (value.startswith("[") and value.endswith("]")):
        return (), f"{value!r} is not a single-line flow sequence of plain scalars"
    inner = value[1:-1].strip()
    if not inner:
        return (), "the filter is empty"
    literals: list[str] = []
    for element in inner.split(","):
        literal = _scalar_literal(element.strip())
        if literal is None:
            return (), f"{element.strip()!r} is not a plain or quoted scalar"
        literals.append(literal)
    return tuple(literals), None


def _tag_literals(node: _Node) -> tuple[tuple[str, ...], str | None]:
    """The tag patterns a `tags`/`tags-ignore` node states, or a reason it cannot."""
    if node.value:
        return _flow_scalars(node.value)
    if not node.children:
        return (), "declares no tag pattern"
    literals: list[str] = []
    for item in node.children:
        if item.name:
            return (
                (),
                f"line {item.line.number}: holds a mapping key, not a tag pattern",
            )
        if item.body:
            return (), f"line {item.line.number}: holds a nested list"
        literal = _scalar_literal(item.value)
        if literal is None:
            problem = (
                f"line {item.line.number}: {item.value!r} is not a plain or"
                " quoted scalar"
            )
            return (), problem
        literals.append(literal)
    return tuple(literals), None


def _scan_tag_keys(
    nodes: tuple[_Node, ...], allowance: ReviewedTagAllowance | None
) -> _TagScan:
    """Find every `tags`/`tags-ignore` key under a trigger block.

    A tag filter is cleared only when the whole trigger block has a reviewed
    allowance, every pattern it declares is named there literally, and the key is
    `tags` — an allowance never carries over to `tags-ignore`, whose meaning is
    inverted. No other tag filter is ever cleared: `tags: ["**"]` and
    `tags-ignore: ["v*"]` both reach a pinned receiver tag, and the guard has no
    rule that could say otherwise.
    """
    problems: list[str] = []
    literals: list[str] = []
    saw_tag_key = False
    cleared = True
    for node in nodes:
        if node.name in TAG_REF_KEYS:
            saw_tag_key = True
            found, error = _tag_literals(node)
            if error is not None:
                problems.append(f"line {node.line.number}: `{node.name}` {error}")
                cleared = False
                continue
            literals += found
            if (
                allowance is not None
                and node.name == "tags"
                and set(found) <= set(allowance.literals)
            ):
                continue
            unreviewed = (
                f"line {node.line.number}: `{node.name}` filter {list(found)} has"
                " no reviewed allowance, and the guard clears no tag filter it was"
                " not told about"
            )
            problems.append(unreviewed)
            cleared = False
            continue
        nested = _scan_tag_keys(node.children, allowance)
        problems += nested.problems
        literals += nested.literals
        saw_tag_key = saw_tag_key or nested.saw_tag_key
        cleared = cleared and nested.cleared
    return _TagScan(
        problems=problems,
        literals=tuple(literals),
        saw_tag_key=saw_tag_key,
        cleared=cleared,
    )


def _branch_list_error(node: _Node) -> str | None:
    """Why a `branches`/`branches-ignore` node is not a readable block list."""
    if node.value:
        return f"line {node.line.number}: `{node.name}` is not a block list"
    if not node.body:
        return f"line {node.line.number}: `{node.name}` is empty"
    for item in node.body:
        if not item.content.startswith("- "):
            return f"line {item.number}: `{node.name}` is not a block list"
        if _scalar_literal(item.content[2:].strip()) is None:
            return f"line {item.number}: `{node.name}` does not hold plain patterns"
    return None


def _push_problem(node: _Node, saw_tag_key: bool, tag_keys_cleared: bool) -> str | None:
    """Why a `push` trigger is not positively recognised as unable to reach a tag.

    A `push` reaches tag pushes unless its filter says otherwise, so the only
    shapes cleared here are a filter to `branches`/`branches-ignore` written as a
    block list, and a tag filter that a human has separately reviewed.
    """
    if saw_tag_key:
        if tag_keys_cleared:
            return None
        return (
            f"line {node.line.number}: `push:` carries a `tags`/`tags-ignore`"
            " key whose filter the guard does not recognise as unable to reach a"
            " pinned receiver tag"
        )
    if node.value:
        return (
            f"line {node.line.number}: `push: {node.value}` states its filter"
            " inline, which the guard cannot place with certainty"
        )
    if not node.children:
        return f"line {node.line.number}: `push:` states no filter at all"
    problems: list[str] = []
    branch_keys = 0
    for child in node.children:
        if child.name not in PUSH_FILTER_KEYS:
            unknown = (
                f"line {child.line.number}: `push:` key {child.name!r} has no rule"
                " in the guard"
            )
            problems.append(unknown)
            continue
        if child.name not in BRANCH_REF_KEYS:
            continue
        branch_keys += 1
        error = _branch_list_error(child)
        if error is not None:
            problems.append(error)
    if not branch_keys and not problems:
        unfiltered = (
            f"line {node.line.number}: `push:` is not filtered to"
            " `branches`/`branches-ignore`, so it reaches every tag"
        )
        problems.append(unfiltered)
    return problems[0] if problems else None


def workflow_problems(
    text: str, tag_allowance: ReviewedTagAllowance | None = None
) -> list[str]:
    """Every way `text` (one workflow) fails the conservative guard.

    `tag_allowance` is the reviewed allowance for the file `text` came from, if
    any. It is passed in rather than looked up so that a caller can never apply
    one file's allowance to another.
    """
    problems: list[str] = []
    if PINNED_TAG_PREFIX in text:
        mention = (
            f"the file mentions {PINNED_TAG_PREFIX!r}; a pinned receiver release"
            " must never appear in a workflow, not even in a comment"
        )
        problems.append(mention)
    located, reason = _locate_trigger_block(text)
    if located is None:
        problems.append(f"the trigger block cannot be located: {reason}")
        return problems
    if not located.body:
        problems.append(f"line {located.number}: `on:` states no triggers at all")
        return problems
    nodes, placement_problems = _children(located.body)
    problems += placement_problems
    tags = _scan_tag_keys(nodes, tag_allowance)
    problems += tags.problems
    for node in nodes:
        if node.name == "push":
            problem = _push_problem(node, tags.saw_tag_key, tags.cleared)
        elif node.name in CLEARABLE_EVENTS:
            problem = None
        elif not node.name:
            problem = (
                f"line {node.line.number}: trigger {node.value!r} is not a mapping"
                " key the guard can place"
            )
        else:
            problem = (
                f"line {node.line.number}: trigger `{node.name}` is not on the"
                " guard's clearable event list"
            )
        if problem is not None:
            problems.append(problem)
    return problems


def declared_tag_literals(text: str) -> tuple[str, ...]:
    """The tag patterns a workflow's trigger block states, for allowance checks."""
    located, _reason = _locate_trigger_block(text)
    if located is None:
        return ()
    nodes, _problems = _children(located.body)
    return _scan_tag_keys(nodes, None).literals


# --------------------------------------------------------------------------
# Reading the workflow tree
# --------------------------------------------------------------------------


def read_workflow_text(path: Path) -> str:
    """Read a workflow as text, failing closed when it cannot be decoded."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise UnreadableWorkflowError(path) from exc


def _relative_to_root(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _reviewed_allowance(relative: str) -> ReviewedTagAllowance | None:
    """The reviewed tag allowance for one workflow, or None."""
    for allowance in REVIEWED_TAG_ALLOWANCES:
        if allowance.path == relative:
            return allowance
    return None


def scan_workflow_file(
    path: Path,
    allowances: tuple[tuple[str, str], ...] = (),
    tag_allowance: ReviewedTagAllowance | None = None,
) -> list[str]:
    """Problems in one workflow file; never silently skips an unreadable file."""
    try:
        text = read_workflow_text(path)
    except UnreadableWorkflowError as exc:
        fail_closed = (
            f"{exc}; the guard fails closed rather than certifying a workflow"
            " it cannot read"
        )
        return [fail_closed]
    relative = _relative_to_root(path)
    problems = workflow_problems(text, tag_allowance)
    if relative in dict(allowances) and PINNED_TAG_PREFIX in text:
        # The allowance covers the *prefix-appearance* rule and nothing else, so
        # only that one problem is dropped. Returning early here would silence
        # every other finding in the file: an allowlisted mention in a comment
        # plus an unfiltered `push:` would report nothing at all.
        prefix_problem = (
            f"the file mentions {PINNED_TAG_PREFIX!r}; a pinned receiver release"
            " must never appear in a workflow, not even in a comment"
        )
        problems = [problem for problem in problems if problem != prefix_problem]
    return [f"{relative}: {problem}" for problem in problems]


def scan_workflows(
    workflows: Path = WORKFLOWS, allowances: tuple[tuple[str, str], ...] = ()
) -> list[str]:
    files = sorted(p for p in workflows.rglob("*") if p.is_file())
    assert files, f"no workflows found under {workflows}; the guard must stay honest"
    problems: list[str] = []
    for path in files:
        problems += scan_workflow_file(
            path, allowances, _reviewed_allowance(_relative_to_root(path))
        )
    return problems


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_no_workflow_can_trigger_on_a_pinned_receiver_tag() -> None:
    offenders = scan_workflows(WORKFLOWS, ALLOWED_PREFIX_APPEARANCES)
    assert not offenders, "\n".join(offenders)


def test_allowlist_entries_must_state_a_reason() -> None:
    for entry in ALLOWED_PREFIX_APPEARANCES:
        assert isinstance(entry, tuple), entry
        assert len(entry) == 2, entry
        path, reason = entry
        assert path, entry
        assert not path.startswith("/"), path
        assert reason.strip(), f"{path} is allowlisted without a stated reason"


def test_reviewed_tag_allowances_are_narrow_and_stated() -> None:
    assert REVIEWED_TAG_ALLOWANCES, "the reviewed tag allowances were dropped"
    for entry in REVIEWED_TAG_ALLOWANCES:
        assert not entry.path.startswith("/"), entry.path
        assert entry.path.endswith((".yml", ".yaml")), entry.path
        assert entry.literals, entry.path
        assert entry.reason.strip(), f"{entry.path} is reviewed without a stated reason"
        assert (ROOT / entry.path).is_file(), entry.path
        for literal in entry.literals:
            # A reviewed pattern is only ever matched literally, but it still has
            # to be one whose leading text cannot be the pinned prefix. Spelling
            # that out here means a widening has to change this assertion too.
            assert literal.startswith(("ios-v", "receiver-v")), literal
            assert not literal.startswith(PINNED_TAG_PREFIX), literal


def test_reviewed_tag_allowances_cover_exactly_the_filters_present() -> None:
    """An allowance states the patterns its file has, so it cannot widen itself."""
    allowed = {entry.path for entry in REVIEWED_TAG_ALLOWANCES}
    for path in sorted(p for p in WORKFLOWS.rglob("*") if p.is_file()):
        relative = _relative_to_root(path)
        text = read_workflow_text(path)
        literals = set(declared_tag_literals(text))
        if not literals:
            assert relative not in allowed, (
                f"{relative} is reviewed for a tag filter it no longer declares"
            )
            continue
        entry = _reviewed_allowance(relative)
        assert entry is not None, (
            f"{relative} declares tag filters {sorted(literals)} with no reviewed"
            " allowance"
        )
        assert literals <= set(entry.literals), (
            f"{relative} declares {sorted(literals)} but is reviewed for"
            f" {list(entry.literals)}"
        )


def test_no_allowance_is_reached_through_tags_ignore() -> None:
    """`tags-ignore` inverts the key's meaning, so no allowance may clear one.

    A pattern that cannot match a pinned tag under `tags` — it selects none —
    is exactly a pattern that leaves the pinned tag running under
    `tags-ignore`, because there it excludes nothing relevant. An allowance that
    followed the pattern to the other key would turn a reviewed safety into an
    unreviewed one, so this asserts the inversion directly rather than trusting
    every allowance's key to stay spelled correctly.
    """
    for name, template in (
        ("tags", 'on:\n  push:\n    {key}:\n      - "{pattern}"\njobs: {{}}\n'),
        ("tags-ignore", 'on:\n  push:\n    {key}:\n      - "{pattern}"\njobs: {{}}\n'),
    ):
        for pattern in ("ios-v*", "receiver-v*"):
            with tempfile.TemporaryDirectory() as raw:
                tree = Path(raw)
                _ = (tree / "wf.yml").write_text(
                    template.format(key=name, pattern=pattern), encoding="utf-8"
                )
                _ = (tree / ".keep").write_text("", encoding="utf-8")
                problems = scan_workflow_file(
                    tree / "wf.yml",
                    tag_allowance=ReviewedTagAllowance(
                        "wf.yml", ("ios-v*", "receiver-v*"), "reviewed: probe"
                    ),
                )
            if name == "tags-ignore":
                assert problems, (
                    f"`{name}: [{pattern}]` was cleared by an allowance that was "
                    "reviewed for the same pattern under `tags`, whose meaning is "
                    "the opposite"
                )
            else:
                assert not problems, (
                    f"a reviewed `tags` filter was reported ({name}, {pattern}): "
                    f"{problems}"
                )


def test_pinned_release_tag_discovery_is_not_vacuous() -> None:
    tags = pinned_release_tags()
    assert tags, f"no pinned receiver release notes found under {RELEASE_NOTES}"
    assert all(PINNED_TAG_RE.fullmatch(tag) for tag in tags)
    for tag in tags:
        assert release_notes_path(tag).is_file(), tag

    # Discovery is a filename scan, not a hard-coded literal: a second pinned
    # release is discovered without touching this module.
    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        _ = (tmp_path / f"notes-{PINNED_TAG_PREFIX}2099.01.02.md").write_text(
            "x", encoding="utf-8"
        )
        _ = (tmp_path / "notes-receiver-v9.9.9.md").write_text("x", encoding="utf-8")
        discovered = pinned_release_tags(tmp_path)
    assert discovered == ("healthrelay-receiver-2099.01.02",)
    assert not set(discovered) & set(tags)


def test_every_pinned_tag_named_in_docs_has_its_own_release_notes() -> None:
    named = pinned_tags_named_in_docs()
    assert named, "no pinned receiver tag is named in the docs at all"
    missing = sorted(tag for tag in named if not release_notes_path(tag).is_file())
    assert not missing, f"pinned tags without release notes: {missing}"


# The label must be exactly "base commit". Matching any row whose label merely
# contains "commit" left the false-pass open through unrelated metadata such as
# `| Artifact commit | ... |` or `| Previous commit | ... |`.
BASE_COMMIT_FIELD = re.compile(
    r"^\|[ \t]*base[ \t]+commit[ \t]*\|[ \t]*`(?P<sha>[0-9a-f]{7,40})`[ \t]*\|",
    re.MULTILINE | re.IGNORECASE,
)


def release_notes_name_their_base_commit(notes: str) -> bool:
    """Whether the notes name the release's base commit as that field's value.

    A bare hex search is too weak to be evidence: any copied checksum, example
    digest or unrelated commit reference would satisfy it while the notes still
    failed to say which commit the release pins.
    """
    return BASE_COMMIT_FIELD.search(notes) is not None


def test_release_notes_must_name_the_commit_in_its_base_commit_field() -> None:
    """A stray hex string is not a base commit; the designated field is.

    Without this the check can silently go back to a bare hex search.
    """
    assert release_notes_name_their_base_commit("| Base commit | `4677570` |")
    assert release_notes_name_their_base_commit("| base commit | `0b42c4fafb13` |")
    assert not release_notes_name_their_base_commit("artifact sha256: deadbeefcafe")
    assert not release_notes_name_their_base_commit("see also 4677570 for context")
    assert not release_notes_name_their_base_commit("| Base commit | see main |")
    assert not release_notes_name_their_base_commit("")
    # Another row whose label merely contains "commit" must not stand in for it.
    assert not release_notes_name_their_base_commit("| Artifact commit | `deadbeef` |")
    assert not release_notes_name_their_base_commit("| Previous commit | `4677570` |")
    assert not release_notes_name_their_base_commit("| commit | `4677570` |")


def test_a_malformed_pinned_ref_in_the_docs_is_reported() -> None:
    """A ref that runs on past its date is a broken install command.

    `PINNED_TAG_RE` refuses to match `healthrelay-receiver-2026.10.04-typo` or
    `healthrelay-receiver-2026.10.041` as a tag, so those mentions are skipped
    entirely by discovery — which means they satisfy no assertion at all. They
    have to be reported in their own right, because each one is a documented ref
    that does not exist.

    A date short a digit is included: `healthrelay-receiver-2026.10.4` is the same
    lost-zero shape this module rejects in a release-notes filename, and a
    pattern that needed a complete date before it could object would never see it.
    """
    malformed = sorted(malformed_pinned_refs())
    detail = "\n".join(f"  {path}: {ref!r}" for path, ref in malformed)
    assert not malformed, (
        "these mentions of a pinned receiver tag are not a well-formed tag and"
        f" would install nothing:\n{detail}"
    )


def test_every_malformed_ref_shape_is_recognised() -> None:
    """The recogniser covers the shapes it is claimed to, and not the good ones.

    Pinned here rather than only in the acceptance script, because each of these
    was a real gap in an earlier version of this check.

    Both fixtures are asserted non-empty before either loop, in the style of
    `test_workflow_guard_is_not_vacuous`. A loop over an empty fixture never runs,
    so emptying either one used to leave the whole suite green while this test
    asserted nothing at all — the same vacuity as an empty
    `RELEASE_TRIGGERING_WORKFLOWS`, and invisible unless someone tries it. They are
    module constants rather than locals because a local literal is narrowed to its
    own length by the type checker, which would report the guard as unreachable.
    """
    malformed = MALFORMED_REFS
    well_formed = WELL_FORMED_REFS
    assert malformed, (
        "the malformed-ref fixture is empty, so this test asserts nothing about a "
        "broken ref"
    )
    assert well_formed, (
        "the well-formed-ref fixture is empty, so this test asserts nothing about a "
        "correct ref"
    )
    for ref in malformed:
        runs: list[str] = ANY_PINNED_REF_RUN.findall(f"install {ref} now")
        assert runs, f"{ref!r} is not recognised as a pinned ref run at all"
        assert not PINNED_TAG_RE.fullmatch(runs[0]), (
            f"{ref!r} is reported as a well-formed tag"
        )
    for ref in well_formed:
        found: list[str] = ANY_PINNED_REF_RUN.findall(f"see {ref} for details")
        assert found, f"{ref!r} is not recognised at all"
        assert PINNED_TAG_RE.fullmatch(found[0]), (
            f"the correct reference {ref!r} was read as {found[0]!r}, so it would be "
            "reported as malformed"
        )


def test_an_ignored_file_is_not_public_documentation() -> None:
    """Local output is not public documentation.

    A working tree carries `.pytest_cache`, `.build` trees and scratch
    directories. Without this a draft pinned ref in a scratch file fails the
    release guard for a reason that has nothing to do with a release — which
    teaches everyone to ignore the guard.

    The exclusion is asked of git rather than guessed from a directory list, so a
    newly ignored directory is covered without editing this module.
    """
    scratch = ROOT / ".pytest_cache" / "README.md"
    if scratch.exists():
        scratch.unlink()
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        _ = scratch.write_text(
            "scratch\n\nhealthrelay-receiver-2027.03.03\n", encoding="utf-8"
        )
        ignored = _git_ignored_paths()
        if not ignored:
            # No git repository here — the acceptance script runs the suite in a
            # throwaway copy of the tracked tree. The exclusion is a no-op rather
            # than a wrong answer, so the invariant that matters is simply that
            # nothing crashed and the scan still returns a list.
            assert isinstance(_markdown_sources(), list)
            return
        assert scratch.resolve() in ignored, (
            "git reports .pytest_cache/README.md as ignored, so the scan must "
            "skip it; local output is not public documentation"
        )
        assert scratch not in _markdown_sources(), (
            "an ignored scratch file is not a public document"
        )
        assert scratch not in _markdown_sources() + _release_note_sources(), (
            "the git-ignored exclusion applies to both source sets: an ignored "
            "file is not public documentation, and it is not a release note "
            "either, so neither the malformed-ref scan nor tag discovery may "
            "read it"
        )
    finally:
        shutil.rmtree(scratch.parent, ignore_errors=True)


def test_an_untracked_file_is_not_public_documentation() -> None:
    """Not merely ignored: a file git does not track cannot be published.

    `.pytest_cache` and `.tmp/` are covered by `.gitignore`, but an *untracked*
    file that no ignore rule covers is equally unpublishable — a scratch document
    left in `docs/`, or a dependency tree a build wrote somewhere unexpected.
    Discovery asks git what is tracked, so an untracked Markdown file is not
    public documentation even when nothing ignores it.
    """
    scratch = ROOT / "docs" / "scratch-untracked.md"
    tracked = _git_tracked_paths()
    if tracked is None:
        return
    assert not scratch.exists(), f"{scratch.name} exists; the probe needs a fresh name"
    _ = scratch.write_text("healthrelay-receiver-2099.09.09\n", encoding="utf-8")
    try:
        after = _git_tracked_paths()
        assert after is not None, "git stopped answering mid-test"
        assert scratch not in after, (
            "the probe must be untracked, or it is testing nothing"
        )
        assert scratch not in _markdown_sources(), (
            "an untracked file is not publishable documentation, so a draft "
            "pinned ref in it must not fail the release guard"
        )
    finally:
        scratch.unlink()


@contextmanager
def release_notes_replaced_by_a_copy() -> Generator[Path]:
    """Yield a writable copy of the release notes, standing in for the real ones.

    Two checks have to read release notes, and proving that they do needs a note
    naming a ref it should not. That proof used to be written into the tracked
    `.github/release/` files themselves and put back in a `finally`, which no
    `finally` can promise: a timeout, a CI cancellation or an OOM kills the process
    between the write and the restore, and what survives is published release notes
    containing a fabricated `healthrelay-receiver-2099.01.02` or
    `healthrelay-receiver-2026.10.04-typo`, one `git commit -a` from being the
    release record for a release that never happened. Every other `write_text` in
    this module targets a `TemporaryDirectory` tree or this git-ignored cache
    directory; these two were the only ones to reach a tracked file.

    The copy lives under `.pytest_cache` for that reason — the same scratch root
    `test_an_ignored_file_is_not_public_documentation` already uses — so even a copy
    leaked by an abnormal exit is neither tracked, nor readable as public
    documentation, nor publishable.

    `RELEASE_NOTES` is repointed for the duration because that is the one path both
    `_release_note_sources()` and `_markdown_sources()` resolve, and repointing it is
    the only way to prove the checks *read* the notes rather than merely agreeing
    that they should. `mock.patch.object` does the repointing because the constant is
    spelled in upper case and a direct rebinding of it reads as a mistake.

    `release_notes_path`'s default argument is bound when the function is defined, so
    it still resolves against the real notes directory inside the block. That is
    deliberate: whether a notes file exists for a ref is a question about the
    repository, not about the copy.
    """
    cache = ROOT / ".pytest_cache"
    cache.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(dir=cache, prefix="release-notes-copy-"))
    try:
        _ = shutil.copytree(RELEASE_NOTES, scratch, dirs_exist_ok=True)
        with mock.patch.object(sys.modules[__name__], "RELEASE_NOTES", scratch):
            yield scratch
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_a_pinned_tag_named_in_a_release_note_is_discovered() -> None:
    """An ordinary public release note naming a ref still requires its own notes.

    The notes were excluded from tag discovery on the reasoning that a release
    note is what discovery looks up rather than a source of claims. That is true
    of the *pinned* note being looked up and false of every other note:
    `notes-receiver-v1.1.1.md` naming `healthrelay-receiver-2099.01.02` is a claim
    that the ref is published, and with the notes excluded
    `test_every_pinned_tag_named_in_docs_has_its_own_release_notes` passed while
    the documented ref had no release notes at all.
    """
    # The two source sets really are disjoint, and that is what makes composing
    # them necessary: a note is reached only because each check adds
    # `_release_note_sources()` to `_markdown_sources()`. This replaces an assertion
    # of the form `assert path in _markdown_sources() + sources`, which held by
    # construction — `sources` was the right-hand operand, so it was true even if
    # no check ever read a note — and therefore proved nothing.
    notes = _release_note_sources()
    documents = {path.resolve() for path in _markdown_sources()}
    assert notes, f"no release notes found under {RELEASE_NOTES}"
    assert documents, f"no public documentation found under {ROOT}"
    for path in notes:
        assert path.resolve() not in documents, (
            f"{path.name} is being read as a public document as well as a release"
            " note; the two sets answer different questions and have to stay"
            " separate, or the composition below is not what makes notes visible"
        )

    with release_notes_replaced_by_a_copy() as notes_dir:
        note = notes_dir / "notes-receiver-v1.1.1.md"
        assert note.is_file(), (
            f"{note.name} is missing from the copy, so the probe is testing nothing"
        )
        ref = f"{PINNED_TAG_PREFIX}2099.01.02"
        _ = note.write_text(
            note.read_text(encoding="utf-8")
            + f"\ninstall this build at {ref} to pin it\n",
            encoding="utf-8",
        )
        # The copy has to be the source set these checks read, or every assertion
        # below is vacuous for a second and more subtle reason.
        assert note in _release_note_sources(), (
            "the release-notes copy is not the source set the checks read, so "
            "writing to it cannot prove anything about them"
        )
        assert ref in pinned_tags_named_in_docs(), (
            f"{note.name} names {ref}, so discovery must see it; an ordinary "
            "release note is published documentation and a claim in it that the "
            "ref is released"
        )
        missing = sorted(
            tag
            for tag in pinned_tags_named_in_docs()
            if not release_notes_path(tag).is_file()
        )
        assert missing == [ref], (
            f"{note.name} names {ref} and there is no notes file for it, so "
            "test_every_pinned_tag_named_in_docs_has_its_own_release_notes must "
            f"fail; it passes only if the notes are excluded from discovery "
            f"(got {missing})"
        )


def test_release_notes_are_scanned_for_malformed_refs() -> None:
    """A mistyped ref inside a published note is a broken link in public docs.

    The notes share the malformed-ref scan with the rest of the documentation. A
    `/blob/healthrelay-receiver-2026.10.04-typo/...` link in a note that has
    already been published reaches every reader of it, and the per-note checks
    still pass as long as the heading and install command remain elsewhere in
    the file — so only this scan can see it.
    """
    with release_notes_replaced_by_a_copy() as notes_dir:
        note = notes_dir / f"notes-{PINNED_TAG_PREFIX}2026.10.04.md"
        assert note.is_file(), (
            f"{note.name} is missing from the copy, so the probe is testing nothing"
        )
        broken = f"{PINNED_TAG_PREFIX}2026.10.04-typo"
        _ = note.write_text(
            note.read_text(encoding="utf-8") + f"\n/blob/{broken}/docs/pairing.md\n",
            encoding="utf-8",
        )
        assert note in _release_note_sources(), (
            "the release-notes copy is not the source set the malformed-ref scan"
            " reads, so writing to it cannot prove anything about the scan"
        )
        assert (str(note.relative_to(ROOT)), broken) in malformed_pinned_refs(), (
            "a mistyped /blob link inside a published release note must be"
            f" reported as {broken!r} in {note.relative_to(ROOT)}; the per-note"
            " checks cannot see it. Reported findings were"
            f" {sorted(malformed_pinned_refs())}"
        )


def test_a_malformed_ref_is_reported_however_it_is_punctuated() -> None:
    """The boundary must recognise punctuation without capturing it.

    Two failures hid behind one boundary. A ref run followed immediately by `!`,
    `?` or `>` had no recognised terminator, so `finditer` returned *no match* —
    `healthrelay-receiver-2026.10.04-typo!` satisfied no assertion anywhere and
    the guard was green. And a sentence-ending period was consumed into the
    captured ref, so the perfectly correct `healthrelay-receiver-2026.10.04.` was
    reported as malformed.

    Both directions are asserted for every punctuation mark below: punctuation
    after a broken ref must not hide it, and punctuation after a correct ref must
    not invent a finding. The list is deliberately not exhaustive — the point is
    that the boundary is "a character that cannot be part of a ref", so it does
    not depend on this list being complete.
    """
    # Without this the loop below never runs: emptying the fixture left the whole
    # suite green while this test asserted nothing, which is the vacuity
    # `test_workflow_guard_is_not_vacuous` already guards against for the workflow
    # fixtures.
    assert PUNCTUATION_AFTER_A_REF, (
        "the punctuation fixture is empty, so this test asserts nothing"
    )
    broken = f"{PINNED_TAG_PREFIX}2026.10.04-typo"
    correct = f"{PINNED_TAG_PREFIX}2026.10.04"
    for punctuation in PUNCTUATION_AFTER_A_REF:
        for ref, want_malformed in ((broken, True), (correct, False)):
            line = f"install {ref}{punctuation}"
            runs: list[str] = ANY_PINNED_REF_RUN.findall(line)
            assert runs, (
                f"{line!r} produced no match at all, so the ref is invisible to "
                f"every check; punctuation {punctuation!r} is not recognised as "
                "terminating a ref run"
            )
            # The captured ref must never carry the punctuation with it.
            assert runs[0] == ref, (
                f"{line!r} captured {runs[0]!r} instead of {ref!r}; the boundary "
                "must recognise punctuation without including it in the ref"
            )
            assert (PINNED_TAG_RE.fullmatch(runs[0]) is None) == want_malformed, (
                f"{line!r} was read as {runs[0]!r}; want "
                f"{'a malformed ref' if want_malformed else 'the bare tag'}"
            )


# The two refs every boundary test contrasts: one broken by a suffix, one correct.
BROKEN_SUFFIX = f"{PINNED_TAG_PREFIX}2026.10.04-typo"
BARE_TAG = f"{PINNED_TAG_PREFIX}2026.10.04"

# (label, lead-in, the non-ASCII character that follows, the ref that must be
# captured, and whether that ref must be reported).
#
# The non-ASCII characters are the ones a document in another language actually puts
# next to a ref: a CJK word, a Latin-1 accented letter, a Cyrillic letter pasted
# from a keyboard, and a superscript. The first four rows are broken refs and the
# last four are correct ones, and the required verdict differs between them — which
# is the whole point. A terminator that ends a ref must not be able to hide a
# broken one, and must not be able to invent a finding on a correct one.
NON_ASCII_AFTER_A_REF: tuple[tuple[str, str, str, str, bool], ...] = (
    ("broken_cjk", "请安装 ", "版本", BROKEN_SUFFIX, True),
    ("broken_accented", "pin ", "\u00e9", BROKEN_SUFFIX, True),
    ("broken_cyrillic", "pin ", "\u0442", BROKEN_SUFFIX, True),
    ("broken_superscript", "install ", "\u00b2", BROKEN_SUFFIX, True),
    ("correct_cjk", "请安装 ", "版本", BARE_TAG, False),
    ("correct_accented", "pin ", "\u00e9", BARE_TAG, False),
    ("correct_cyrillic", "pin ", "\u0442", BARE_TAG, False),
    ("correct_superscript", "install ", "\u00b2", BARE_TAG, False),
)


def test_a_non_ascii_character_ends_a_ref_without_joining_it() -> None:
    """A non-ASCII character terminates a ref and is never absorbed into one.

    The boundary used to be the complement of `\\w`, and Python's `\\w` is
    Unicode-aware, so every Unicode letter, mark and number counted as part of a
    ref. Both directions of that were wrong, and each had its own reproduction:

    - **Fail-open.** `请安装 healthrelay-receiver-2026.10.04-typo版本` reported
      nothing. The regex could not end the run on `版`, so it backtracked off the
      entire broken suffix `-typo`, matched `healthrelay-receiver-2026.10.04`, and
      that prefix is a well-formed tag — so a documented ref that installs nothing
      satisfied no assertion anywhere. `PINNED_TAG_RE.findall` returns `[]` for the
      same text, so tag discovery cannot see it either.
    - **False positive.** `install healthrelay-receiver-2026.10.04²` reported
      `healthrelay-receiver-2026.10` as malformed, because the run had to be cut
      short before the superscript and shortening it too far was enough to break the
      tag shape.

    A ref's alphabet is ASCII — digits, ASCII letters, `_`, `-` and `.` — so the
    expected ref is always the ASCII part of what was written, and the non-ASCII
    character is always outside it.
    """
    assert NON_ASCII_AFTER_A_REF, "the non-ASCII boundary fixture was dropped"
    for label, lead, trail, want_ref, want_malformed in NON_ASCII_AFTER_A_REF:
        text = lead + want_ref + trail
        runs: list[str] = ANY_PINNED_REF_RUN.findall(text)
        assert runs, (
            f"{text!r} ({label}) produced no match at all, so the ref is invisible"
            " to every check; a non-ASCII character is being read as part of a ref"
            " rather than as the end of one"
        )
        assert runs[0] == want_ref, (
            f"{text!r} ({label}) captured {runs[0]!r} instead of {want_ref!r}; a"
            " non-ASCII character must end a ref without joining it, so the"
            " captured ref is exactly the ASCII part of what was written"
        )
        assert (PINNED_TAG_RE.fullmatch(runs[0]) is None) == want_malformed, (
            f"{text!r} ({label}) was read as {runs[0]!r}; want "
            f"{'a malformed ref' if want_malformed else 'the bare tag'}"
        )


def test_release_notes_name_the_tag_install_command_and_intake_tool() -> None:
    for tag in pinned_release_tags():
        notes_path = release_notes_path(tag)
        notes = notes_path.read_text(encoding="utf-8")
        heading = notes.splitlines()[0]
        assert heading.startswith("# "), notes_path.name
        assert heading.rstrip().endswith(tag), (
            f"{notes_path.name} must open with a heading naming {tag}"
        )
        assert tag in notes, f"{notes_path.name} does not name {tag}"
        install = (
            f'uv tool install "git+https://github.com/mwdearing/health-relay.git@{tag}"'
        )
        assert install in notes, (
            f"{notes_path.name} must teach the pinned install command"
        )
        assert "get_intake_evidence_v1" in notes
        assert "--enable-intake-context" in notes
        assert "health_bridge.batch.v1" in notes
        assert release_notes_name_their_base_commit(notes), (
            f"{notes_path.name} must name the release's base commit in a "
            "`Base commit` field, so an unrelated checksum cannot stand in for it"
        )


def test_versioning_documents_the_scheme_without_a_version_bump() -> None:
    versioning = VERSIONING.read_text(encoding="utf-8")
    assert PINNED_TAG_PREFIX in versioning
    assert "Latest" in versioning
    assert "Mailbox" in versioning
    pinned = (
        'uv tool install "git+https://github.com/mwdearing/health-relay.git'
        f"@{PINNED_TAG_PREFIX}"
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


# Every shape below reaches a `healthrelay-receiver-*` ref on GitHub, so each one
# must be reported. The eight marked `#87` were confirmed to leave the whole
# suite green on 3c8a4f4: the guard had been handed a list of shapes to recognise
# instead of a whitelist of shapes to trust.
RELEASE_TRIGGERING_WORKFLOWS: dict[str, str] = {
    "tag_block_sequence": (
        'on:\n  push:\n    tags:\n      - "healthrelay-receiver-*"\njobs: {}\n'
    ),
    "tag_inline_mapping": 'on:\n  push: {tags: ["healthrelay-receiver-*"]}\njobs: {}\n',
    "tag_escaped_quote": (
        'on:\n  push:\n    tags: ["ios-\\"suffix", "healthrelay-receiver-*"]'
        "\njobs: {}\n"
    ),
    "tag_comment_obscured": (
        "on:\n  push:\n    # aligned with the key\n"
        '    tags: ["healthrelay-receiver-*"]\njobs: {}\n'
    ),
    "tag_anchored_alias": (
        'x: &rel ["healthrelay-receiver-*"]\non:\n  push:\n    tags: *rel\njobs: {}\n'
    ),
    "prefix_in_a_comment": (
        "# never publish healthrelay-receiver- tags\non:\n  push:\n"
        "    branches:\n      - main\njobs: {}\n"
    ),
    # --- the eight that failed open on 3c8a4f4 ---
    "87_tags_ignore_star": 'on:\n  push:\n    tags-ignore: ["v*"]\njobs: {}\n',
    "87_tags_glob_all": 'on:\n  push:\n    tags: ["**"]\njobs: {}\n',
    "87_unfiltered_push": "on:\n  push:\n    branches: [main]\njobs: {}\n",
    "87_bare_push_key": "on:\n  push:\njobs: {}\n",
    "87_quoted_keys": '"on":\n  "push":\n    branches: [main]\njobs: {}\n',
    "87_flow_sequence_across_lines": "on: [\n  push,\n  pull_request\n]\njobs: {}\n",
    "87_flow_sequence_inline": "on: [push, pull_request]\njobs: {}\n",
    "87_trailing_content_after_push": "on: push # run on every push\njobs: {}\n",
    "87_anchored_bare_push": "on: &trigger push\njobs: {}\n",
    "87_create_event": "on:\n  create:\njobs: {}\n",
    "87_release_event": "on:\n  release:\n    types: [published]\njobs: {}\n",
    "87_delete_event": "on:\n  delete:\njobs: {}\n",
    "87_event_with_no_rule": (
        "on:\n  push:\n    branches:\n      - main\n  merge_group:\njobs: {}\n"
    ),
    # --- further shapes with no rule, reported for the same reason ---
    "tag_key_beside_a_branch_filter": (
        "on:\n  push:\n    branches:\n      - main\n    tags:\n      - 'ios-v*'\n"
    ),
    "paths_only_push": "on:\n  push:\n    paths: ['src/**']\njobs: {}\n",
    "branch_ignore_written_inline": (
        "on:\n  push:\n    branches-ignore: ['main']\njobs: {}\n"
    ),
    "unknown_push_key": "on:\n  push:\n    refs:\n      - main\njobs: {}\n",
    "tab_indented_trigger": "on:\n\tpush:\njobs: {}\n",
    "no_trigger_key": "jobs: {}\n",
    "empty_trigger": "on:\njobs: {}\n",
}

# Every shape below provably cannot fire for a ref matching the pinned prefix, so
# each one must be cleared. A guard that reported all of these would be as useless
# as one that cleared all of the shapes above.
SAFE_WORKFLOWS: dict[str, str] = {
    "push_filtered_to_branches": (
        "on:\n  push:\n    branches:\n      - main\njobs: {}\n"
    ),
    "push_branch_ignore_block": (
        "on:\n  push:\n    branches-ignore:\n      - 'tmp/**'\njobs: {}\n"
    ),
    "pull_request_only": (
        "on:\n  pull_request:\n    branches:\n      - main\njobs: {}\n"
    ),
    "workflow_dispatch_only": "on:\n  workflow_dispatch:\njobs: {}\n",
    "schedule_only": "on:\n  schedule:\n    - cron: '0 8 * * *'\njobs: {}\n",
    "push_branches_plus_dispatch": (
        "on:\n  push:\n    branches:\n      - main\n  workflow_dispatch:\njobs: {}\n"
    ),
    # Shapes in real use in this repository's own workflows.
    "pull_request_with_block_list": (
        "on:\n  pull_request:\n    branches:\n      - main\n  schedule:\n"
        '    - cron: "0 8 * * *"\n  workflow_dispatch:\n'
    ),
    "pull_request_target_with_inline_types": (
        "on:\n  pull_request_target:\n    types: [opened, edited, synchronize]\n"
        "    branches:\n      - main\n"
    ),
    "workflow_dispatch_with_inputs": (
        "on:\n  workflow_dispatch:\n    inputs:\n      tag:\n"
        "        description: Exact tag\n"
        "        required: true\n        type: string\n"
    ),
}


def test_workflow_guard_reports_every_release_triggering_shape() -> None:
    for name, body in RELEASE_TRIGGERING_WORKFLOWS.items():
        problems = workflow_problems(body)
        assert problems, (
            f"the guard cleared a release-triggering workflow: {name}\n{body}"
        )


def test_workflow_guard_clears_every_positively_recognised_safe_shape() -> None:
    for name, body in SAFE_WORKFLOWS.items():
        assert not workflow_problems(body), f"false alarm on {name}:\n{body}"


def test_a_comment_cannot_hide_the_triggers_that_follow_it() -> None:
    """A comment carries no structure, so it must not end the block it sits in.

    A comment at column zero inside an `on:` mapping used to stop the scan
    there. Every trigger written after it was then invisible: an unfiltered
    `push:` and a `release:` were both cleared. Comments are dropped from the
    structural view and kept only for the textual prefix rule, which is
    deliberately textual.
    """
    for name, hidden in (
        ("unfiltered push", "  push:\n"),
        ("release", "  release:\n    types: [published]\n"),
        ("create", "  create:\n"),
        ("quoted-key push", '  "push":\n'),
        ("pinned tag filter", '  push:\n    tags:\n      - "healthrelay-receiver-*"\n'),
    ):
        body = (
            "on:\n"
            "  workflow_dispatch:\n"
            "# a comment at column zero\n"
            f"{hidden}"
            "jobs: {}\n"
        )
        assert workflow_problems(body), (
            f"a comment hid a {name} trigger from the guard:\n{body}"
        )
        indented = body.replace(
            "# a comment at column zero\n", "  # an indented comment\n"
        )
        assert workflow_problems(indented), (
            f"an indented comment hid a {name} trigger from the guard:\n{indented}"
        )


def test_workflow_guard_is_not_vacuous() -> None:
    # A guard that reported everything, or nothing, would pass the two tests
    # above. Pin both ends, and pin the one reviewed tag filter per file.
    assert RELEASE_TRIGGERING_WORKFLOWS
    assert SAFE_WORKFLOWS
    assert workflow_problems("on:\n  push:\n    branches:\n      - main\n") == []
    assert workflow_problems("on:\n  push:\n    tags:\n      - 'ios-v*'\n")
    assert workflow_problems("on:\n  push:\n    tags-ignore: ['v*']\n")
    assert workflow_problems("on:\n  push:\n    tags: ['**']\n")
    assert workflow_problems('on:\n  push:\n    tags: ["healthrelay-*"]\n')
    assert workflow_problems("on:\n  push:\nhealthrelay-receiver-x\n")

    # A reviewed tag filter clears only where the allowance says so, and only for
    # the patterns the allowance names.
    allowance = REVIEWED_TAG_ALLOWANCES[0]
    reviewed = 'on:\n  push:\n    tags:\n      - "ios-v*"\njobs: {}\n'
    assert not workflow_problems(reviewed, allowance)
    assert workflow_problems(reviewed)
    assert workflow_problems(
        'on:\n  push:\n    tags:\n      - "ios-v*"\n      - "**"\n', allowance
    )
    assert workflow_problems(reviewed, allowance._replace(literals=("app-v*",)))


def test_workflow_guard_fails_closed_on_an_unreadable_workflow() -> None:
    with tempfile.TemporaryDirectory() as raw:
        binary = Path(raw) / "binary.yml"
        _ = binary.write_bytes(b"\x00\xff\xfe not utf8 \x00")

        unreadable = False
        try:
            _ = read_workflow_text(binary)
        except UnreadableWorkflowError:
            unreadable = True
        assert unreadable, "an undecodable workflow was read as text"

        problems = scan_workflow_file(binary)
        assert problems, (
            "an unreadable workflow was ignored; the guard must fail closed"
        )
        assert "fails closed" in problems[0]


def test_scan_workflows_fails_closed_on_an_unreadable_workflow() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        _ = (tmp_path / "ok.yml").write_text(
            "on:\n  push:\n    branches:\n      - main\n", encoding="utf-8"
        )
        _ = (tmp_path / "bad.yml").write_bytes(b"\x00\xff\xfe not utf8 \x00")
        problems = scan_workflows(tmp_path)
    assert any("bad.yml" in problem for problem in problems), problems
    assert not any("ok.yml" in problem for problem in problems), problems


def test_reviewed_allowlist_suppresses_only_the_named_file() -> None:
    body = 'on:\n  push:\n    tags: ["healthrelay-receiver-*"]\n'
    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        _ = (tmp_path / "allowed.yml").write_text(body, encoding="utf-8")
        _ = (tmp_path / "other.yml").write_text(body, encoding="utf-8")
        allowances = ((str(tmp_path / "allowed.yml"), "reviewed: example"),)
        flagged = [
            problem
            for problem in scan_workflows(tmp_path, allowances)
            if "allowed.yml" not in problem
        ]
    assert flagged, "the allowlist must not silence other files"
    assert all("other.yml" in problem for problem in flagged)


def test_allowlisting_a_prefix_mention_keeps_the_other_findings() -> None:
    """The allowance exempts one rule, not the whole file.

    An allowlisted prefix mention used to return early, so any other problem in
    that file went unreported: a comment carrying the prefix alongside an
    unfiltered `push:` produced nothing at all. The prefix rule is the only
    thing an allowance waives.
    """
    body = f"# this workflow never publishes {PINNED_TAG_PREFIX} tags\non:\n  push:\n"
    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        allowed = tmp_path / "allowed.yml"
        _ = allowed.write_text(body, encoding="utf-8")
        with_allowance = scan_workflow_file(
            allowed, ((str(allowed), "reviewed: example"),)
        )
        without = scan_workflow_file(allowed)
    assert not any(PINNED_TAG_PREFIX in problem for problem in with_allowance), (
        f"the allowance did not suppress the prefix finding: {with_allowance}"
    )
    assert len(with_allowance) == len(without) - 1, (
        "the allowance must suppress exactly the prefix finding and nothing else: "
        f"with {with_allowance} vs without {without}"
    )


def test_declared_tag_literals_reports_what_a_workflow_asks_for() -> None:
    ios = (ROOT / ".github/workflows/ios.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    codeql = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    assert declared_tag_literals(ios) == ("ios-v*",)
    assert declared_tag_literals(release) == ("receiver-v*",)
    assert declared_tag_literals(codeql) == ()
