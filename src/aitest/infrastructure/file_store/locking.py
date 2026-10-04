"""OS writer locks: per-transaction locks and the process-lifetime writer lock.

同一个用户数据工作空间在任意时刻只能有一个核心进程持排他写锁（A-02）：

- :class:`LifetimeWriterLock` 在核心装配时取得，覆盖恢复、服务到退出的
  全生命周期；同进程二次装配同根工作空间直接拒绝，跨进程由 OS 锁拒绝；
- :func:`writer_lock` 供短事务使用。当前进程已持有同根生命周期锁时，
  事务不再二次申请 OS 锁（Windows 同进程对同一区域重复加锁会自冲突），
  改用进程内互斥串行化，跨进程互斥仍由生命周期 OS 锁保证。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import portalocker

from aitest.application.errors import WorkspaceInUse

_MAX_QUEUED_WRITERS = 32
_TRANSACTION_WAIT_SECONDS = 2.0


class _Admission:
    """一个工作空间在本进程内的写准入事实（生命周期 OS 锁 + 事务互斥）。"""

    __slots__ = ("epoch", "os_lock", "queue", "ready", "serial")

    def __init__(self, os_lock: portalocker.Lock, *, ready: bool = True) -> None:
        self.os_lock = os_lock
        self.serial = threading.Lock()
        self.queue = threading.BoundedSemaphore(_MAX_QUEUED_WRITERS)
        self.epoch: int | None = None
        self.ready = ready


_registry_guard = threading.Lock()
_admissions: dict[str, _Admission] = {}
_held = threading.local()


def _normalize(path: Path) -> str:
    return str(Path(path).resolve())


def admitted_writer_epoch(path: Path) -> int | None:
    """Return the epoch bound to the currently admitted core in this process."""
    with _registry_guard:
        admission = _admissions.get(_normalize(path))
        return admission.epoch if admission is not None and admission.ready else None


def _open_os_lock(path: Path) -> portalocker.Lock:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(
        str(path),
        mode="a",
        timeout=0,
        flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
    )
    try:
        lock.acquire()
    except portalocker.exceptions.LockException as error:
        raise WorkspaceInUse("workspace already has a writer") from error
    return lock


@contextmanager
def writer_lock(path: Path, *, reentrant: bool = False) -> Iterator[None]:
    key = _normalize(path)
    owned: set[str] = getattr(_held, "paths", set())
    if key in owned:
        if not reentrant:
            raise WorkspaceInUse("workspace already has a writer in this thread")
        yield
        return
    with _registry_guard:
        admission = _admissions.get(key)
    if admission is not None:
        # 本进程已是该工作空间的唯一写入者：事务只需进程内串行，绝不
        # 重复对同一 OS 锁区域加锁（A-02）。
        if not admission.queue.acquire(blocking=False):
            raise WorkspaceInUse("workspace writer queue is full; retry the same intent")
        acquired = False
        try:
            acquired = admission.serial.acquire(timeout=_TRANSACTION_WAIT_SECONDS)
            if not acquired:
                raise WorkspaceInUse("workspace writer queue is busy; retry the same intent")
            with _registry_guard:
                if _admissions.get(key) is not admission or not admission.ready:
                    raise WorkspaceInUse("workspace writer admission is no longer available")
            _held.paths = owned | {key}
            try:
                yield
            finally:
                _held.paths = owned
        finally:
            if acquired:
                admission.serial.release()
            admission.queue.release()
        return
    lock = _open_os_lock(path)
    _held.paths = owned | {key}
    try:
        yield
    finally:
        _held.paths = owned
        lock.release()


class LifetimeWriterLock:
    """工作空间全生命周期排他写锁；同一根目录在本进程不可二次准入。"""

    def __init__(self, path: Path, *, identity_admission: bool = False) -> None:
        self._key = _normalize(path)
        self._path = path
        self._admission: _Admission | None = None
        self._identity_admission = identity_admission

    @property
    def held(self) -> bool:
        return self._admission is not None

    def acquire(self) -> None:
        with _registry_guard:
            if self._key in _admissions:
                raise WorkspaceInUse("workspace already has a lifetime writer in this process")
        lock = _open_os_lock(self._path)
        admission = _Admission(lock, ready=not self._identity_admission)
        with _registry_guard:
            if self._key in _admissions:
                # 拿锁期间被同进程另一线程抢先登记：释放新锁，保持单一准入。
                lock.release()
                raise WorkspaceInUse("workspace already has a lifetime writer in this process")
            _admissions[self._key] = admission
        self._admission = admission

    def bind_epoch(self, epoch: int) -> None:
        if type(epoch) is not int or epoch < 1:
            raise ValueError("writer epoch must be a positive integer")
        with _registry_guard:
            admission = self._admission
            if admission is None or _admissions.get(self._key) is not admission:
                raise ValueError("writer epoch requires an active admission")
            if admission.epoch is not None:
                raise ValueError("writer epoch is already bound")
            admission.epoch = epoch
            admission.ready = True

    def release(self) -> None:
        admission = self._admission
        if admission is None:
            return
        if self._key in getattr(_held, "paths", set()):
            raise WorkspaceInUse("cannot release a writer with an active transaction")
        if not admission.serial.acquire(timeout=_TRANSACTION_WAIT_SECONDS):
            raise WorkspaceInUse("writer still has an active transaction; drain it first")
        try:
            with _registry_guard:
                if _admissions.get(self._key) is admission:
                    _admissions.pop(self._key, None)
            admission.os_lock.release()
            self._admission = None
        finally:
            admission.serial.release()

    def __enter__(self) -> LifetimeWriterLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


__all__ = ["LifetimeWriterLock", "admitted_writer_epoch", "writer_lock"]
