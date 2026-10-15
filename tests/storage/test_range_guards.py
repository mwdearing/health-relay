"""Storage refuses out-of-range values the batch contract already bounds (#133)."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from health_bridge.storage import initialize_database

SOURCE_SQL = "insert into sources (source_key, name, kind) values ('s', 'n', 'app')"
TYPE_SQL = """
      insert into health_types (source_id, type_code, display_name, category,
      default_unit,
      sensitivity) select source_id, 't', 'T', 'other', 'u', 'low' from sources
    """
T1 = "2026-01-01T00:00:00Z"
T2 = "2026-01-01T01:00:00Z"


def _conn(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "guards.sqlite"
    initialize_database(path)
    conn = sqlite3.connect(path)
    _ = conn.execute("pragma foreign_keys = on")
    _ = conn.execute(SOURCE_SQL)
    _ = conn.execute(TYPE_SQL)
    return conn


def _workout(conn: sqlite3.Connection, **kw: object) -> None:
    row = {"start": T1, "end": T2, "dur": 10, "kcal": None, "dist": None} | kw
    _ = conn.execute(
        """
          insert into workouts (source_id, client_record_id, workout_type,
          start_time, end_time, duration_seconds, energy_kcal, distance_meters)
          values (1, ?, 'run', ?, ?, ?, ?, ?)
        """,
        (
            f"w{row['dur']}{row['kcal']}{row['dist']}{row['start']}",
            row["start"],
            row["end"],
            row["dur"],
            row["kcal"],
            row["dist"],
        ),
    )


def test_valid_rows_are_accepted(tmp_path: Path) -> None:
    with closing(_conn(tmp_path)) as conn:
        _workout(conn, kcal=0, dist=0)
        _ = conn.execute(
            """
              insert into samples (source_id, type_code, client_record_id,
              start_time, end_time, value, unit, metadata_json)
              values (1, 't', 'a', ?, ?, -5.0, 'u', '{}')
            """,
            (T1, T1),
        )


@pytest.mark.parametrize(
    "kw",
    [{"dur": -1}, {"kcal": -0.5}, {"dist": -1.0}, {"start": T2, "end": T1}],
)
def test_workout_range_violations_abort(tmp_path: Path, kw: dict[str, object]) -> None:
    with closing(_conn(tmp_path)) as conn, pytest.raises(sqlite3.IntegrityError):
        _workout(conn, **kw)


def test_reversed_sample_interval_aborts(tmp_path: Path) -> None:
    with closing(_conn(tmp_path)) as conn, pytest.raises(sqlite3.IntegrityError):
        _ = conn.execute(
            """
              insert into samples (source_id, type_code, client_record_id,
              start_time, end_time, value, unit, metadata_json)
              values (1, 't', 'a', ?, ?, 1.0, 'u', '{}')
            """,
            (T2, T1),
        )


def test_reversed_ecg_interval_aborts(tmp_path: Path) -> None:
    with closing(_conn(tmp_path)) as conn, pytest.raises(sqlite3.IntegrityError):
        _ = conn.execute(
            """
              insert into electrocardiograms (source_id, client_record_id,
              start_time, end_time, classification, symptoms_status)
              values (1, 'e', ?, ?, 'c', 's')
            """,
            (T2, T1),
        )


def test_update_cannot_introduce_a_violation(tmp_path: Path) -> None:
    with closing(_conn(tmp_path)) as conn:
        _workout(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _ = conn.execute("update workouts set duration_seconds = -3")
