from datetime import date, datetime
from decimal import Decimal

import pandas as pd


def infer_series(values: list[object]) -> tuple[pd.Series, str, list[str]]:
    """Convert only columns whose nonempty values all fit a narrow type."""
    cleaned = [
        None if value is None or pd.isna(value) or str(value).strip() == "" else value
        for value in values
    ]
    present = [value for value in cleaned if value is not None]
    if not present:
        return pd.Series(cleaned, dtype="string"), "text", []

    if all(isinstance(value, (datetime, date)) for value in present):
        return pd.to_datetime(pd.Series(cleaned), errors="raise"), "date", []

    if any(isinstance(value, (datetime, date)) for value in present):
        return (
            pd.Series(
                [str(value) if value is not None else None for value in cleaned], dtype="string"
            ),
            "text",
            ["Mixed date and other cell values were kept as text."],
        )

    numeric = pd.to_numeric(pd.Series(cleaned), errors="coerce")
    if numeric.notna().sum() == len(present):
        if all(float(value).is_integer() for value in numeric.dropna()):
            return numeric.astype("Int64"), "integer", []
        return numeric.astype("Float64"), "number", []

    # ISO dates are deliberately preferred to locale-sensitive date guessing.
    def parse_iso(value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None

    dates = [parse_iso(value) for value in cleaned]
    if all(
        parsed is not None
        for value, parsed in zip(cleaned, dates, strict=True)
        if value is not None
    ):
        return pd.to_datetime(pd.Series(dates), errors="raise"), "date", []

    warnings: list[str] = []
    if numeric.notna().any():
        warnings.append("Mixed numeric and text values were kept as text.")
    if any(isinstance(value, (datetime, date, Decimal)) for value in present):
        warnings.append("Mixed cell types were kept as text.")
    return (
        pd.Series([str(value) if value is not None else None for value in cleaned], dtype="string"),
        "text",
        warnings,
    )
