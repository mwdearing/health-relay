"""Guardrails for the upstream-sync procedure in `docs/upstream-sync.md`.

The procedure's correctness depends on facts that live in prose: which upstream
commit was last synced, and where the sync entries sit relative to each other.
`docs/upstream-sync.md` §1 tells a maintainer to take the *newest*
`Upstream sync, <date>:` entry in `FORK.md` as the checkpoint for the next sync.
That is only true while the entries are newest-first, and nothing else checked
it — an entry appended at the end would silently become the checkpoint, and the
next sync would compare against a stale SHA while still recording itself complete.

These assertions are stdlib only and read files under the repository root.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORK_MD = ROOT / "FORK.md"
PROCEDURE = ROOT / "docs" / "upstream-sync.md"

# `Upstream sync, 2026-10-05: upstream checked through `40aa1c9`…`
SYNC_ENTRY = re.compile(r"^- Upstream sync, (?P<date>\d{4}-\d{2}-\d{2}):", re.MULTILINE)
# The upstream commit the entry says we synced through.
SYNCED_THROUGH = re.compile(r"checked through `?(?P<sha>[0-9a-f]{7,40})`?")


def test_fork_md_exists_and_declares_the_section() -> None:
    assert FORK_MD.is_file(), f"missing {FORK_MD.relative_to(ROOT)}"
    text = FORK_MD.read_text(encoding="utf-8")
    assert "## Modifications (newest first)" in text, (
        "FORK.md no longer declares its Modifications section as newest-first, which "
        "is what makes the newest sync entry the checkpoint for the next sync"
    )


def test_sync_entries_are_newest_first() -> None:
    """The order the procedure depends on, enforced rather than assumed.

    A maintainer following the section's stated ordering inserts the next entry
    above the others. If the existing entries are not newest-first, the *last*
    line in the file is not the newest sync, and `docs/upstream-sync.md` §1
    would hand the next sync a stale SHA.
    """
    text = FORK_MD.read_text(encoding="utf-8")
    dates = SYNC_ENTRY.findall(text)
    assert dates, (
        "FORK.md has no `Upstream sync, <date>:` entries, so the upstream sync "
        "checkpoint is undefined"
    )
    assert len(dates) == len(set(dates)), f"duplicate sync dates: {dates}"
    assert dates == sorted(dates, reverse=True), (
        f"upstream sync entries are not newest-first: {dates}. The procedure reads "
        "the newest entry as the checkpoint, so appending at the end would make it "
        "wrong."
    )


def test_every_sync_entry_names_the_commit_it_synced_through() -> None:
    """Each entry must carry the SHA the next sync starts from.

    An entry that records a date but no SHA cannot serve as a checkpoint, and
    the failure is silent: the next sync would fall back to the fork point and
    re-examine commits already reviewed.
    """
    text = FORK_MD.read_text(encoding="utf-8")
    for match in SYNC_ENTRY.finditer(text):
        date = match.group("date")
        following = text[match.end() : match.end() + 400]
        head = following.split("\n- ", 1)[0]
        synced = SYNCED_THROUGH.search(head)
        assert synced is not None, (
            f"the {date} sync entry names no upstream commit; the next sync has "
            "nothing to compare against"
        )
        assert re.fullmatch(r"[0-9a-f]{7,40}", synced.group("sha")), (
            f"the {date} entry's commit {synced.group('sha')!r} is not a sha"
        )


def test_the_procedure_points_at_the_newest_entry_not_the_last_line() -> None:
    """The procedure's instruction must match how FORK.md is actually ordered.

    "The last sync line" is wrong for a newest-first list and right for an
    append-at-the-end one. Whichever the file uses, the doc and the file have to
    agree, or §1 hands out a stale SHA.
    """
    procedure = PROCEDURE.read_text(encoding="utf-8")
    assert "recorded-sha" in procedure, (
        "docs/upstream-sync.md no longer defines <recorded-sha>; §1 cannot say "
        "where to find the checkpoint"
    )
    assert "newest" in procedure, (
        "docs/upstream-sync.md does not say the checkpoint is the *newest* entry, "
        "which is what makes it unambiguous against a newest-first list"
    )
    assert (
        "physically last" in procedure or "not the one physically last" in procedure
    ), (
        "the procedure should say explicitly that newest-first does not mean last "
        "in the file; that ambiguity is what this guardrail exists to close"
    )
    assert "test_sync_entries_are_newest_first" in procedure, (
        "the procedure should name the guardrail that enforces the ordering, so a "
        "reader knows the convention is checked rather than remembered"
    )
