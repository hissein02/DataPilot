from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    """Confirm that the API process is running."""
    return {"status": "ok", "service": "datapilot"}
