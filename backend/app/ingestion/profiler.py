from datetime import date, datetime
from decimal import Decimal
from math import isfinite

import pandas as pd

from app.ingestion.loader import LoadedTable


def clean_value(value: object) -> object:
    """Convert pandas, date, and numeric values into JSON-friendly values."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not isfinite(value):
        return None
    return value


def profile_table(table: LoadedTable) -> dict[str, object]:
    """Save column details once so question planning can reuse them."""
    frame = table.frame
    row_count = len(frame)
    columns: list[dict[str, object]] = []
    for column in table.columns:
        series = frame[column["name"]]
        null_count = int(series.isna().sum())
        samples = [clean_value(value) for value in series.dropna().drop_duplicates().head(3)]
        detail: dict[str, object] = {
            **column,
            "null_count": null_count,
            "null_percentage": round(100 * null_count / row_count, 2) if row_count else 0,
            "distinct_count": int(series.nunique(dropna=True)),
            "distinct_method": "exact",
            "sample_values": samples,
        }
        if column["type"] in ("integer", "number") and series.notna().any():
            detail["minimum"] = clean_value(series.min())
            detail["maximum"] = clean_value(series.max())
            detail["mean"] = clean_value(series.mean())
        if column["type"] == "date" and series.notna().any():
            detail["earliest"] = clean_value(series.min())
            detail["latest"] = clean_value(series.max())
        columns.append(detail)
    return {
        "name": table.name,
        "display_name": table.display_name,
        "row_count": row_count,
        "column_count": len(columns),
        "duplicate_rows": int(frame.duplicated().sum()) if row_count else 0,
        "columns": columns,
        "warnings": table.warnings,
    }
