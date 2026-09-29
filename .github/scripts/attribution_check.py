#!/usr/bin/env python3
"""Fail when a pull request shows tool or agent attribution.

Checks the PR title and body, every commit message in BASE..HEAD, every commit
author and committer identity, and the head branch name. Reads its inputs from
environment variables (never from shell interpolation): PR_TITLE, PR_BODY,
HEAD_REF, BASE_SHA, HEAD_SHA.
"""

import os
import re
import subprocess
import sys

AGENT_WORDS = r"(claude|anthropic|codex|openai|chatgpt|hermes|nousresearch)"
CO_AUTHOR = re.compile(r"^\s*co-authored-by:(?P<who>.*)$", re.IGNORECASE | re.MULTILINE)
TEXT_PATTERNS = [
    (
        "Claude-Session trailer",
        re.compile(r"^\s*claude-session:", re.IGNORECASE | re.MULTILINE),
    ),
    (
        "'Generated with/by <agent>' line",
        re.compile(rf"generated (with|by) \[?{AGENT_WORDS}", re.IGNORECASE),
    ),
    ("claude.ai/code session link", re.compile(r"claude\.ai/code", re.IGNORECASE)),
]
# Bot identities are matched by canonical name or address, never by substring,
# so a person named Claude or Codex is not flagged.
AGENT_NAME = re.compile(
    r"^(claude( code| opus| sonnet| haiku)?( [0-9][0-9.]*)?|codex"
    r"|hermes( agent| backup bot)?|chatgpt.*|.*\[bot\])$",
    re.IGNORECASE,
)
AGENT_EMAIL = re.compile(
    r"@(anthropic|openai|nousresearch)\.com$|^(codex|hermes|hermes-backup)@(fedora\.local|localhost)$",
    re.IGNORECASE,
)
IDENTITY_PARTS = re.compile(r"^\s*(?P<name>.*?)\s*(<(?P<email>[^>]*)>)?\s*$")
BRANCH_ALLOWED = re.compile(
    r"^(feature|bugfix|hotfix|docs|chore|refactor|ci|test)/[a-z0-9][a-z0-9._-]*$"
)


def git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def is_agent_identity(text: str) -> bool:
    match = IDENTITY_PARTS.match(text)
    if not match:
        return False
    name = match.group("name") or ""
    email = match.group("email") or ""
    return bool(AGENT_NAME.match(name.strip()) or AGENT_EMAIL.search(email.strip()))


def branch_problem(head_ref: str) -> str | None:
    if BRANCH_ALLOWED.match(head_ref) or head_ref.startswith("dependabot/"):
        return None
    return (
        f"branch '{head_ref}' must be <type>/<slug> with type feature, bugfix, "
        "hotfix, docs, chore, refactor, ci or test"
    )


def check_text(label: str, text: str | None) -> list[str]:
    found = [
        f"{label}: Co-Authored-By trailer naming an agent"
        for m in CO_AUTHOR.finditer(text or "")
        if is_agent_identity(m.group("who"))
    ][:1]
    return found + [
        f"{label}: {name}" for name, pat in TEXT_PATTERNS if pat.search(text or "")
    ]


def main() -> int:
    problems = []
    branch = branch_problem(os.environ.get("HEAD_REF", ""))
    if branch:
        problems.append(branch)
    problems += check_text("PR title", os.environ.get("PR_TITLE", ""))
    problems += check_text("PR body", os.environ.get("PR_BODY", ""))
    base, head = os.environ["BASE_SHA"], os.environ["HEAD_SHA"]
    for sha in git("rev-list", f"{base}..{head}").split():
        short = sha[:9]
        problems += check_text(
            f"commit {short} message", git("log", "-1", "--format=%B", sha)
        )
        for role, fmt in (("author", "%an <%ae>"), ("committer", "%cn <%ce>")):
            ident = git("log", "-1", f"--format={fmt}", sha).strip()
            if is_agent_identity(ident):
                problems.append(f"commit {short} {role} identity: {ident}")
    if problems:
        print("Attribution check failed:")  # noqa: T201
        for p in problems:
            print(f"  - {p}")  # noqa: T201
        return 1
    print("Attribution check passed.")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
