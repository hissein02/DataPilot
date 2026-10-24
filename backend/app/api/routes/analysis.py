import json
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.analysis.workflow import analysis_events
from app.core.config import get_settings
from app.core.exceptions import DatasetError

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class AnalysisRequest(BaseModel):
    workspace_id: str
    question: str = Field(min_length=1, max_length=2000)
    table: str | None = None
    dataset_context: str = Field(default="", max_length=4000)


def _context(body: AnalysisRequest, request: Request):
    """Resolve the selected table using saved workspace metadata."""
    path, metadata = request.app.state.workspace_manager.get(body.workspace_id)
    table_name = body.table or str(metadata["active_table"])
    table = next((item for item in metadata["tables"] if item["name"] == table_name), None)
    if table is None:
        raise DatasetError("Table not found in this workspace.", 404)
    return table, path / "analysis.duckdb"


def _public_error(exc: Exception) -> tuple[int, str]:
    """Expose useful setup errors without leaking internal model errors."""
    if isinstance(exc, RuntimeError) and str(exc) in (
        "Set OPENAI_API_KEY to enable analysis.",
        "Set OLLAMA_MODEL to enable Ollama analysis.",
    ):
        return 503, str(exc)
    if get_settings().llm_provider == "ollama":
        return (
            502,
            "Ollama is unavailable or returned an invalid response. Check the local server and model.",
        )
    return 502, "Analysis model or workflow is unavailable."


@router.post("")
def analyze(body: AnalysisRequest, request: Request) -> dict[str, object]:
    """Run the analysis workflow and return its final report as JSON."""
    table, database_path = _context(body, request)
    try:
        for event in analysis_events(body.question, table, database_path, body.dataset_context):
            if event["type"] == "result":
                return event["report"]
    except Exception as exc:
        status, message = _public_error(exc)
        raise HTTPException(status_code=status, detail=message) from exc
    raise HTTPException(status_code=502, detail="Analysis ended without a result.")


@router.post("/stream")
def analyze_stream(body: AnalysisRequest, request: Request) -> StreamingResponse:
    """Stream the workflow's public progress events and final report."""
    table, database_path = _context(body, request)

    def stream() -> Iterator[str]:
        try:
            for event in analysis_events(body.question, table, database_path, body.dataset_context):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            _, message = _public_error(exc)
            yield f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"
            yield 'data: {"type":"done"}\n\n'

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
