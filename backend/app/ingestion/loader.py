import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from app.core.exceptions import DatasetError
from app.ingestion.type_inference import infer_series


@dataclass
class LoadedTable:
    """A parsed sheet with SQL-safe names and its original labels."""

    name: str
    display_name: str
    frame: pd.DataFrame
    columns: list[dict[str, str]]
    warnings: list[str]


def normalize_identifier(value: str, used: set[str], prefix: str = "column") -> str:
    """Turn a display name into a unique SQL identifier."""
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-zA-Z0-9]+", "_", plain).strip("_").lower() or prefix
    if base[0].isdigit():
        base = f"{prefix}_{base}"
    name = base
    counter = 2
    while name in used:
        name = f"{base}_{counter}"
        counter += 1
    used.add(name)
    return name


def _make_table(
    display_name: str,
    rows: list[list[object]],
    table_name: str,
    max_columns: int,
    warnings: list[str],
) -> LoadedTable:
    """Build a typed table from a header row and data rows."""
    if not rows:
        raise DatasetError(f"{display_name} is empty.")
    header = rows[0]
    width = max(len(row) for row in rows)
    if width > max_columns:
        raise DatasetError(f"{display_name} has more than {max_columns} columns.")
    if not any(value is not None and str(value).strip() for value in header):
        raise DatasetError(f"{display_name} has no header row.")
    if len(header) < width:
        header = [*header, *([None] * (width - len(header)))]
        warnings.append("Some data rows are wider than the header; unnamed columns were added.")

    used: set[str] = set()
    columns: list[dict[str, str]] = []
    for index, raw in enumerate(header):
        display = str(raw).strip() if raw is not None else ""
        if not display:
            display = f"Column {index + 1}"
            warnings.append(f"Missing header at column {index + 1}; named it {display}.")
        identifier = normalize_identifier(display, used)
        if identifier != normalize_identifier(display, set()):
            warnings.append(f"Duplicate header {display!r} was renamed to {identifier}.")
        columns.append({"name": identifier, "display_name": display})

    data = [row + [None] * (width - len(row)) for row in rows[1:]]
    if not data:
        warnings.append("This table has headers but no data rows.")
    frame = pd.DataFrame(data, columns=[column["name"] for column in columns])
    for column in columns:
        series, kind, type_warnings = infer_series(frame[column["name"]].tolist())
        frame[column["name"]] = series
        column["type"] = kind
        warnings.extend(f"{column['display_name']}: {warning}" for warning in type_warnings)
    return LoadedTable(table_name, display_name, frame, columns, warnings)


def load_csv(data: bytes, filename: str, max_rows: int, max_columns: int) -> list[LoadedTable]:
    """Parse a UTF-8 CSV and report irregular rows as warnings."""
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DatasetError("CSV must be UTF-8 encoded.") from exc
    if "\x00" in content:
        raise DatasetError("CSV contains invalid null bytes.")
    first_line = next((line for line in content.splitlines() if line.strip()), "")
    delimiter = max(",;\t|", key=lambda char: len(next(csv.reader([first_line], delimiter=char))))
    reader = csv.reader(io.StringIO(content, newline=""), delimiter=delimiter, strict=True)
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise DatasetError(f"Malformed CSV: {exc}.") from exc
    if len(rows) - 1 > max_rows:
        raise DatasetError(f"CSV exceeds the {max_rows:,} row limit.")
    if not rows:
        raise DatasetError("CSV is empty.")
    warnings: list[str] = []
    expected = len(rows[0])
    irregular = sum(len(row) != expected for row in rows[1:])
    if irregular:
        warnings.append(
            f"{irregular} record(s) have a different field count; missing cells were left blank."
        )
    if any(not line.strip() for line in content.splitlines()):
        warnings.append("Blank CSV lines were skipped.")
    name = Path(filename).stem
    return [
        _make_table(name, rows, normalize_identifier(name, set(), "table"), max_columns, warnings)
    ]


def load_xlsx(data: bytes, filename: str, max_rows: int, max_columns: int) -> list[LoadedTable]:
    """Read each nonempty workbook sheet without evaluating formulas."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise DatasetError("XLSX is not a valid Excel workbook.")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(member.file_size for member in archive.infolist()) > 250 * 1024 * 1024:
            raise DatasetError("XLSX expands beyond the 250 MB processing limit.")
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise DatasetError("XLSX could not be opened as an Excel workbook.") from exc
    try:
        tables: list[LoadedTable] = []
        used: set[str] = set()
        for sheet in workbook.worksheets:
            rows: list[list[object]] = []
            blank_rows = 0
            for scanned, raw in enumerate(sheet.iter_rows(values_only=True), start=1):
                if scanned > max_rows * 2 + 1000:
                    raise DatasetError(f"Worksheet {sheet.title!r} has too many scanned rows.")
                row = list(raw)
                while row and row[-1] is None:
                    row.pop()
                if len(row) > max_columns:
                    raise DatasetError(
                        f"Worksheet {sheet.title!r} has more than {max_columns} columns."
                    )
                if not row or all(value is None or str(value).strip() == "" for value in row):
                    blank_rows += 1
                    continue
                rows.append(row)
                if len(rows) - 1 > max_rows:
                    raise DatasetError(
                        f"Worksheet {sheet.title!r} exceeds the {max_rows:,} row limit."
                    )
            if not rows:
                continue
            name = normalize_identifier(sheet.title, used, "table")
            warnings: list[str] = []
            if blank_rows:
                warnings.append(f"{blank_rows} blank worksheet row(s) were skipped.")
            tables.append(_make_table(sheet.title, rows, name, max_columns, warnings))
        if not tables:
            raise DatasetError("Workbook has no nonempty worksheets.")
        return tables
    finally:
        workbook.close()


def load_dataset(data: bytes, filename: str, max_rows: int, max_columns: int) -> list[LoadedTable]:
    """Choose the parser from the file extension and normalize errors."""
    try:
        suffix = Path(filename).suffix.lower()
        if suffix == ".csv":
            return load_csv(data, filename, max_rows, max_columns)
        if suffix == ".xlsx":
            return load_xlsx(data, filename, max_rows, max_columns)
        raise DatasetError("Upload a .csv or .xlsx file.")
    except DatasetError:
        raise
    except Exception as exc:
        raise DatasetError(
            "Could not parse this dataset. Check the file structure and try again."
        ) from exc
