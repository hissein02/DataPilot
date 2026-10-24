"""Execute a validated query against only the selected workspace database."""

import json
from pathlib import Path
from threading import Event, Timer
from time import perf_counter

import duckdb
from fastapi.encoders import jsonable_encoder

from app.analysis.sql_safety import UnsafeQuery, validate_sql
from app.core.config import get_settings


def execute_query(database_path: Path, sql: str, allowed_tables: set[str]) -> dict[str, object]:
    """Run validated SQL with read-only access, a timeout, and result limits."""
    settings = get_settings()
    safe_sql = validate_sql(sql, allowed_tables)
    connection = duckdb.connect(
        str(database_path),
        read_only=True,
        config={
            "enable_external_access": "false",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
            "allow_community_extensions": "false",
            "threads": str(settings.duckdb_threads),
            "memory_limit": settings.duckdb_memory_limit,
        },
    )
    timed_out = Event()

    def interrupt() -> None:
        timed_out.set()
        connection.interrupt()

    timer = Timer(settings.max_query_seconds, interrupt)
    started = perf_counter()
    try:
        connection.execute("SET lock_configuration = true")
        timer.start()
        # The outer limit applies even when the generated query contains its own LIMIT.
        result = connection.execute(
            f"SELECT * FROM ({safe_sql}) AS _analysis LIMIT ?", [settings.max_query_rows + 1]
        )
        columns = [item[0] for item in result.description]
        rows = result.fetchall()
        if timed_out.is_set():
            raise UnsafeQuery("Query exceeded the time limit.")
        truncated = len(rows) > settings.max_query_rows
        payload = jsonable_encoder(
            {
                "columns": columns,
                "rows": [
                    dict(zip(columns, row, strict=True)) for row in rows[: settings.max_query_rows]
                ],
                "truncated": truncated,
            }
        )
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > settings.max_result_bytes:
            raise UnsafeQuery("Query result exceeds the response size limit.")
        return {
            **payload,
            "sql": safe_sql,
            "execution_duration_ms": round((perf_counter() - started) * 1000, 1),
        }
    except duckdb.Error as exc:
        if timed_out.is_set():
            raise UnsafeQuery("Query exceeded the time limit.") from exc
        detail = str(exc).splitlines()[0][:240]
        raise UnsafeQuery(f"Query execution failed: {detail}") from exc
    finally:
        timer.cancel()
        connection.close()
