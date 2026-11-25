"""Workspace directory, project layout and short-lived business lock."""

from __future__ import annotations

import contextlib
import os
import re
import threading
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import portalocker

from aitest.errors import AiTestError
from aitest.models import Project, WorkspaceMeta, new_id
from aitest.storage.files import read_model, write_model

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class _WorkspaceRWGuard:
    """进程内工作空间读写协调器：支持多项目并发持读锁，全局操作独占写锁。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False

    def acquire_read(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._writer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._cond.wait(timeout=remaining):
                    return False
            self._readers += 1
            return True

    def release_read(self) -> None:
        with self._cond:
            self._readers = max(0, self._readers - 1)
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._writer or self._readers > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._cond.wait(timeout=remaining):
                    return False
            self._writer = True
            return True

    def release_write(self) -> None:
        with self._cond:
            self._writer = False
            self._cond.notify_all()


_RW_GUARDS: dict[str, _WorkspaceRWGuard] = {}


def _rw_guard(root: Path) -> _WorkspaceRWGuard:
    key = os.path.normcase(str(root))
    with _THREAD_LOCKS_GUARD:
        guard = _RW_GUARDS.get(key)
        if guard is None:
            guard = _WorkspaceRWGuard()
            _RW_GUARDS[key] = guard
        return guard


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path))
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
        self.load_errors: list[str] = []

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
    def _file_lock(
        self,
        lock_path: Path,
        *,
        timeout: float = 10.0,
        shared: bool = False,
        use_thread_lock: bool = True,
    ) -> Iterator[None]:
        """对指定锁文件施加文件锁；非共享写锁同时使用进程内线程锁串行化。"""
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        thread_lock = _thread_lock(lock_path) if use_thread_lock else None
        if thread_lock is not None and not thread_lock.acquire(timeout=timeout):
            raise AiTestError(
                "WORKSPACE_LOCKED", "工作空间被另一个进程占用", "等待或关闭另一实例"
            )
        try:
            mode_flags = portalocker.LOCK_SH if shared else portalocker.LOCK_EX
            lock = portalocker.Lock(
                lock_path,
                mode="a",
                timeout=timeout,
                flags=mode_flags | portalocker.LOCK_NB,
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
            if thread_lock is not None:
                thread_lock.release()

    @contextlib.contextmanager
    def lock(self, timeout: float = 10.0) -> Iterator[None]:
        """获取工作空间级全局独占锁（阻断维护窗口期间的跨项目写入）。"""
        guard = _rw_guard(self.root)
        deadline = time.monotonic() + timeout
        if not guard.acquire_write(timeout):
            raise AiTestError(
                "WORKSPACE_LOCKED", "工作空间被另一个进程占用", "等待或关闭另一实例"
            )
        try:
            remaining = max(0.01, deadline - time.monotonic())
            with self._file_lock(self.root / ".workspace.lock", timeout=remaining):
                yield
        finally:
            guard.release_write()

    @contextlib.contextmanager
    def project_lock(self, project_id: str, timeout: float = 10.0) -> Iterator[None]:
        """获取单项目排他写锁：允许不同项目并发，仅与全局工作空间锁或同项目冲突。"""
        project_lock_path = self.project_dir(project_id) / ".project.lock"
        guard = _rw_guard(self.root)
        deadline = time.monotonic() + timeout
        if not guard.acquire_read(timeout):
            raise AiTestError(
                "WORKSPACE_LOCKED", "工作空间被另一个进程占用", "等待或关闭另一实例"
            )
        try:
            rem_ws = max(0.01, deadline - time.monotonic())
            with self._file_lock(
                self.root / ".workspace.lock",
                timeout=rem_ws,
                shared=True,
                use_thread_lock=False,
            ):
                rem_proj = max(0.01, deadline - time.monotonic())
                with self._file_lock(project_lock_path, timeout=rem_proj):
                    yield
        finally:
            guard.release_read()

    @contextlib.contextmanager
    def repair_lock(self, project_id: str, timeout: float = 10.0) -> Iterator[None]:
        """同一项目的依赖安装互斥锁，避免并发 pip 冲突而不阻塞其他命令。"""
        repair_lock_path = self.project_dir(project_id) / ".repair.lock"
        with self._file_lock(repair_lock_path, timeout=timeout):
            yield

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
        self.load_errors = []
        for entry in sorted(self.projects_dir.iterdir()):
            path = entry / "project.json"
            if not path.is_file():
                continue
            try:
                projects.append(read_model(path, Project))
            except AiTestError as exc:
                self.load_errors.append(f"{path}：{exc.info.message}")
        return projects

    def find_by_root(self, root: str) -> Project | None:
        for project in self.list_projects():
            if os.path.normcase(project.root) == os.path.normcase(root):
                return project
        return None

    def find_overlapping(self, root: str) -> list[Project]:
        target = Path(os.path.normcase(os.path.abspath(root)))
        result: list[Project] = []
        for project in self.list_projects():
            existing = Path(os.path.normcase(os.path.abspath(project.root)))
            if existing == target:
                continue
            if target.is_relative_to(existing) or existing.is_relative_to(target):
                result.append(project)
        return result
