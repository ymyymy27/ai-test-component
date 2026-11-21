"""Workspace directory, project layout and short-lived business lock."""

from __future__ import annotations

import contextlib
import os
import re
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import portalocker

from aitest.errors import AiTestError
from aitest.models import Project, WorkspaceMeta, new_id
from aitest.storage.files import read_model, write_model

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(root: Path) -> threading.Lock:
    key = os.path.normcase(str(root))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def init(self) -> WorkspaceMeta:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AiTestError(
                "INVALID_PATH", f"工作空间不可创建：{self.root}", "换一个可写目录"
            ) from exc
        meta_path = self.root / "workspace.json"
        if meta_path.is_file():
            return read_model(meta_path, WorkspaceMeta)
        with self.lock():
            if meta_path.is_file():
                return read_model(meta_path, WorkspaceMeta)
            meta = WorkspaceMeta(workspace_id=new_id("ws"), created_at=iso_now())
            write_model(meta_path, meta)
            return meta

    @contextlib.contextmanager
    def lock(self, timeout: float = 10.0) -> Iterator[None]:
        """Hold an in-process thread lock and an OS file lock for the workspace.

        The thread lock serialises callers in the same process (POSIX file locks
        are per-process), while the file lock covers other processes. Both use
        the same timeout and raise ``WORKSPACE_LOCKED`` on contention.
        """
        thread_lock = _thread_lock(self.root)
        if not thread_lock.acquire(timeout=timeout):
            raise AiTestError(
                "WORKSPACE_LOCKED", "工作空间被另一个进程占用", "等待或关闭另一实例"
            )
        try:
            lock_path = self.root / ".workspace.lock"
            lock = portalocker.Lock(
                lock_path,
                mode="a",
                timeout=timeout,
                flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            )
            try:
                lock.acquire()
            except portalocker.exceptions.LockException as exc:
                raise AiTestError(
                    "WORKSPACE_LOCKED", "工作空间被另一个进程占用", "等待或关闭另一实例"
                ) from exc
            try:
                yield
            finally:
                with contextlib.suppress(Exception):
                    lock.release()
        finally:
            thread_lock.release()

    # -- project layout -------------------------------------------------
    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    def project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", project_id):
            raise AiTestError("INVALID_PATH", f"项目 ID 不合法：{project_id}", "检查项目 ID")
        return self.projects_dir / project_id

    def project_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def load_project(self, project_id: str) -> Project:
        path = self.project_path(project_id)
        if not path.is_file():
            raise AiTestError(
                "PROJECT_NOT_FOUND", f"项目不存在：{project_id}", "先创建项目或检查 ID"
            )
        return read_model(path, Project)

    def save_project(self, project: Project) -> None:
        project.updated_at = iso_now()
        write_model(self.project_path(project.project_id), project)

    def list_projects(self) -> list[Project]:
        if not self.projects_dir.is_dir():
            return []
        projects: list[Project] = []
        for entry in sorted(self.projects_dir.iterdir()):
            path = entry / "project.json"
            if path.is_file():
                projects.append(read_model(path, Project))
        return projects

    def find_by_root(self, root: str) -> Project | None:
        for project in self.list_projects():
            if os.path.normcase(project.root) == os.path.normcase(root):
                return project
        return None

    def find_overlapping(self, root: str) -> list[Project]:
        target = os.path.normcase(root)
        result: list[Project] = []
        for project in self.list_projects():
            existing = os.path.normcase(project.root)
            if existing == target:
                continue
            if target.startswith(existing + os.sep) or existing.startswith(target + os.sep):
                result.append(project)
        return result
