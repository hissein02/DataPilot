import io

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import get_settings
from app.main import create_app


def test_upload_preview_and_delete(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/datasets",
                files={"file": ("orders.csv", b"item,quantity\nPen,2\nBook,3\n", "text/csv")},
            )
            assert response.status_code == 200
            body = response.json()
            assert len(body["workspace_id"]) == 32
            assert body["tables"][0]["row_count"] == 2
            workspace_id = body["workspace_id"]
            assert client.get(f"/api/datasets/{workspace_id}").json()["filename"] == "orders.csv"
            preview = client.get(
                f"/api/datasets/{workspace_id}/preview", params={"offset": 1, "limit": 1}
            )
            assert preview.status_code == 200
            assert preview.json()["rows"] == [{"item": "Book", "quantity": 3}]
            assert client.delete(f"/api/datasets/{workspace_id}").status_code == 204
            assert client.get(f"/api/datasets/{workspace_id}").status_code == 404
    finally:
        get_settings.cache_clear()


def test_xlsx_sheets_and_bad_upload(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        book = Workbook()
        book.active.title = "One"
        book.active.append(["item"])
        book.active.append(["Pen"])
        two = book.create_sheet("Two")
        two.append(["region"])
        two.append(["North"])
        output = io.BytesIO()
        book.save(output)
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/datasets", files={"file": ("book.xlsx", output.getvalue())}
            )
            assert response.status_code == 200
            body = response.json()
            assert [table["name"] for table in body["tables"]] == ["one", "two"]
            preview = client.get(
                f"/api/datasets/{body['workspace_id']}/preview", params={"table": "two"}
            )
            assert preview.json()["rows"] == [{"region": "North"}]
            assert (
                client.post("/api/datasets", files={"file": ("bad.xlsx", b"bad")}).status_code
                == 400
            )
    finally:
        get_settings.cache_clear()
