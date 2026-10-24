import shutil
from pathlib import Path
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder

from app.core.config import get_settings
from app.core.exceptions import DatasetError
from app.database.engine import preview_rows, save_tables
from app.ingestion.loader import load_dataset
from app.ingestion.profiler import profile_table

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.post("")
def upload_dataset(request: Request, file: Annotated[UploadFile, File()]) -> dict[str, object]:
    """Validate an upload, save its tables, and return their profiles."""
    settings = get_settings()
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in (".csv", ".xlsx"):
        raise DatasetError("Upload a .csv or .xlsx file.")
    started = perf_counter()
    data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise DatasetError(f"File exceeds the {settings.max_upload_mb} MB upload limit.", 413)
    if not data:
        raise DatasetError("Uploaded file is empty.")
    tables = load_dataset(data, filename, settings.max_rows, settings.max_columns)
    manager = request.app.state.workspace_manager
    workspace_id, path = manager.create()
    try:
        (path / f"source{Path(filename).suffix.lower()}").write_bytes(data)
        save_tables(path / "analysis.duckdb", tables)
        profiling_started = perf_counter()
        table_profiles = [profile_table(table) for table in tables]
        metadata: dict[str, object] = {
            "workspace_id": workspace_id,
            "filename": filename,
            "tables": table_profiles,
            "active_table": table_profiles[0]["name"],
            "upload_duration_ms": round((perf_counter() - started) * 1000, 1),
            "profiling_duration_ms": round((perf_counter() - profiling_started) * 1000, 1),
        }
        manager.save_metadata(path, metadata)
        return jsonable_encoder(metadata)
    except Exception as exc:
        shutil.rmtree(path, ignore_errors=True)
        if isinstance(exc, DatasetError):
            raise
        raise DatasetError("Could not store this dataset. Please try again.", 500) from exc


@router.get("/{workspace_id}")
def get_dataset(request: Request, workspace_id: str) -> dict[str, object]:
    """Return the saved metadata for a live workspace."""
    _, metadata = request.app.state.workspace_manager.get(workspace_id)
    return metadata


@router.get("/{workspace_id}/preview")
def get_preview(
    request: Request,
    workspace_id: str,
    table: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, object]:
    """Return a requested page of rows from one uploaded table."""
    path, metadata = request.app.state.workspace_manager.get(workspace_id)
    table_name = table or str(metadata["active_table"])
    table_metadata = next((item for item in metadata["tables"] if item["name"] == table_name), None)
    if table_metadata is None:
        raise DatasetError("Table not found in this workspace.", 404)
    rows = preview_rows(path / "analysis.duckdb", table_name, offset, limit)
    return jsonable_encoder(
        {
            "table": table_name,
            "offset": offset,
            "limit": limit,
            "total_rows": table_metadata["row_count"],
            "rows": rows,
        }
    )


@router.delete("/{workspace_id}", status_code=204)
def delete_dataset(request: Request, workspace_id: str) -> None:
    """Remove the uploaded file, database, and metadata."""
    request.app.state.workspace_manager.delete(workspace_id)
