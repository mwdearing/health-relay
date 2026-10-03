import sqlite3
from pathlib import Path
from typing import Annotated, Final, Never

import typer
from pydantic import BaseModel

from health_bridge.queries import (
    explain_sources,
    get_daily_summary,
    get_sleep_summary,
    get_timeseries,
    get_workouts,
    list_synced_metrics,
)
from health_bridge.queries.intake_evidence import (
    DEFAULT_LIMIT,
    INTAKE_EVIDENCE_PAGE_ADAPTER,
    IntakeEvidencePage,
    InvalidIntakeEvidenceCursorError,
    InvalidIntakeEvidenceLimitError,
    UnresolvedIntakeOwnerError,
    list_intake_evidence,
    resolve_intake_owner,
)
from health_bridge.storage.database import connect_readonly_database
from health_bridge.timeseries_catalog import list_supported_timeseries_types

query_app = typer.Typer(help="Read source-grounded observations from local data.")
# `--all` walks page by page, so it stops instead of following a listing that
# never ends.
MAX_INTAKE_EVIDENCE_PAGES: Final = 1000


class IntakeEvidencePageCapError(RuntimeError):
    """The `--all` walk reached its page cap before the listing ended."""


def echo_json(model: BaseModel) -> None:
    typer.echo(model.model_dump_json())


def _exit_with_error(message: str) -> Never:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


@query_app.command("synced-metrics")
def synced_metrics(
    db: Annotated[Path, typer.Option("--db", help="User-owned database path.")],
) -> None:
    echo_json(list_synced_metrics(db))


@query_app.command("supported-timeseries-types")
def supported_timeseries_types(
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Optional supported metric category filter for metadata-only output.",
        ),
    ] = None,
) -> None:
    echo_json(list_supported_timeseries_types(category=category))


@query_app.command()
def timeseries(
    db: Annotated[Path, typer.Option("--db", help="User-owned database path.")],
    types: Annotated[
        str,
        typer.Option("--types", help="Comma-separated metric type codes."),
    ],
    start_time: Annotated[
        str,
        typer.Option("--start-time", help="Inclusive UTC observation start."),
    ],
    end_time: Annotated[
        str,
        typer.Option("--end-time", help="Exclusive UTC observation end."),
    ],
) -> None:
    type_codes = tuple(part.strip() for part in types.split(",") if part.strip())
    echo_json(
        get_timeseries(
            db,
            type_codes=type_codes,
            start_time=start_time,
            end_time=end_time,
        ),
    )


@query_app.command()
def workouts(
    db: Annotated[Path, typer.Option("--db", help="User-owned database path.")],
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Inclusive observation date."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Exclusive observation date."),
    ],
) -> None:
    echo_json(get_workouts(db, start_date=start_date, end_date=end_date))


@query_app.command("sleep-summary")
def sleep_summary(
    db: Annotated[Path, typer.Option("--db", help="User-owned database path.")],
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Inclusive observation date."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Exclusive observation date."),
    ],
) -> None:
    echo_json(get_sleep_summary(db, start_date=start_date, end_date=end_date))


@query_app.command("daily-summary")
def daily_summary(
    db: Annotated[Path, typer.Option("--db", help="User-owned database path.")],
    start_date: Annotated[
        str,
        typer.Option("--start-date", help="Inclusive observation date."),
    ],
    end_date: Annotated[
        str,
        typer.Option("--end-date", help="Exclusive observation date."),
    ],
) -> None:
    echo_json(get_daily_summary(db, start_date=start_date, end_date=end_date))


@query_app.command("explain-sources")
def source_explanation(
    db: Annotated[Path, typer.Option("--db", help="User-owned database path.")],
) -> None:
    echo_json(explain_sources(db))


def _intake_evidence_page(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    intake_id: str | None,
    cursor: str | None,
    limit: int,
) -> IntakeEvidencePage:
    return list_intake_evidence(
        connection,
        owner_id=owner_id,
        cursor=cursor,
        limit=limit,
        intake_id=intake_id,
    )


def _all_intake_evidence_pages(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    intake_id: str | None,
    limit: int,
) -> IntakeEvidencePage:
    page = _intake_evidence_page(
        connection,
        owner_id=owner_id,
        intake_id=intake_id,
        cursor=None,
        limit=limit,
    )
    items = list(page.items)
    next_cursor = page.next_cursor
    pages = 1
    while next_cursor is not None:
        if pages >= MAX_INTAKE_EVIDENCE_PAGES:
            message = (
                f"--all stopped after {MAX_INTAKE_EVIDENCE_PAGES} pages; narrow the "
                "listing with --intake-id or --owner-id"
            )
            raise IntakeEvidencePageCapError(message)
        page = _intake_evidence_page(
            connection,
            owner_id=owner_id,
            intake_id=intake_id,
            cursor=next_cursor,
            limit=limit,
        )
        items.extend(page.items)
        next_cursor = page.next_cursor
        pages += 1
    return IntakeEvidencePage(items=items, next_cursor=None)


@query_app.command("intake-evidence")
def intake_evidence(  # noqa: PLR0913 - Typer exposes each query input as an option.
    db: Annotated[
        Path,
        typer.Option("--db", help="Existing database path; it is only ever read."),
    ],
    owner_id: Annotated[
        str | None,
        typer.Option(
            "--owner-id",
            help="Intake owner to read; defaults to the only registered owner.",
        ),
    ] = None,
    intake_id: Annotated[
        str | None,
        typer.Option("--intake-id", help="Limit the listing to one intake."),
    ] = None,
    cursor: Annotated[
        str | None,
        typer.Option("--cursor", help="next_cursor from an earlier page."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Items per page, 1 to 500 (default 100)."),
    ] = DEFAULT_LIMIT,
    every_page: Annotated[
        bool,
        typer.Option("--all", help="Follow every page and print one document."),
    ] = False,
) -> None:
    """Report which HealthKit sample each intake component claims, and its status.

    Read-only: it opens an existing database and never migrates or writes it.
    Prints one JSON page of `items` and `next_cursor`, the same document the
    `get_intake_evidence_v1` MCP tool returns.
    """
    if every_page and cursor is not None:
        _exit_with_error("--all cannot be combined with --cursor.")
    if not db.is_file():
        _exit_with_error(f"intake evidence needs an existing database: {db}")
    try:
        with connect_readonly_database(db) as connection:
            resolved_owner = resolve_intake_owner(
                connection,
                owner_id,
                owner_option="--owner-id",
            )
            page = (
                _all_intake_evidence_pages(
                    connection,
                    owner_id=resolved_owner,
                    intake_id=intake_id,
                    limit=limit,
                )
                if every_page
                else _intake_evidence_page(
                    connection,
                    owner_id=resolved_owner,
                    intake_id=intake_id,
                    cursor=cursor,
                    limit=limit,
                )
            )
    except (
        IntakeEvidencePageCapError,
        InvalidIntakeEvidenceCursorError,
        InvalidIntakeEvidenceLimitError,
        UnresolvedIntakeOwnerError,
    ) as error:
        _exit_with_error(str(error))
    except (OSError, sqlite3.Error) as error:
        _exit_with_error(f"intake evidence could not read the database: {error}")
    typer.echo(INTAKE_EVIDENCE_PAGE_ADAPTER.dump_json(page).decode())
