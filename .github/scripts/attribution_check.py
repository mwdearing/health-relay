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

AGENTS = r"(claude|anthropic|codex|openai|hermes|nousresearch)"
TEXT_PATTERNS = [
    ("Co-Authored-By trailer naming an agent", re.compile(rf"^\s*co-authored-by:.*{AGENTS}", re.I | re.M)),
    ("Claude-Session trailer", re.compile(r"^\s*claude-session:", re.I | re.M)),
    ("'Generated with/by Claude Code' line", re.compile(r"generated (with|by) \[?claude code", re.I)),
    ("claude.ai/code session link", re.compile(r"claude\.ai/code", re.I)),
]
IDENTITY = re.compile(rf"noreply@anthropic\.com|{AGENTS}", re.I)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def check_text(label, text):
    return [f"{label}: {name}" for name, pat in TEXT_PATTERNS if pat.search(text or "")]


def main():
    problems = []
    head_ref = os.environ.get("HEAD_REF", "")
    if head_ref.startswith("claude/"):
        problems.append(f"branch name '{head_ref}' starts with claude/ (use mwd/<slug>)")
    problems += check_text("PR title", os.environ.get("PR_TITLE", ""))
    problems += check_text("PR body", os.environ.get("PR_BODY", ""))
    base, head = os.environ["BASE_SHA"], os.environ["HEAD_SHA"]
    for sha in git("rev-list", f"{base}..{head}").split():
        short = sha[:9]
        problems += check_text(f"commit {short} message", git("log", "-1", "--format=%B", sha))
        for role, fmt in (("author", "%an <%ae>"), ("committer", "%cn <%ce>")):
            ident = git("log", "-1", f"--format={fmt}", sha).strip()
            if IDENTITY.search(ident):
                problems.append(f"commit {short} {role} identity: {ident}")
    if problems:
        print("Attribution check failed:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Attribution check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
