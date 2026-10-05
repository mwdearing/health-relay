# Syncing from upstream Apple Health AI Bridge

HealthRelay is a fork of [`roian6/apple-health-ai-bridge`](https://github.com/roian6/apple-health-ai-bridge)
(Apache-2.0). Upstream moves slowly — roughly monthly — so this is a manual procedure, run when you
want to know where we stand. Nothing here is automated on purpose: a periodic alert about a
documentation commit trains you to ignore the alerts, and a false alarm costs a human a diff review.

**Where we last synced:** recorded in [`FORK.md`](../FORK.md) as an `Upstream sync, <date>:` line.
That line is the only source of truth. Read it first.

## 1. See what we have not seen

Remotes live in local Git configuration and are **not** committed with the repository, so a fresh
clone has only `origin`. If `git remote -v` does not list `upstream`, add it first:

```bash
git remote add upstream https://github.com/roian6/apple-health-ai-bridge.git
git remote set-url --push upstream DISABLED   # nothing should ever be pushed upstream
```

The disabled push URL is deliberate: it makes an accidental `git push upstream …` fail rather than
offer to open a pull request against the project we forked from.

```bash
git fetch upstream
git log --oneline <recorded-sha>..upstream/main
```

`<recorded-sha>` is the upstream commit named in the last `FORK.md` sync line. If that line is
missing, the answer is "we have not synced since the fork point", which is `4818cdc`.

Also worth checking, because a release may have shipped without the commits being interesting:

```bash
gh release list -R roian6/apple-health-ai-bridge --limit 5
```

Then, to see whether a release tag is ahead of `main`:

```bash
git fetch upstream --tags
git log --oneline upstream/main..refs/tags/receiver-v1.1.2
```

Use `refs/tags/…` rather than `upstream/receiver-v1.1.2`. A release tag is a tag, not a branch, and
a plain `git fetch upstream` does not create a remote-tracking ref for it — the short form fails
with "unknown revision" and looks like the release is missing.

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
| Dependency bump in `uv.lock` | Take the dependency delta, **not** the file. See below. |
| `pyproject.toml` or `__init__.py` **version bump** | **Skip.** See §4. |
| Release docs, release guardrails, `component-versions.json` receiver values | **Skip.** See §4. |
| README / brand / public-language guardrails | **Skip by default.** See §4. |

Never take an upstream `uv.lock` wholesale. It records the editable root package's own version, so
once upstream is at `1.1.2` its lock says `1.1.2` while our `pyproject.toml` stays at `1.1.1` — the
locked sync then fails. Port the dependency delta and regenerate the lock from our own metadata:

```bash
uv lock
```

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

That rule is not cosmetic, but it is worth being precise about why, because overstating it would
give a future maintainer a false constraint.

`NOTICE` already carries the attribution Apache-2.0 §4(d) requires, and that requirement — as
reproduced in `LICENSE:107-121` — is about distributing a readable copy of the applicable NOTICE
attribution. It does **not** require this particular relationship sentence in three further
documents. So the guardrail is a **product-identification policy we choose**, not a licence
condition: a user who installs HealthRelay should be able to tell which project they are running,
that it is a fork, and where to report a problem — and that is worth keeping on its own merits.

Keep the test, and keep the reasoning honest about which kind of constraint it is. Upstream's rule
is correct for upstream, where there is no second product to distinguish from, and wrong here.

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

**If you took a behaviour change from `src/` or `ios/`, the guardrail tests are not enough.** They
check the release scheme, not the code. The complete gates are what CI runs, and for an imported
change remote CI is the first place the real suite would notice a problem — so run it first.

The Python workflow runs, in order:

```bash
cd <worktree>
uv run python scripts/public-release-audit.py --strict
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run bandit -r src -q
uv run pip-audit --local --skip-editable
PYTHONPATH=src python -m pytest -q
uv run python scripts/package-smoke.py --dist-dir dist
```

The iOS workflow runs `swift test` and unsigned app builds, and only when the change is
app-affecting. `swift test` builds for macOS, so an iOS-only Swift modifier needs `#if os(iOS)`.

Two traps worth knowing before you trust a local result:

- **`PYTHONPATH=src` is required** for the test suite. Without it the interpreter may import the
  package from a different checkout and report phantom failures.
- **The type checker fails on warnings**, not just errors. Bind discarded results to `_`, which is
  how the rest of the test suite already writes them.

For a documentation-only change, `ruff format --check .`, `ruff check .` and
`scripts/public-release-audit.py --strict` are the ones that bite — the last of those rejects
machine-specific paths and other public-surface problems, and it is strict by design.

Never run acceptance, tests or builds from the **live receiver tree** — the receiver process runs
from it, and any restart deploys whatever is checked out there. Create worktrees from a review
clone; if you do not have one, clone this repository somewhere safe and work there.

## Why there is no automation

Upstream shipped five releases between 2026-07-19 and 2026-10-01. A scheduled check would fire
roughly monthly, and most of those firings would be documentation. The cost of a missed upstream
fix is bounded — we would notice it as a missing behaviour, and the fix is reviewable on its own
merits — while the cost of a habituated alert is that we stop looking. Run this when you are
curious, not on a timer.