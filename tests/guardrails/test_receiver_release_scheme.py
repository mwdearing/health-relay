"""Guardrails for the pinned receiver release scheme.

A pinned receiver release is identified by a `healthrelay-receiver-<date>` tag on
an immutable commit, never by a package version bump, and that tag must not
trigger any upstream release workflow. Every pinned tag that has release notes
is checked, not just the first one. These tests are stdlib only and read files
under the repository root.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import NamedTuple, cast

ROOT = Path(__file__).resolve().parents[2]
TAG = "healthrelay-receiver-2026.10.04"
RELEASE_NOTES = ROOT / ".github/release"
NOTES = RELEASE_NOTES / f"notes-{TAG}.md"
VERSIONING = ROOT / "docs/versioning.md"
WORKFLOWS = ROOT / ".github/workflows"
PACKAGE_VERSION = "1.1.1"
PINNED_TAG_PATTERN = re.compile(r"healthrelay-receiver-\d{4}\.\d{2}\.\d{2}")
PINNED_NOTES_GLOB = "notes-healthrelay-receiver-*.md"
TAG_FILTER_KEYS = ("tags", "tags-ignore")
TAG_FILTER_KEY = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>"
    + "|".join(TAG_FILTER_KEYS)
    + r"):[ \t]*(?P<rest>.*)$"
)
ALIAS = re.compile(r"^\*(?P<name>[^\s\[\]{},]+)$")
ANCHOR = re.compile(
    r"(?:\A|[:\-,\[\{])[ \t]*&(?P<name>[^\s\[\]{},]+)[ \t]*(?P<value>.*)$"
)


def strip_yaml_comment(text: str) -> str:
    """Drop a trailing YAML comment from `text`.

    A `#` only starts a comment outside quotes and when it follows whitespace
    or begins the scalar, so `'receiver#v*'` keeps its hash.
    """
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index]
    return text


def unquote_scalar(text: str) -> str:
    """Strip surrounding YAML quotes from an already comment-free scalar."""
    scalar = text.strip()
    if len(scalar) >= 2 and scalar[0] == scalar[-1] and scalar[0] in "\"'":
        return scalar[1:-1]
    return scalar


class TagFilter(NamedTuple):
    """One `tags:`/`tags-ignore:` declaration and the patterns read from it.

    `patterns` is empty when the parser could not read the body. That is never
    treated as "no filter declared" and never as safe: `unreadable_tag_filters`
    turns it into a test failure.
    """

    key: str
    line: int
    patterns: list[str]


def split_flow_sequence(text: str) -> list[str]:
    """Split the body of an inline `[...]` sequence on top-level commas."""
    items: list[str] = []
    current: list[str] = []
    quote = ""
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == ",":
            items.append("".join(current))
            current = []
            continue
        current.append(char)
    items.append("".join(current))
    return [
        scalar
        for scalar in (unquote_scalar(strip_yaml_comment(i)) for i in items)
        if scalar
    ]


def yaml_anchors(lines: list[str]) -> dict[str, list[str]]:
    """Collect the anchors a workflow defines, so `tags: *name` can be resolved.

    GitHub Actions resolves aliases against anchors anywhere in the same
    document, so the whole file is scanned before any `tags:` key is read.
    """
    anchors: dict[str, list[str]] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        anchor = ANCHOR.search(strip_yaml_comment(line).strip())
        if anchor is None:
            continue
        value = anchor.group("value").strip()
        indent = len(line) - len(line.lstrip())
        if not value:
            patterns = block_list_items(lines, index + 1, indent)
        elif value.startswith("["):
            patterns = split_flow_sequence(value[1:-1])
        else:
            patterns = [unquote_scalar(value)]
        if patterns:
            anchors[anchor.group("name")] = patterns
    return anchors


def read_filter_value(
    lines: list[str],
    index: int,
    rest: str,
    indent: int,
    anchors: dict[str, list[str]],
) -> list[str]:
    """Return the patterns in the value written on `lines[index]` as `rest`.

    Handles the three shapes GitHub Actions accepts for a filter: an inline
    flow sequence (possibly spanning lines, possibly anchored), an alias of an
    anchor, and a block sequence below the key. Anything else yields no
    patterns so the meta-guard can report it.
    """
    value = strip_yaml_comment(rest).strip()
    alias = ALIAS.match(value)
    if alias is not None:
        return list(anchors.get(alias.group("name"), []))
    anchor = ANCHOR.match(value)
    if anchor is not None:
        # `tags: &name [...]` declares and uses an anchor in one step.
        value = anchor.group("value").strip()
    if value.startswith("["):
        parts = [value]
        cursor = index + 1
        while not parts[-1].rstrip().endswith("]") and cursor < len(lines):
            # Each line is comment-stripped on its own. Joining first would let
            # an end-of-line comment swallow the patterns on the lines below.
            parts.append(strip_yaml_comment(lines[cursor]).strip())
            cursor += 1
        return split_flow_sequence(" ".join(parts).strip()[1:-1])
    if value:
        return [unquote_scalar(value)]
    return block_list_items(lines, index + 1, indent)


def tag_filter_declarations(text: str) -> list[TagFilter]:
    """Return every `tags:`/`tags-ignore:` declaration with its read patterns."""
    lines = text.splitlines()
    anchors = yaml_anchors(lines)
    found: list[TagFilter] = []
    for index, line in enumerate(lines):
        key = TAG_FILTER_KEY.match(line)
        if key is None:
            continue
        found.append(
            TagFilter(
                key.group("key"),
                index + 1,
                read_filter_value(
                    lines, index, key.group("rest"), len(key.group("indent")), anchors
                ),
            )
        )
    return found


def tag_filter_groups(text: str) -> list[list[str]]:
    """Return every readable tag filter as an ordered group of patterns.

    Each `tags:` key contributes one group, inline (`tags: [a, b]`) or as a
    block list, because GitHub evaluates a single filter as an ordered list in
    which a leading `!` negates and the last matching pattern wins.
    """
    return [
        filters.patterns
        for filters in tag_filter_declarations(text)
        if filters.patterns
    ]


def unreadable_tag_filters(workflows: dict[str, str]) -> list[str]:
    """Fail closed: name every declared tag filter key the parser could not read.

    `push_reaches_any_tag` treats the mere presence of a `tags` key as proof the
    workflow is filtered, so a filter the parser silently skipped would report
    the workflow as safe. Every key in `TAG_FILTER_KEYS` must yield at least one
    pattern; anything else is reported instead of assumed harmless.
    """
    return [
        f"{name}:{filters.line} declares `{filters.key}:` but no filter was read"
        for name, text in sorted(workflows.items())
        for filters in tag_filter_declarations(text)
        if not filters.patterns
    ]


def block_list_items(lines: list[str], start: int, indent: int) -> list[str]:
    """Return the raw `- item` scalars of a block list under a key."""
    items: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # A comment-only line carries no indentation meaning in YAML, so it
            # never ends the block even when it lines up with the key.
            continue
        if not stripped.startswith("-"):
            break
        # A sequence may sit at the key's own indentation or deeper; a
        # shallower item belongs to an outer list and ends this one.
        if len(line) - len(line.lstrip()) < indent:
            break
        items.append(unquote_scalar(strip_yaml_comment(stripped[1:])))
    return [item for item in items if item]


def tag_globs(text: str) -> list[str]:
    """Return every tag filter a workflow declares, inline or as a list."""
    return [pattern for group in tag_filter_groups(text) for pattern in group]


def pinned_release_tags(release_dir: Path) -> list[str]:
    """Return every pinned receiver release tag, derived from its notes files.

    A pinned release ships `.github/release/notes-<tag>.md`, so that file set is
    the registry of tags that must never trigger an upstream release workflow.
    """
    tags = {
        path.name.removeprefix("notes-").removesuffix(".md")
        for path in release_dir.glob(PINNED_NOTES_GLOB)
    }
    return sorted(tag for tag in tags if PINNED_TAG_PATTERN.fullmatch(tag))


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


def github_filters_trigger(patterns: list[str], ref: str) -> bool:
    """True when an ordered tag filter list triggers for `ref`.

    GitHub walks one filter list in order: a leading `!` excludes the ref and
    any other pattern includes it, so the last pattern that matches decides.
    """
    triggered = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if github_filter_matches(pattern[1:], ref):
                triggered = False
        elif github_filter_matches(pattern, ref):
            triggered = True
    return triggered


def push_reaches_any_tag(text: str) -> bool:
    """True when a `push` trigger fires for tag pushes without a positive `tags` filter.

    A push workflow runs for every tag when it has no `branches`/`tags` filter, and
    `tags-ignore` matches every tag it does not exclude, so both reach the tag.
    """
    if re.search(r"^on:[ \t]*(push\b|\[[^\]]*\bpush\b)", text, re.MULTILINE):
        return True
    match = re.search(
        r"^(?P<indent>[ \t]+)push:[ \t]*(?P<rest>.*)\n", text, re.MULTILINE
    )
    if match is None:
        return False
    rest = match.group("rest").strip()
    if rest and rest != "{}":
        # Inline flow mapping such as `push: {branches: [main]}`.
        keys = set(re.findall(r"([A-Za-z-]+)\s*:", rest))
    else:
        indent = len(match.group("indent"))
        body: list[str] = []
        for line in text[match.end() :].splitlines():
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            body.append(line)
        keys = {line.strip().split(":", 1)[0] for line in body if line.strip()}
    if "tags-ignore" in keys:
        return True
    return not ({"tags", "branches", "branches-ignore"} & keys)


def test_no_workflow_tag_trigger_matches_a_pinned_receiver_release_tag() -> None:
    workflows = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
    }
    assert workflows, "no workflows found; the tag filter parser must stay honest"

    groups = {name: tag_filter_groups(text) for name, text in workflows.items()}
    assert any(groups.values()), "no workflow declares a tag filter; parser broken"

    # Fail closed before reasoning about the filters at all: a declared tag key
    # the parser could not read would otherwise be reported as a filtered
    # workflow and let the pinned tag through unnoticed.
    assert not unreadable_tag_filters(workflows)

    tags = pinned_release_tags(RELEASE_NOTES)
    assert tags, "no pinned receiver release notes discovered; parser broken"

    offenders = sorted(
        f"{name}: {patterns}"
        for name, filters in groups.items()
        for patterns in filters
        for tag in tags
        if github_filters_trigger(patterns, tag)
    )
    assert not offenders, (
        f"pinned receiver release tags {tags} must not trigger any workflow "
        f"release; filters: {offenders}"
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


def test_no_workflow_push_trigger_reaches_tags_without_a_filter() -> None:
    offenders = sorted(
        path.name
        for path in WORKFLOWS.glob("*.y*ml")
        if push_reaches_any_tag(path.read_text(encoding="utf-8"))
    )
    assert not offenders, f"push triggers that would run for {TAG}: {offenders}"


def test_push_trigger_detection_is_not_vacuous() -> None:
    assert push_reaches_any_tag("on: push\njobs: {}\n")
    assert push_reaches_any_tag("on: [push, pull_request]\n")
    assert push_reaches_any_tag("on:\n  push:\n  pull_request:\n")
    assert push_reaches_any_tag("on:\n  push:\n    tags-ignore: ['v*']\n")
    assert push_reaches_any_tag("on:\n  push:\n    paths: ['src/**']\n")
    assert not push_reaches_any_tag("on:\n  push:\n    branches: [main]\n")
    assert not push_reaches_any_tag("on:\n  pull_request:\n")
    assert not push_reaches_any_tag("on:\n  push: {branches: [main]}\n")


def test_github_filter_matching_follows_github_semantics() -> None:
    assert github_filter_matches("healthrelay-receiver-[0-9]+.[0-9]+.[0-9]+", TAG)
    assert github_filter_matches("healthrelay-*", TAG)
    assert github_filter_matches("**", TAG)
    assert not github_filter_matches("receiver-v*", TAG)
    assert not github_filter_matches("ios-v*", TAG)
    assert not github_filter_matches("!healthrelay-*", TAG)
    assert not github_filter_matches("v[0-9]+", TAG)


def test_tag_glob_metacharacters_keep_working() -> None:
    assert github_filter_matches("healthrelay-receiver-[0-9]+.[0-9]+.[0-9]+", TAG)
    assert github_filter_matches("healthrelay-**", TAG)
    assert github_filter_matches("**", f"refs/tags/{TAG}")
    assert not github_filter_matches("*", f"refs/tags/{TAG}")
    assert github_filter_matches("healthrelay-?receiver-*", TAG)
    assert not github_filter_matches("healthrelay-?", TAG)
    assert github_filter_matches(TAG, TAG)
    assert not github_filter_matches("healthrelay-receiver-2026.10.0", TAG)


def test_tag_glob_parsing_strips_quotes_and_comments_quote_aware() -> None:
    block = (
        "on:\n"
        "  push:\n"
        "    tags:\n"
        '      - "healthrelay-*" # receiver releases\n'
        "      - 'ios-v*'  # iOS tags\n"
        '      - "receiver#v[0-9]+"  # a hash inside quotes is not a comment\n'
        "      - receiver-rc-*   # bare patterns too\n"
        "      - '!healthrelay-receiver-*'  # pinned releases never publish\n"
    )
    patterns = [
        "healthrelay-*",
        "ios-v*",
        "receiver#v[0-9]+",
        "receiver-rc-*",
        "!healthrelay-receiver-*",
    ]
    assert tag_filter_groups(block) == [patterns]
    inline = (
        "on:\n"
        "  push:\n"
        '    tags: ["healthrelay-*", "ios-v*"]  # receiver and iOS releases\n'
    )
    assert tag_filter_groups(inline) == [["healthrelay-*", "ios-v*"]]

    assert github_filter_matches(patterns[0], TAG)
    assert not github_filter_matches('"healthrelay-*" # receiver releases', TAG), (
        "an unstripped pattern must not be able to hide a real match"
    )
    assert not github_filters_trigger(patterns, TAG)


def test_tag_glob_parsing_handles_blank_lines_and_comment_only_lines() -> None:
    text = (
        "on:\n"
        "  push:\n"
        "    tags:\n"
        "      # receiver releases only\n"
        "\n"
        '      - "healthrelay-*"\n'
        "      - '!healthrelay-receiver-*'\n"
        "  workflow_dispatch:\n"
    )
    assert tag_filter_groups(text) == [["healthrelay-*", "!healthrelay-receiver-*"]]


def test_multiline_flow_sequence_survives_end_of_line_comments() -> None:
    """A comment inside a multiline `[...]` must not swallow the next pattern.

    Joining the lines before stripping the comment turns everything after `#`
    into comment text, which silently hides `healthrelay-*`.
    """
    text = (
        'on:\n  push:\n    tags: ["ios-v*", # app tags\n           "healthrelay-*"]\n'
    )
    assert tag_filter_groups(text) == [["ios-v*", "healthrelay-*"]]
    assert github_filters_trigger(tag_filter_groups(text)[0], TAG), (
        "the hidden pattern is exactly the one that would publish the tag"
    )

    # The same defect when the comment sits on a continuation line rather than on
    # the `tags:` line itself.
    continuation = (
        "on:\n"
        "  push:\n"
        "    tags: [\n"
        '      "ios-v*", # app tags\n'
        '      "healthrelay-*"]\n'
    )
    assert tag_filter_groups(continuation) == [["ios-v*", "healthrelay-*"]]
    assert github_filters_trigger(tag_filter_groups(continuation)[0], TAG)

    # A full-width comment on its own line inside the flow sequence is legal too.
    whole_line = (
        "on:\n"
        "  push:\n"
        "    tags: [\n"
        "      # receiver and app tags\n"
        '      "healthrelay-*",\n'
        '      "ios-v*"\n'
        "    ]\n"
    )
    assert tag_filter_groups(whole_line) == [["healthrelay-*", "ios-v*"]]


def test_comment_line_aligned_with_the_key_does_not_end_the_block() -> None:
    """A comment at the key's own indentation is valid YAML, not the block end."""
    text = (
        "on:\n"
        "  push:\n"
        "    tags:\n"
        "    # receiver releases are published elsewhere\n"
        '      - "healthrelay-*"\n'
        "    workflow_dispatch:\n"
    )
    assert tag_filter_groups(text) == [["healthrelay-*"]]
    assert github_filters_trigger(tag_filter_groups(text)[0], TAG)


def test_anchored_and_aliased_tag_filters_are_read() -> None:
    """GitHub Actions supports anchors and aliases in a `tags:` filter."""
    anchored = 'on:\n  push:\n    tags: &release_tags ["healthrelay-*"]\n'
    assert tag_filter_groups(anchored) == [["healthrelay-*"]]
    assert github_filters_trigger(tag_filter_groups(anchored)[0], TAG)

    aliased = (
        "on:\n"
        "  push:\n"
        "    tags: *release_tags\n"
        "  workflow_call:\n"
        "x-tags: &release_tags\n"
        '  - "healthrelay-*"\n'
    )
    assert tag_filter_groups(aliased) == [["healthrelay-*"]]
    assert github_filters_trigger(tag_filter_groups(aliased)[0], TAG)

    quoted = (
        "on:\n"
        "  push:\n"
        "    tags: &release_tags ['healthrelay-*'] # anchored\n"
        "  workflow_call:\n"
        "    tags: *release_tags\n"
    )
    assert tag_filter_groups(quoted) == [["healthrelay-*"], ["healthrelay-*"]]


def test_indentationless_block_sequence_under_a_tag_key_is_read() -> None:
    """`tags:` followed by a `-` at the key's own indentation is valid YAML."""
    text = 'on:\n  push:\n    tags:\n    - "healthrelay-*"\n    branches: [main]\n'
    assert tag_filter_groups(text) == [["healthrelay-*"]]
    assert github_filters_trigger(tag_filter_groups(text)[0], TAG)

    # Indentationless items followed by a sibling key still ends the list.
    sibling = "on:\n  push:\n    tags:\n    - 'ios-v*'\n    branches: [main]\n"
    assert tag_filter_groups(sibling) == [["ios-v*"]]


def test_meta_guard_fails_when_a_tag_key_declares_no_readable_filter() -> None:
    """The fail-closed guard: a declared key with no extracted filter is a bug.

    `push_reaches_any_tag` sees the key and calls the workflow filtered, so an
    unreadable body has to be reported rather than assumed safe.
    """
    nested_mapping = "on:\n  push:\n    tags:\n      pattern: healthrelay-*\n"
    dangling_alias = "on:\n  push:\n    tags: *never_defined\n"
    for text in (nested_mapping, dangling_alias):
        declarations = tag_filter_declarations(text)
        assert [d.key for d in declarations] == ["tags"], (
            f"the key itself must still be seen: {text!r}"
        )
        assert tag_filter_groups(text) == [], (
            f"no filter may be invented from an unreadable body: {text!r}"
        )
        reported = unreadable_tag_filters({"wf.yml": text})
        assert len(reported) == 1, (
            f"the meta-guard must name the workflow and the key: {text!r}"
        )
        assert "wf.yml:3" in reported[0], reported
        assert "`tags:`" in reported[0], reported
        assert not push_reaches_any_tag(text), (
            "an unfiltered push must not be reported as safe"
        )


def test_meta_guard_accepts_every_tag_filter_key_the_repository_declares() -> None:
    unreadable = unreadable_tag_filters(
        {
            path.name: path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.y*ml")
        }
    )
    assert not unreadable, f"tag filters the parser could not read: {unreadable}"

    # The guard must actually fire on the repository, not just on synthetic text.
    unreadable_workflow = WORKFLOWS / "release.yml"
    body = unreadable_workflow.read_text(encoding="utf-8")
    broken = body.replace('tags: ["receiver-v*"]', "tags: *never_defined")
    assert broken != body
    reported = unreadable_tag_filters({unreadable_workflow.name: broken})
    assert len(reported) == 1, reported
    assert "release.yml:5" in reported[0], reported


def test_tags_ignore_is_read_as_a_filter_too() -> None:
    """`tags-ignore` is guarded like `tags`, not skipped by the key matcher."""
    text = "on:\n  push:\n    tags-ignore: ['healthrelay-receiver-*']\n"
    assert tag_filter_declarations(text) == [
        TagFilter("tags-ignore", 3, ["healthrelay-receiver-*"])
    ]
    assert tag_filter_groups(text) == [["healthrelay-receiver-*"]]
    assert not unreadable_tag_filters({"wf.yml": text})
    assert push_reaches_any_tag(text), (
        "a tags-ignore filter never includes the pinned tag, it excludes it"
    )

    unreadable = "on:\n  push:\n    tags-ignore:\n      pattern: x\n"
    reported = unreadable_tag_filters({"wf.yml": unreadable})
    assert len(reported) == 1, reported
    assert "`tags-ignore:`" in reported[0], reported


def test_meta_guard_only_guards_keys_the_guardrail_reasons_about() -> None:
    """Guarding must be non-vacuous: a workflow with no tag key reports nothing."""
    branches_only = "on:\n  push:\n    branches: [main]\n"
    assert tag_filter_declarations(branches_only) == []
    assert not unreadable_tag_filters({"none.yml": branches_only})
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    real = tag_filter_declarations(text)
    assert [(d.key, d.patterns) for d in real] == [("tags", ["receiver-v*"])], (
        "release.yml still yields exactly one readable tag filter"
    )


def test_ordered_tag_negation_is_evaluated_across_the_whole_list() -> None:
    inline = "on:\n  push:\n    tags: ['healthrelay-*', '!healthrelay-receiver-*']\n"
    block = (
        "on:\n"
        "  push:\n"
        "    tags:\n"
        "      - 'healthrelay-*'\n"
        "      - '!healthrelay-receiver-*'\n"
    )
    for text in (inline, block):
        assert tag_filter_groups(text) == [["healthrelay-*", "!healthrelay-receiver-*"]]

    # Exclusion last: the negation wins over the earlier inclusion.
    assert not github_filters_trigger(["healthrelay-*", "!healthrelay-receiver-*"], TAG)
    # Exclusion first: the later inclusion wins and the tag does trigger.
    assert github_filters_trigger(["!healthrelay-receiver-*", "healthrelay-*"], TAG)
    assert github_filters_trigger(
        ["healthrelay-*", "!healthrelay-receiver-*", "healthrelay-receiver-*"], TAG
    )
    assert github_filters_trigger(["healthrelay-*", "!ios-v*"], TAG)
    assert github_filters_trigger(["ios-v*", "healthrelay-*"], TAG)
    assert github_filters_trigger(["healthrelay-*"], TAG)
    assert not github_filters_trigger(["ios-v*"], TAG)
    assert not github_filters_trigger([], TAG)
    assert not github_filters_trigger(["!healthrelay-receiver-*"], TAG)


def test_pinned_release_tags_come_from_every_release_notes_file(
    tmp_path: Path,
) -> None:
    _ = (tmp_path / f"notes-{TAG}.md").write_text("first\n", encoding="utf-8")
    _ = (tmp_path / "notes-healthrelay-receiver-2026.11.07.md").write_text(
        "second\n", encoding="utf-8"
    )
    _ = (tmp_path / "notes-receiver-v1.1.1.md").write_text(
        "not a pinned receiver release\n", encoding="utf-8"
    )
    _ = (tmp_path / "notes-healthrelay-receiver-template.md").write_text(
        "not a dated release\n", encoding="utf-8"
    )
    assert pinned_release_tags(tmp_path) == [
        "healthrelay-receiver-2026.10.04",
        "healthrelay-receiver-2026.11.07",
    ]


def test_pinned_release_tag_discovery_is_not_vacuous() -> None:
    tags = pinned_release_tags(RELEASE_NOTES)
    assert tags, "no pinned receiver release tags discovered"
    assert TAG in tags, f"the published pinned release {TAG} is unguarded"
    assert tags == sorted(tags)
    assert all(
        re.fullmatch(r"healthrelay-receiver-\d{4}\.\d{2}\.\d{2}", tag) for tag in tags
    )


def test_every_pinned_release_tag_has_its_own_release_notes() -> None:
    notes = sorted(path.name for path in RELEASE_NOTES.glob(PINNED_NOTES_GLOB))
    assert notes, "the pinned receiver release notes directory must stay populated"
    assert [f"notes-{tag}.md" for tag in pinned_release_tags(RELEASE_NOTES)] == notes
