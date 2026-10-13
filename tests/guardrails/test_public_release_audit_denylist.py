from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

AUDIT = Path("scripts/public-release-audit.py")


@runtime_checkable
class AuditModule(Protocol):
    LOCAL_DENYLIST_FILES: tuple[Path, ...]

    def main(self) -> int: ...

    def git_tracked_files(self) -> list[Path]: ...


def _load_audit(monkeypatch: pytest.MonkeyPatch, denylist: Path) -> AuditModule:
    name = "public_release_audit_denylist"
    spec = importlib.util.spec_from_file_location(name, AUDIT)
    assert spec is not None
    assert spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert isinstance(module, AuditModule)
    monkeypatch.setattr(module, "LOCAL_DENYLIST_FILES", (denylist,))
    monkeypatch.setattr(module, "git_tracked_files", list)
    return module


def _run_main(monkeypatch: pytest.MonkeyPatch, audit: AuditModule, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["public-release-audit.py", *args])
    return audit.main()


def test_strict_audit_fails_closed_when_local_denylist_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a strict run whose private denylist is absent (fresh CI checkout).
    audit = _load_audit(monkeypatch, tmp_path / "missing-denylist.local")

    # When: the strict audit runs.
    code = _run_main(monkeypatch, audit, "--strict")

    # Then: it exits non-zero with a clear message and never prints a PASS line.
    output = capsys.readouterr().out
    assert code == 3
    assert "STRICT local-denylist missing" in output
    assert "strict private values were not checked" in output
    assert "FAIL: strict public-surface blockers found." in output
    assert "PASS" not in output


def test_non_strict_audit_still_passes_without_local_denylist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a non-strict run whose private denylist path does not exist.
    audit = _load_audit(monkeypatch, tmp_path / "missing-denylist.local")

    # When: the non-strict audit runs.
    code = _run_main(monkeypatch, audit)

    # Then: behavior is unchanged: it passes and does not mention strict blockers.
    output = capsys.readouterr().out
    assert code == 0
    assert "strict" not in output


def test_strict_pass_reports_how_many_local_denylist_patterns_were_consulted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a strict run with a denylist that holds two synthetic patterns.
    denylist = tmp_path / ".public-release-denylist.local"
    _ = denylist.write_text("# synthetic\nSYNTHETIC_ONE\nSYNTHETIC_TWO\n")
    audit = _load_audit(monkeypatch, denylist)

    # When: the strict audit runs.
    code = _run_main(monkeypatch, audit, "--strict")

    # Then: it passes and the PASS line states the consulted pattern count.
    output = capsys.readouterr().out
    assert code == 0
    assert (
        "PASS: no strict public-surface blockers found (2 local denylist patterns)."
        in output
    )


def test_strict_audit_allows_missing_denylist_only_with_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a strict run without the private denylist, as on a CI checkout.
    audit = _load_audit(monkeypatch, tmp_path / "missing-denylist.local")

    # When: the explicit CI opt-out flag is passed.
    code = _run_main(monkeypatch, audit, "--strict", "--allow-missing-local-denylist")

    # Then: it passes, but says loudly that the local denylist was not consulted.
    output = capsys.readouterr().out
    assert code == 0
    assert "local_denylist_consulted=false" in output
    assert "local denylist was NOT consulted" in output
    assert "strict_blockers=0" in output


def test_strict_audit_reports_denylist_consulted_when_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a strict run with a present synthetic denylist.
    denylist = tmp_path / ".public-release-denylist.local"
    _ = denylist.write_text("SYNTHETIC_ONE\n")
    audit = _load_audit(monkeypatch, denylist)

    # When: the strict audit runs.
    code = _run_main(monkeypatch, audit, "--strict")

    # Then: the summary records that the denylist was consulted.
    output = capsys.readouterr().out
    assert code == 0
    assert "local_denylist_consulted=true" in output
    assert "NOT consulted" not in output


def test_only_ci_strict_gates_pass_the_missing_denylist_opt_out() -> None:
    # Given: the flag that lets CI run strict mode without the private denylist.
    flag = "--allow-missing-local-denylist"
    gate = "uv run python scripts/public-release-audit.py --strict " + flag
    workflows = (
        Path(".github/workflows/python.yml"),
        Path(".github/workflows/release.yml"),
    )

    # When / Then: exactly the two CI workflows carry the opt-out, once each.
    for workflow in workflows:
        assert workflow.read_text(encoding="utf-8").count(gate) == 1, workflow

    # And: no other tracked workflow, script, doc or guard passes it.
    allowed = {*workflows, Path("scripts/public-release-audit.py")}
    allowed.add(Path("tests/guardrails/test_public_release_audit_denylist.py"))
    allowed.add(Path("tests/guardrails/test_ios_app_privacy.py"))
    allowed.add(Path("docs/qa/public-release-qa.md"))
    tracked = subprocess.run(
        ["git", "ls-files"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    for name in tracked:
        path = Path(name)
        if path in allowed or path.suffix not in {".yml", ".yaml", ".sh", ".py", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert flag not in text, path


def test_release_operator_docs_require_strict_run_with_local_denylist() -> None:
    qa = Path("docs/qa/public-release-qa.md").read_text(encoding="utf-8")

    assert "public-release-audit.py --strict" in qa
    assert "before tagging" in qa
    assert "local denylist" in qa
    assert "scripts/validate-fresh-device-evidence.py" in qa
    assert "component-versions.json" in qa
