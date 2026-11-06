from pathlib import Path

import pytest

from health_bridge.storage import database


def test_ensure_database_initialized_runs_migrations_once_per_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    real_initialize = database.initialize_database

    def counting_initialize(db_path: Path) -> None:
        calls.append(db_path)
        real_initialize(db_path)

    monkeypatch.setattr(database, "initialize_database", counting_initialize)
    db_path = tmp_path / "health.sqlite"

    for _ in range(25):
        database.ensure_database_initialized(db_path)

    assert calls == [db_path]


def test_ensure_database_initialized_reruns_after_file_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    real_initialize = database.initialize_database

    def counting_initialize(db_path: Path) -> None:
        calls.append(db_path)
        real_initialize(db_path)

    monkeypatch.setattr(database, "initialize_database", counting_initialize)
    db_path = tmp_path / "health.sqlite"
    database.ensure_database_initialized(db_path)
    db_path.unlink()

    database.ensure_database_initialized(db_path)

    assert len(calls) == 2
    assert db_path.exists()
