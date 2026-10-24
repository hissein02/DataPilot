"""Generic column context and checks shared by planning and validation."""

import json
import re

import sqlglot
from sqlglot import exp

from app.analysis.sql_safety import UnsafeQuery

_IDENTIFIER_NAME = re.compile(r"(^|_)(id|uuid|guid|identifier|key|code)($|_)", re.IGNORECASE)
_MEASURE_FUNCTIONS = {
    "AVG",
    "SUM",
    "MIN",
    "MAX",
    "MEDIAN",
    "MODE",
    "STDDEV",
    "STDDEV_POP",
    "STDDEV_SAMP",
}


def is_identifier(column: dict[str, object]) -> bool:
    """Recognize common ID-like names so they are not treated as measures."""
    return bool(_IDENTIFIER_NAME.search(str(column["name"])))


def schema_context(table: dict[str, object]) -> str:
    """Compact profile, including clues that help the model distinguish IDs from measures."""
    columns = []
    for column in table["columns"]:
        columns.append(
            {
                "name": column["name"],
                "label": column["display_name"],
                "type": column["type"],
                "distinct_values": column.get("distinct_count"),
                "missing_percent": column.get("null_percentage"),
                "likely_identifier": is_identifier(column),
                "sample_values": [str(value)[:80] for value in column.get("sample_values", [])[:3]],
            }
        )
    return json.dumps({"table": table["name"], "rows": table["row_count"], "columns": columns})


def validate_measure_roles(sql: str, table: dict[str, object]) -> None:
    """Do not aggregate identifier values as measurements in exploratory analysis."""
    identifiers = {
        str(column["name"]).lower() for column in table["columns"] if is_identifier(column)
    }
    if not identifiers:
        return
    tree = sqlglot.parse_one(sql, read="duckdb")
    for function in tree.find_all(exp.Func):
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if name.upper() not in _MEASURE_FUNCTIONS:
            continue
        for column in function.find_all(exp.Column):
            if column.name.lower() in identifiers:
                raise UnsafeQuery(
                    f"{column.name} appears to be an identifier; use it for counts or grouping, not {name.upper()} measurements."
                )
