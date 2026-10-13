from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pytest

PACKAGE_SMOKE = Path("scripts/package-smoke.py")


@runtime_checkable
class LockedInstallModule(Protocol):
    ROOT: Path

    def install_locked_wheel(
        self, uv: str, python: Path, wheel: Path, *, temp_root: Path
    ) -> None: ...


def _load_package_smoke() -> LockedInstallModule:
    spec = importlib.util.spec_from_file_location("package_smoke_locked", PACKAGE_SMOKE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module, LockedInstallModule)
    return module


def test_wheel_dependencies_come_only_from_hashed_lock_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: a fresh-environment Python, a built wheel, and a recording runner.
    package_smoke = _load_package_smoke()
    python = tmp_path / "venv" / "bin" / "python"
    wheel = tmp_path / "health_bridge-0.0.0-py3-none-any.whl"
    requirements = tmp_path / "runtime-requirements.txt"
    calls: list[tuple[list[str], Path]] = []

    def record_run(
        command: list[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(package_smoke, "run", record_run)

    # When: the smoke installs the wheel into the fresh environment.
    package_smoke.install_locked_wheel("uv", python, wheel, temp_root=tmp_path)

    # Then: the runtime tree is exported from uv.lock with hashes, installed
    # with --require-hashes, and only then the wheel is added with --no-deps.
    assert calls == [
        (
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(requirements),
            ],
            package_smoke.ROOT,
        ),
        (
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "--requirement",
                str(requirements),
            ],
            tmp_path,
        ),
        (
            ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            tmp_path,
        ),
    ]


def test_locked_install_never_resolves_wheel_dependencies_unpinned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the same recording runner as the positive case.
    package_smoke = _load_package_smoke()
    python = tmp_path / "venv" / "bin" / "python"
    wheel = tmp_path / "health_bridge-0.0.0-py3-none-any.whl"
    commands: list[list[str]] = []

    def record_run(
        command: list[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(package_smoke, "run", record_run)

    # When: the wheel is installed.
    package_smoke.install_locked_wheel("uv", python, wheel, temp_root=tmp_path)

    # Then: every pip install that could resolve dependencies is either
    # hash-enforced or disables dependency resolution.
    installs = [
        command for command in commands if command[:3] == ["uv", "pip", "install"]
    ]
    assert installs
    for command in installs:
        assert "--require-hashes" in command or "--no-deps" in command
