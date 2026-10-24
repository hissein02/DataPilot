import os
import time

import pytest

from app.core.exceptions import DatasetError
from app.workspace.manager import WorkspaceManager


def test_workspace_lifecycle_and_expiry(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path, ttl_minutes=1)
    workspace_id, path = manager.create()
    manager.save_metadata(path, {"filename": "demo.csv"})
    assert manager.get(workspace_id)[1]["filename"] == "demo.csv"
    old = time.time() - 120
    os.utime(path, (old, old))
    manager.cleanup_expired()
    with pytest.raises(DatasetError, match="expired"):
        manager.get(workspace_id)


def test_workspace_rejects_invalid_identifiers(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path, ttl_minutes=1)
    with pytest.raises(DatasetError, match="expired"):
        manager.get("../../etc/passwd")
