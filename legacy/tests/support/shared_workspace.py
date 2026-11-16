"""Shared pytest fixture: a migrated, empty shared workspace."""

from __future__ import annotations

from pathlib import Path

import pytest

from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.infrastructure.file_store.workspace import Workspace

_MIGRATIONS = (
    "0003-sharded-record-authority",
    "0004-bounded-query-directory",
    "0005-complete-commit-closure",
    "0006-canonical-current-publication",
    "0007-canonical-manifest-and-business-changes",
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    identity = Workspace(root)
    FileEventJournal(root, instance_id=identity.workspace_id)
    manager = FileMigrationManager(root)
    plan = manager.plan(_MIGRATIONS)
    assert manager.apply(plan.plan_id).state == "applied"
    return root
