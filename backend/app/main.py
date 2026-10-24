import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.analysis import router as analysis_router
from app.api.routes.datasets import router as datasets_router
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.core.exceptions import DatasetError
from app.workspace.manager import WorkspaceManager


def create_app() -> FastAPI:
    """Build the API, routes, error handler, and workspace cleanup task."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def cleanup_loop() -> None:
            while True:
                await asyncio.sleep(300)
                await asyncio.to_thread(app.state.workspace_manager.cleanup_expired)

        task = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="DataPilot API", version="0.1.0", lifespan=lifespan)
    settings = get_settings()
    app.state.workspace_manager = WorkspaceManager(
        settings.workspace_root, settings.workspace_ttl_minutes
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health_router)
    app.include_router(datasets_router)
    app.include_router(analysis_router)

    @app.exception_handler(DatasetError)
    async def dataset_error(_request: Request, exc: DatasetError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

    return app


app = create_app()
