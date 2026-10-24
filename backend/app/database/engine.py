import json
from pathlib import Path

import duckdb

from app.core.config import get_settings
from app.core.exceptions import DatasetError
from app.ingestion.loader import LoadedTable


def quote_identifier(value: str) -> str:
    """Escape a table or column name before adding it to SQL."""
    return '"' + value.replace('"', '""') + '"'


def save_tables(database_path: Path, tables: list[LoadedTable]) -> None:
    """Copy parsed tables into the upload's DuckDB file."""
    settings = get_settings()
    connection = duckdb.connect(
        str(database_path),
        config={
            "threads": str(settings.duckdb_threads),
            "memory_limit": settings.duckdb_memory_limit,
        },
    )
    try:
        for table in tables:
            connection.register("_incoming", table.frame)
            try:
                connection.execute(
                    f"CREATE TABLE {quote_identifier(table.name)} AS SELECT * FROM _incoming"
                )
            finally:
                connection.unregister("_incoming")
    finally:
        connection.close()


def preview_rows(
    database_path: Path, table_name: str, offset: int, limit: int
) -> list[dict[str, object]]:
    """Read one bounded page of rows for the Dataset preview."""
    settings = get_settings()
    connection = duckdb.connect(
        str(database_path),
        read_only=True,
        config={
            "enable_external_access": "false",
            "threads": str(settings.duckdb_threads),
            "memory_limit": settings.duckdb_memory_limit,
        },
    )
    try:
        result = connection.execute(
            f"SELECT * FROM {quote_identifier(table_name)} LIMIT ? OFFSET ?", [limit, offset]
        )
        names = [description[0] for description in result.description]
        rows = [dict(zip(names, row, strict=True)) for row in result.fetchall()]
        if len(json.dumps(rows, default=str)) > 2_000_000:
            raise DatasetError("Preview is too large. Request fewer rows.", 413)
        return rows
    finally:
        connection.close()
