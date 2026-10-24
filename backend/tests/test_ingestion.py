import io
from datetime import datetime

import pytest
from openpyxl import Workbook

from app.core.exceptions import DatasetError
from app.ingestion.loader import load_csv, load_xlsx, normalize_identifier
from app.ingestion.profiler import profile_table


def test_csv_normalizes_duplicate_headers_and_profiles_values() -> None:
    table = load_csv(b"Amount,Amount,Day\n2,3,2026-01-01\n4,,2026-01-02\n", "data.csv", 100, 20)[0]
    assert [column["name"] for column in table.columns] == ["amount", "amount_2", "day"]
    profile = profile_table(table)
    assert profile["row_count"] == 2
    assert profile["columns"][0]["mean"] == 3
    assert profile["columns"][1]["null_count"] == 1
    assert profile["columns"][2]["type"] == "date"
    assert any("Duplicate header" in warning for warning in profile["warnings"])


def test_csv_reports_irregular_records_without_dropping_them() -> None:
    table = load_csv(b"a;b\n1;2\n3\n", "items.csv", 100, 20)[0]
    assert len(table.frame) == 2
    assert table.frame["b"].isna().sum() == 1
    assert any("different field count" in warning for warning in table.warnings)


def test_xlsx_loads_multiple_sheets_and_dates() -> None:
    book = Workbook()
    orders = book.active
    orders.title = "Orders 2026"
    orders.append(["Date", "Amount"])
    orders.append([datetime(2026, 1, 1), 12.5])
    other = book.create_sheet("Regions")
    other.append(["Region", "Count"])
    other.append(["North", 2])
    output = io.BytesIO()
    book.save(output)
    tables = load_xlsx(output.getvalue(), "book.xlsx", 100, 20)
    assert [table.name for table in tables] == ["orders_2026", "regions"]
    assert tables[0].columns[0]["type"] == "date"
    assert tables[1].frame.iloc[0]["region"] == "North"


def test_invalid_files_have_clear_errors() -> None:
    with pytest.raises(DatasetError, match="UTF-8"):
        load_csv(b"\xff\xfe", "x.csv", 100, 20)
    with pytest.raises(DatasetError, match="valid Excel"):
        load_xlsx(b"garbage", "x.xlsx", 100, 20)


def test_identifier_normalization_is_unique() -> None:
    used: set[str] = set()
    assert normalize_identifier("  Gross Sales (€)", used) == "gross_sales"
    assert normalize_identifier("Gross Sales", used) == "gross_sales_2"
    assert normalize_identifier("2026", used) == "column_2026"
