"""Guardrails for the pinned receiver release scheme.

A pinned receiver release is identified by a `healthrelay-receiver-<date>` tag on
an immutable commit, never by a package version bump, and that tag must not
trigger any upstream release workflow.

The release-trigger guard in this module is deliberately **textual**, not a YAML
shape parser (issue #85). The shape parser it replaced had one failure mode
that matters and one that does not: it silently certified a release-triggering
workflow as safe whenever
the workflow's YAML used a shape the parser did not model. A false alarm that
costs a human thirty seconds is acceptable here; a false all-clear ships a
receiver release into someone else's release pipeline. So the guard never claims
to understand a workflow. It asserts only what can be read off the raw bytes:

1. no file under `.github/workflows` (recursively) contains the literal prefix
   `healthrelay-receiver-` at all -- including inside a comment, because "this
   workflow will never fire for that prefix" written in a comment is exactly
   the sort of claim that goes stale;
2. no file under `.github/workflows` fails to decode as UTF-8 text, because a
   file the guard cannot read is a file the guard cannot clear;
3. no `push` trigger in any workflow is left in a shape a human cannot read its
   ref filter off at a glance (bare `push:`, flow-style `on: [...]`, or an
   inline `push: {branches: [...]}` mapping). Those fail closed.

Do not "improve" this back into a parser. If you want the guard to tolerate a
workflow shape, add a deliberate allowance with a stated reason
(`ALLOWED_PREFIX_APPEARANCES`) or ask a human to look.

These tests are stdlib only and read files under the repository root.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
PINNED_TAG_PREFIX = "healthrelay-receiver-"
PINNED_TAG_RE = re.compile(r"healthrelay-receiver-\d{4}\.\d{2}\.\d{2}")
WORKFLOWS = ROOT / ".github/workflows"
RELEASE_NOTES = ROOT / ".github/release"
VERSIONING = ROOT / "docs/versioning.md"
PACKAGE_VERSION = "1.1.1"

# Places that may name a pinned receiver tag. Every tag named in any of them
# must have its own release-notes file.
PINNED_TAG_SOURCES = (
    RELEASE_NOTES,
    ROOT / "docs",
    ROOT / "README.md",
    ROOT / "FORK.md",
)

# Reviewed exceptions for rule 1: (path relative to the repository root, reason).
# Empty by default. A deliberate appearance of the pinned prefix in a workflow
# is a code change that has to state why, not a deleted assertion.
ALLOWED_PREFIX_APPEARANCES: tuple[tuple[str, str], ...] = ()


class UnreadableWorkflowError(Exception):
    """A workflow file could not be decoded as UTF-8 text.

    Carries the offending path rather than a formatted message, so the wording
    lives with the class instead of at each raise site.
    """

    def __init__(self, path: object) -> None:
        self.path = path
        super().__init__(f"{path}: not decodable as UTF-8 text")


# --------------------------------------------------------------------------
# Pinned release tag discovery
# --------------------------------------------------------------------------


def pinned_release_tags(notes_dir: Path = RELEASE_NOTES) -> tuple[str, ...]:
    """Every pinned receiver tag that has release notes in `notes_dir`.

    Discovered from the notes filenames, never hard-coded to one literal, so a
    second pinned release is picked up without editing this module.
    """
    tags = []
    for path in sorted(notes_dir.glob(f"notes-{PINNED_TAG_PREFIX}*.md")):
        candidate = path.name[len("notes-") : -len(".md")]
        if PINNED_TAG_RE.fullmatch(candidate):
            tags.append(candidate)
    return tuple(sorted(tags))


def release_notes_path(tag: str, notes_dir: Path = RELEASE_NOTES) -> Path:
    return notes_dir / f"notes-{tag}.md"


def pinned_tags_named_in_docs() -> set[str]:
    """Pinned tags mentioned in release notes, docs, README.md or FORK.md."""
    named: set[str] = set()
    for source in PINNED_TAG_SOURCES:
        paths = sorted(source.rglob("*.md")) if source.is_dir() else [source]
        for path in paths:
            if path.is_file():
                named.update(PINNED_TAG_RE.findall(path.read_text(encoding="utf-8")))
    return named


# --------------------------------------------------------------------------
# The conservative textual guard
# --------------------------------------------------------------------------

PUSH_KEY_LINE = re.compile(
    r"^(?P<indent>[ \t]*)push\s*:[ \t]*(?P<rest>.*)$", re.MULTILINE
)
ON_BARE_PUSH = re.compile(r"^on\s*:[ \t]*push[ \t]*$", re.MULTILINE)
ON_FLOW_STYLE = re.compile(r"^on\s*:[ \t]*[\[{].*\bpush\b", re.MULTILINE)
LIST_ITEM = re.compile(r"^[ \t]*-[ \t]*\S")
TAG_REF_KEYS = ("tags", "tags-ignore")
BRANCH_REF_KEYS = ("branches", "branches-ignore")


def _block_body(text: str, indent: int) -> list[str]:
    """Lines indented deeper than `indent`, up to the next line that is not."""
    body: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return body


def push_trigger_problems(text: str) -> list[str]:
    """Report `push` triggers whose ref filter is not readable at a glance.

    A push trigger reaches tag pushes unless it is filtered to branches, so the
    guard wants each one to state its filter in block form: a `tags`/`tags-ignore`
    key (inline or block -- the key is the point), or a `branches`/
    `branches-ignore` key written as a block list. Everything else -- a bare
    `push:`, `on: push`, a flow-style `on: [push]`, an inline `push: {...}`
    mapping, or a trigger with no ref key at all -- is reported rather than
    judged, because judging it means modelling YAML again.
    """
    problems: list[str] = [
        _line_problem(text, match, "`on: push`")
        for match in ON_BARE_PUSH.finditer(text)
    ]
    problems += [
        _line_problem(text, match, "flow-style `on:` trigger")
        for match in ON_FLOW_STYLE.finditer(text)
    ]
    problems += [
        problem
        for problem in (
            _push_key_problem(text, match) for match in PUSH_KEY_LINE.finditer(text)
        )
        if problem is not None
    ]
    return problems


def _line_number(text: str, match: re.Match[str]) -> int:
    return text[: match.start()].count("\n") + 1


def _line_problem(text: str, match: re.Match[str], description: str) -> str:
    return f"line {_line_number(text, match)}: {description}"


def _push_key_problem(text: str, match: re.Match[str]) -> str | None:
    """The problem with one `push:` key's ref filter, or None if it is readable."""
    line = _line_number(text, match)
    rest = match.group("rest").strip()
    if rest and rest != "{}":
        return f"line {line}: inline `push:{rest}`"
    body = _block_body(text[match.end() :], len(match.group("indent")))
    if not body:
        return f"line {line}: `push:` with no ref filter"
    if _ref_filter_is_readable(body):
        return None
    return f"line {line}: `push:` ref filter is not a readable block form"


def _ref_filter_is_readable(body: list[str]) -> bool:
    """Whether a `push:` block states its filter in a form a human can read.

    A `tags`/`tags-ignore` key is accepted however it is written, because the
    key itself is the readable statement. A `branches`/`branches-ignore` key is
    accepted only as a block list, so that `branches: [main]` still reaches a
    human. Anything else -- no ref key at all, or a filter that cannot be read
    off the text -- is not accepted, because accepting it means judging YAML.
    """
    child_indent = min(len(line) - len(line.lstrip()) for line in body)
    children = [line for line in body if len(line) - len(line.lstrip()) == child_indent]
    names = [child.strip().split(":", 1)[0].strip() for child in children]
    if any(name in TAG_REF_KEYS for name in names):
        return True
    if not any(name in BRANCH_REF_KEYS for name in names):
        return False
    return _branch_keys_are_block_lists(body, child_indent)


def _branch_keys_are_block_lists(body: list[str], child_indent: int) -> bool:
    for index, raw in enumerate(body):
        if len(raw) - len(raw.lstrip()) != child_indent:
            continue
        name, _, value = raw.strip().partition(":")
        if name not in BRANCH_REF_KEYS:
            continue
        following = body[index + 1] if index + 1 < len(body) else ""
        if value.strip() or not LIST_ITEM.match(following):
            return False
    return True


def workflow_problems(text: str) -> list[str]:
    """Every way `text` (one workflow) fails the conservative guard."""
    problems = []
    if PINNED_TAG_PREFIX in text:
        problems.append(
            f"the file mentions {PINNED_TAG_PREFIX!r}; a pinned receiver release "
            "must never appear in a workflow, not even in a comment"
        )
    problems += push_trigger_problems(text)
    return problems


def read_workflow_text(path: Path) -> str:
    """Read a workflow as text, failing closed when it cannot be decoded."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise UnreadableWorkflowError(path) from exc


def scan_workflow_file(
    path: Path, allowances: tuple[tuple[str, str], ...] = ()
) -> list[str]:
    """Problems in one workflow file; never silently skips an unreadable file."""
    try:
        text = read_workflow_text(path)
    except UnreadableWorkflowError as exc:
        return [
            f"{exc}; the guard fails closed rather than certifying a workflow "
            "it cannot read"
        ]
    try:
        relative = str(path.relative_to(ROOT))
    except ValueError:
        relative = str(path)
    allowed = dict(allowances)
    if relative in allowed and PINNED_TAG_PREFIX in text:
        return []
    return [f"{relative}: {problem}" for problem in workflow_problems(text)]


def scan_workflows(
    workflows: Path = WORKFLOWS, allowances: tuple[tuple[str, str], ...] = ()
) -> list[str]:
    files = sorted(p for p in workflows.rglob("*") if p.is_file())
    assert files, f"no workflows found under {workflows}; the guard must stay honest"
    problems: list[str] = []
    for path in files:
        problems += scan_workflow_file(path, allowances)
    return problems


# --------------------------------------------------------------------------
# GitHub filter semantics (kept as a documented reference, used by the
# demonstration below -- the guard itself never matches filters)
# --------------------------------------------------------------------------


def github_filter_matches(pattern: str, ref: str) -> bool:
    """Match a ref against a GitHub Actions filter pattern.

    `*` matches any run without `/`, `**` any run, `?` zero or one and `+` one
    or more of the preceding character, `[...]` a character class. A leading
    `!` negates and never counts as a positive match here.
    """
    if pattern.startswith("!"):
        return False
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char in "?+":
            out.append(char)
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                out.append(pattern[i : end + 1])
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    return re.fullmatch("".join(out), ref) is not None


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
        (tmp_path / f"notes-{PINNED_TAG_PREFIX}2099.01.02.md").write_text(
            "x", encoding="utf-8"
        )
        (tmp_path / "notes-receiver-v9.9.9.md").write_text("x", encoding="utf-8")
        discovered = pinned_release_tags(tmp_path)
    assert discovered == ("healthrelay-receiver-2099.01.02",)
    assert not set(discovered) & set(tags)


def test_every_pinned_tag_named_in_docs_has_its_own_release_notes() -> None:
    named = pinned_tags_named_in_docs()
    assert named, "no pinned receiver tag is named in the docs at all"
    missing = sorted(tag for tag in named if not release_notes_path(tag).is_file())
    assert not missing, f"pinned tags without release notes: {missing}"


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
        assert re.search(r"\b[0-9a-f]{7,40}\b", notes) is not None, (
            "notes name the commit"
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


# The shapes the removed parser used to clear silently. Each must be flagged.
RELEASE_TRIGGERING_WORKFLOWS = {
    "block_sequence": """on:
  push:
    tags:
      - "healthrelay-receiver-*"
jobs: {}
""",
    "inline_mapping": """on:
  push: {tags: ["healthrelay-receiver-*"]}
jobs: {}
""",
    "escaped_quote": """on:
  push:
    tags: ["ios-\\"suffix", "healthrelay-receiver-*"]
jobs: {}
""",
    "comment_obscured": """on:
  push:
    # a comment aligned with the key
    tags: ["healthrelay-receiver-*"]
jobs: {}
""",
    "anchored_sequence": """x: &rel ["healthrelay-receiver-*"]
on:
  push:
    tags: *rel
jobs: {}
""",
    "prefix_in_a_comment": """# never publish healthrelay-receiver- tags from here
on:
  push:
    branches: [main]
jobs: {}
""",
    "unfiltered_push": """on:
  push:
    branches: [main]
jobs: {}
""",
    "bare_on_push": """on: push
jobs: {}
""",
    "flow_trigger_list": """on: [push, pull_request]
jobs: {}
""",
    "inline_push_mapping": """on:
  push: {branches: [main]}
jobs: {}
""",
    "paths_only_push": """on:
  push:
    paths: ['src/**']
jobs: {}
""",
}

READABLE_WORKFLOWS = {
    "tag_filtered_push": """on:
  push:
    tags: ["receiver-v*"]
""",
    "tag_block_list": """on:
  push:
    tags:
      - "ios-v*"
""",
    "tags_ignore": """on:
  push:
    tags-ignore: ['v*']
""",
    "branch_block_list": """on:
  push:
    branches:
      - main
""",
    "no_push_trigger": """on:
  pull_request:
    branches:
      - main
""",
}


def test_workflow_guard_flags_every_release_triggering_shape() -> None:
    for name, body in RELEASE_TRIGGERING_WORKFLOWS.items():
        problems = workflow_problems(body)
        assert problems, (
            f"the guard cleared a release-triggering workflow: {name}\n{body}"
        )


def test_workflow_guard_clears_the_readable_shapes() -> None:
    for name, body in READABLE_WORKFLOWS.items():
        assert not workflow_problems(body), f"false alarm on {name}:\n{body}"


def test_workflow_guard_is_not_vacuous() -> None:
    # A guard that flags everything (or nothing) would pass the tests above.
    assert RELEASE_TRIGGERING_WORKFLOWS
    assert READABLE_WORKFLOWS
    assert not workflow_problems("on:\n  push:\n    tags: ['ios-v*']\n")
    assert workflow_problems(
        "on:\n  push:\n    tags: ['ios-v*']\nhealthrelay-receiver-x\n"
    )
    assert workflow_problems("on: push\n")


def test_workflow_guard_fails_closed_on_an_unreadable_workflow() -> None:
    with tempfile.TemporaryDirectory() as raw:
        binary = Path(raw) / "binary.yml"
        binary.write_bytes(b"\x00\xff\xfe not utf8 \x00")

        unreadable = False
        try:
            read_workflow_text(binary)
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
        (tmp_path / "ok.yml").write_text(
            "on:\n  push:\n    branches:\n      - main\n", encoding="utf-8"
        )
        (tmp_path / "bad.yml").write_bytes(b"\x00\xff\xfe not utf8 \x00")
        problems = scan_workflows(tmp_path)
    assert any("bad.yml" in problem for problem in problems), problems
    assert not any("ok.yml" in problem for problem in problems), problems


def test_reviewed_allowlist_suppresses_only_the_named_file() -> None:
    body = 'on:\n  push:\n    tags: ["healthrelay-receiver-*"]\n'
    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        (tmp_path / "allowed.yml").write_text(body, encoding="utf-8")
        (tmp_path / "other.yml").write_text(body, encoding="utf-8")
        allowances = ((str(tmp_path / "allowed.yml"), "reviewed: example"),)
        flagged = [
            problem
            for problem in scan_workflows(tmp_path, allowances)
            if "allowed.yml" not in problem
        ]
    assert flagged, "the allowlist must not silence other files"
    assert all("other.yml" in problem for problem in flagged)


def test_real_workflows_all_declare_a_readable_ref_filter() -> None:
    files = sorted(p for p in WORKFLOWS.rglob("*") if p.is_file())
    assert files
    offenders: dict[str, list[str]] = {}
    for path in files:
        try:
            text = read_workflow_text(path)
        except UnreadableWorkflowError as exc:
            offenders[path.name] = [str(exc)]
            continue
        problems = push_trigger_problems(text)
        if problems:
            offenders[path.name] = problems
    assert not offenders, f"unreadable push triggers: {offenders}"


def test_github_filter_matching_follows_github_semantics() -> None:
    tag = pinned_release_tags()[0]
    assert github_filter_matches("healthrelay-receiver-[0-9]+.[0-9]+.[0-9]+", tag)
    assert github_filter_matches("healthrelay-*", tag)
    assert github_filter_matches("**", tag)
    assert not github_filter_matches("receiver-v*", tag)
    assert not github_filter_matches("ios-v*", tag)
    assert not github_filter_matches("!healthrelay-*", tag)
    assert not github_filter_matches("v[0-9]+", tag)


def test_filter_semantics_explain_why_the_guard_is_textual() -> None:
    # A guard that listed every matching filter would have to be right about
    # all of these; the textual guard only has to refuse the prefix outright.
    tag = pinned_release_tags()[0]
    matching = [
        "healthrelay-*",
        "healthrelay-receiver-*",
        "healthrelay-re*",
        "healthrelay-receiver?*",
        "healthrelay-*-receiver-*",
        "**[healthrelay]",
        "healthrelay-receiver-[0-9]+.[0-9]+.[0-9]+",
        "*",
        "**",
    ]
    hits = [pattern for pattern in matching if github_filter_matches(pattern, tag)]
    assert hits, "the matcher must still demonstrate matches, or this test is vacuous"
    assert "healthrelay-*" in hits
