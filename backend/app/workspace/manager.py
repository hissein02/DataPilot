import json
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4

from app.core.exceptions import DatasetError

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class WorkspaceManager:
    """Manage temporary files and metadata for uploaded datasets."""

    def __init__(self, root: Path, ttl_minutes: int) -> None:
        self.root = root
        self.ttl_seconds = ttl_minutes * 60
        self.root.mkdir(parents=True, exist_ok=True)

    def cleanup_expired(self) -> None:
        """Delete workspace directories that have passed their idle lifetime."""
        now = time.time()
        for directory in self.root.iterdir():
            if not directory.is_dir() or not _ID_PATTERN.fullmatch(directory.name):
                continue
            try:
                if now - directory.stat().st_mtime > self.ttl_seconds:
                    shutil.rmtree(directory)
            except FileNotFoundError:
                continue

    def create(self) -> tuple[str, Path]:
        """Create a new private directory with an opaque workspace ID."""
        self.cleanup_expired()
        workspace_id = uuid4().hex
        path = self.root / workspace_id
        path.mkdir(mode=0o700)
        return workspace_id, path

    def get(self, workspace_id: str) -> tuple[Path, dict[str, object]]:
        """Load a live workspace and refresh its last-access time."""
        if not _ID_PATTERN.fullmatch(workspace_id):
            raise DatasetError("Workspace not found or expired.", 404)
        path = self.root / workspace_id
        metadata_path = path / "metadata.json"
        try:
            if time.time() - path.stat().st_mtime > self.ttl_seconds:
                shutil.rmtree(path)
                raise DatasetError("Workspace not found or expired.", 404)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            path.touch()
            return path, metadata
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise DatasetError("Workspace not found or expired.", 404) from exc

    def save_metadata(self, path: Path, metadata: dict[str, object]) -> None:
        """Store the uploaded table profiles next to the database."""
        (path / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        path.touch()

    def delete(self, workspace_id: str) -> None:
        """Remove a workspace after checking its ID and expiry."""
        path, _ = self.get(workspace_id)
        shutil.rmtree(path)
