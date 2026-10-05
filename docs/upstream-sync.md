# Syncing from upstream Apple Health AI Bridge

HealthRelay is a fork of [`roian6/apple-health-ai-bridge`](https://github.com/roian6/apple-health-ai-bridge)
(Apache-2.0). Upstream moves slowly — roughly monthly — so this is a manual procedure, run when you
want to know where we stand. Nothing here is automated on purpose: a periodic alert about a
documentation commit trains you to ignore the alerts, and a false alarm costs a human a diff review.

**Where we last synced:** recorded in [`FORK.md`](../FORK.md) as an `Upstream sync, <date>:` line.
That line is the only source of truth. Read it first.

## 1. See what we have not seen

```bash
git fetch upstream
git log --oneline <recorded-sha>..upstream/main
```

`<recorded-sha>` is the upstream commit named in the last `FORK.md` sync line. If that line is
missing, the answer is "we have not synced since the fork point", which is `4818cdc`.

Also worth checking, because a release may have shipped without the commits being interesting:

```bash
gh release list -R roian6/apple-health-ai-bridge --limit 5
git log --oneline upstream/main..upstream/receiver-v1.1.2   # releases ahead of main, if any
```

## 2. Read the direction of a diff before believing it

This is the mistake worth guarding against. `git diff ours upstream` shows our additions as
**deletions**, which reads as though upstream removed our features:

```
$ git diff --stat main upstream/main -- src/
 src/health_bridge/storage/intake_context.py | 1354 --------------
 src/health_bridge/storage/migrations/013_intake_context.sql | 386 ----
```

Nothing was removed. Those ~8,300 lines are **ours** — the intake-context subsystem that upstream
does not have. Always confirm which side owns a change before acting on it:

```bash
git show <sha> --stat          # who wrote it, and what it touched
git log --oneline main..upstream/main   # commits we do not have
```

## 3. Classify each commit

| Change | Action |
|---|---|
| `src/`, `ios/` behaviour fix or feature | **Take it.** Write a test first if it is a fix. |
| `tests/` regression test for a fix we also took | **Take it.** Prefer theirs to a test we wrote. |
| Dependency bump in `uv.lock` | Take it, and re-run the suite; note it in `FORK.md`. |
| `pyproject.toml` or `__init__.py` **version bump** | **Skip.** See §4. |
| Release docs, release guardrails, `component-versions.json` receiver values | **Skip.** See §4. |
| README / brand / public-language guardrails | **Skip by default.** See §4. |

## 4. What must never be taken wholesale

Three categories are ours to decide, not upstream's to set.

**Version bumps.** `pyproject.toml` and `__init__.py` stay at `1.1.1` forever. Our pinned receiver
releases identify a commit with a `healthrelay-receiver-<date>` tag, and `docs/versioning.md`
requires the package version *not* to change, because a bump would make the Mailbox helper's
manifest check demand a notarized `receiver-v<version>` helper release that does not exist. See
`docs/versioning.md:34` and `:45`.

**`component-versions.json` and upstream release bookkeeping.** Ours deliberately declares
`release_scope: "ios"` and the iOS marketing version we actually ship; upstream's
`receiver-v1.1.2` / `1.1.1` values are *upstream* identifiers, not HealthRelay numbers. Reconciling
them would break our release tooling. See `docs/versioning.md:52`.

**README, brand and public-language guardrails — the attribution constraint.** This is the one that
will bite. Upstream commit `40aa1c9` (2026-10-04) adds guardrail assertions that the README must
**not** contain the strings `Health Bridge for AI` or `open-source project behind`, on the grounds
that upstream is now a single named project with no separate product to distinguish from.

Our fork asserts the opposite: `test_product_and_project_names_have_an_explicit_relationship`
requires `HealthRelay is a fork of Apple Health AI Bridge` in `README.md`, `docs/brand.md` and
`assets/brand/README.md`. **Taking upstream's version deletes our rule.**

That rule is not cosmetic. `NOTICE` records that the names `Apple Health AI Bridge`,
`Health Bridge for AI` and `Health Bridge`, and the brand assets, are **not** covered by the
Apache-2.0 grant. Apache-2.0 §4(d) requires retaining attribution to the original authors, and a
user who installs HealthRelay needs to be able to tell which project they are running and where to
report a problem. Upstream's rule is correct for upstream and wrong for a fork that is a distinct
product with a distinct issue tracker.

So: record upstream's commit as a **deliberate skip with its reason**. Skipping is a decision, not
an oversight, and it belongs in `FORK.md`.

## 5. Record what you did

Append one line to `FORK.md`, in date order, naming the upstream commit you synced through:

```
- Upstream sync, <YYYY-MM-DD>: upstream checked through <sha>; took <what>; skipped <what and why>.
```

Then, if you skipped something with a reason worth keeping, say the reason in the line. That line
is what §1 reads next time, so it is worth thirty seconds.

## 6. Verify

The repository's own checks, the same way CI runs them:

```bash
cd <worktree>
PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/guardrails
```

Without `PYTHONPATH=src` the interpreter may import the package from a different checkout and report
phantom failures.

Never run acceptance, tests or builds from the **live receiver tree** — the receiver process runs
from it, and any restart deploys whatever is checked out there. Create worktrees from the review
clone only; if you do not have it, clone this repository somewhere safe and work there.

## Why there is no automation

Upstream shipped five releases between 2026-07-19 and 2026-10-01. A scheduled check would fire
roughly monthly, and most of those firings would be documentation. The cost of a missed upstream
fix is bounded — we would notice it as a missing behaviour, and the fix is reviewable on its own
merits — while the cost of a habituated alert is that we stop looking. Run this when you are
curious, not on a timer.