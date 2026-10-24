from functools import lru_cache
from pathlib import Path
from tempfile import gettempdir
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Local limits and credentials loaded from the root .env file."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env", extra="ignore"
    )

    max_upload_mb: int = 25
    workspace_ttl_minutes: int = 60
    cors_origins: str = "http://localhost:5173"
    workspace_root: Path = Path(gettempdir()) / "datapilot"
    max_rows: int = 200_000
    max_columns: int = 200
    duckdb_memory_limit: str = "512MB"
    duckdb_threads: int = 2
    llm_provider: Literal["openai", "ollama"] = "openai"
    openai_api_key: str = ""
    analysis_model: str = "gpt-6-luna"
    ollama_model: str = ""
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_timeout_seconds: float = 120.0
    max_query_rows: int = 100
    max_query_seconds: float = 10.0
    max_result_bytes: int = 100_000
    max_sql_retries: int = 3
    max_sql_queries: int = 5

    @property
    def allowed_origins(self) -> list[str]:
        """Split the comma-separated CORS setting into individual origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Reuse one validated settings instance across requests."""
    return Settings()
