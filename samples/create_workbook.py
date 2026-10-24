"""Regenerate the multi-sheet demonstration workbook with openpyxl."""

import csv
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).parent


def main() -> None:
    workbook = Workbook()
    orders = workbook.active
    orders.title = "Orders"
    with (ROOT / "orders.csv").open(newline="", encoding="utf-8") as source:
        for row in csv.reader(source):
            orders.append(row)
    regions = workbook.create_sheet("Regions")
    regions.append(["region", "manager"])
    regions.append(["North", "Amina"])
    regions.append(["South", "Leo"])
    regions.append(["West", "Maya"])
    workbook.save(ROOT / "orders_workbook.xlsx")


if __name__ == "__main__":
    main()
