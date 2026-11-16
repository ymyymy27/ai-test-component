"""核心端点、启动器与管道连接（从 bootstrap.py 拆出）。

只依赖基础设施/入口类型，不依赖 `CoreAssembly`；bootstrap 继续 re-export 同名符号，
外部 import 路径不变。
"""

from __future__ import annotations

import json
import os
import subprocess as subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from aitest.infrastructure.credentials import SecretManager, WindowsCredentialProvider
from aitest.infrastructure.environment_configuration import (
    carrier_configuration as carrier_configuration,
)
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.core_launch import (
    FileCoreLaunchStore,
    read_startup_diagnostic,
    record_startup_diagnostic,
)
from aitest.infrastructure.file_store.locking import LifetimeWriterLock
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.infrastructure.host_lifetime import cli_host_pid as cli_host_pid
from aitest.infrastructure.model_configuration import (
    validate_model_configuration as validate_model_configuration,
)
from aitest.infrastructure.python_environment import RegisteredPythonEnvironmentResolver
from aitest.source_wiring import (
    _entry_actual_path as _entry_actual_path,
)
from aitest.source_wiring import (
    _environment_ref as _environment_ref,
)
from aitest.source_wiring import (
    _frozen_adapter_version as _frozen_adapter_version,
)
from aitest.source_wiring import (
    _frozen_step_timeout as _frozen_step_timeout,
)
from aitest.source_wiring import (
    _interpreter_verifier as _interpreter_verifier,
)
from aitest.source_wiring import (
    _module_expectations as _module_expectations,
)
from aitest.source_wiring import (
    _module_names_from_project as _module_names_from_project,
)
from aitest.source_wiring import (
    _observed_content_identity as _observed_content_identity,
)
from aitest.source_wiring import (
    _pattern_relative_dir as _pattern_relative_dir,
)
from aitest.source_wiring import (
    _pinned_file_digests as _pinned_file_digests,
)
from aitest.source_wiring import (
    _pinned_snapshot_identity as _pinned_snapshot_identity,
)
from aitest.source_wiring import (
    _positive_int as _positive_int,
)
from aitest.source_wiring import (
    _probe_output_reference as _probe_output_reference,
)
from aitest.source_wiring import (
    _registered_source_check_statuses as _registered_source_check_statuses,
)
from aitest.source_wiring import (
    _required_source_analysis as _required_source_analysis,
)
from aitest.source_wiring import (
    _required_text as _required_text,
)
from aitest.source_wiring import (
    _rules_revision as _rules_revision,
)
from aitest.source_wiring import (
    _source_verification_provider as _source_verification_provider,
)
from aitest.source_wiring import (
    _technical_identity as _technical_identity,
)

_INSTANCE_ID_FILE = ".core-instance-id"
_CORE_DIR = "core"
_CONNECTION_LEDGER_FILE = "connection-ledger.jsonl"
_DEFAULT_WORKER_MODULE = "aitest.interfaces.local.core_worker"
_DEFAULT_CONNECT_TIMEOUT_MS = 200
_DEFAULT_WAIT_TIMEOUT_SECONDS = 5.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05


_CONNECTION_FACT_SCHEMA = "aitest.core-connection/1.0"


def _fsync_directory(path: Path) -> None:
    """尽力 fsync 目录；不支持的平台静默（持久化尽力点，非业务正确性）。"""
    if os.name != "posix":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)

class CoreConnectionLedger:
    """核心连接事实的跨重启追加台账（A-10）。

    连接事实此前只活在 ``EditorHost`` / ``LocalAPI`` 的实例内存里，核心
    换实例或宿主重启后无法核对“谁在什么时候经哪条会话连到了哪个核心”。
    台账位于工作空间 ``core/connection-ledger.jsonl``，每行一条不可变事实，
    追加后显式 ``fsync``，目录首次创建时同样 fsync；读取只解析完整行，
    末尾半行（掉电撕裂）会在 :meth:`facts` 处显式抛错，不静默删除。

    台账**只记连接事实，不记任何业务载荷与凭据**；来源会话/用户 SID
    与管道对端身份核对使用同一份本机取证结果。
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        session_probe: Callable[[], int | None] | None = None,
        user_probe: Callable[[], str | None] | None = None,
    ) -> None:
        FileCommitStore.reject_links(workspace_root)
        self._root = workspace_root.resolve()
        self._dir = self._root / _CORE_DIR
        self._path = self._dir / _CONNECTION_LEDGER_FILE
        self._session_probe = session_probe
        self._user_probe = user_probe

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        kind: str,
        *,
        pipe_workspace_id: str,
        instance_id: str,
    ) -> dict[str, object]:
        """追加一条连接事实并返回落盘内容。"""
        fact: dict[str, object] = {
            "schema": _CONNECTION_FACT_SCHEMA,
            "seq": self._next_seq(),
            "kind": kind,
            "at": datetime.now(UTC).isoformat(),
            "pipe_workspace_id": pipe_workspace_id,
            "instance_id": instance_id,
            "source_session_id": self._source_session(),
            "source_user_sid": self._source_user(),
        }
        line = (json.dumps(fact, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        # 目录项首次落盘需要 fsync，掉电后目录里才保证看得到台账文件。
        _fsync_directory(self._dir)
        return fact

    def facts(self) -> tuple[dict[str, object], ...]:
        """读回全部完整事实行；撕裂尾行抛错，绝不静默截断历史。"""
        if not self._path.exists():
            return ()
        facts: list[dict[str, object]] = []
        for raw in self._path.read_bytes().splitlines():
            if not raw.strip():
                continue
            obj = json.loads(raw.decode("utf-8"))
            if isinstance(obj, dict):
                facts.append(obj)
        return tuple(facts)

    def last(self, kind: str) -> dict[str, object] | None:
        for fact in reversed(self.facts()):
            if fact.get("kind") == kind:
                return fact
        return None

    def _next_seq(self) -> int:
        existing = self.facts()
        if not existing:
            return 1
        last_seq = existing[-1].get("seq")
        return int(last_seq) + 1 if isinstance(last_seq, int) else 1

    def _source_session(self) -> int | None:
        if self._session_probe is not None:
            return self._session_probe()
        from aitest.interfaces.local.pipe import current_session_id

        return current_session_id()

    def _source_user(self) -> str | None:
        if self._user_probe is not None:
            return self._user_probe()
        from aitest.interfaces.local.pipe import current_user_sid

        return current_user_sid()


def save_purpose_credential(*, purpose: str, reference: str, value: str) -> None:
    """宿主装配入口：把凭据写入留给组合根，接口层不直接依赖基础设施实现。

    依赖方向要求 `interfaces` 不导入 `aitest.infrastructure`（架构 00 第 11 节 /
    `tests/architecture/test_boundaries.py`）；安全凭据的落盘实现只在这里装配。
    """
    WindowsCredentialProvider().save(purpose=purpose, reference=reference, value=value)

def verify_purpose_credential(*, purpose: str, reference: str) -> None:
    """只做「能否解析」的能力探测：解析后立刻清理，明文不离开本函数。"""
    secret = SecretManager.default().resolve(reference, purpose=purpose)
    secret.clear()

def reject_path_links(path: Path) -> None:
    """路径级「拒绝符号链接/交接点」检查的装配入口（接口层不直接依赖基础设施）。"""
    RegisteredPythonEnvironmentResolver._no_links(path)

def record_core_startup_failure(workspace_root: Path, message: str) -> bool:
    """核心启动失败诊断的装配入口：落盘前脱敏、有界、原子替换（#118）。"""
    return record_startup_diagnostic(workspace_root, message)

def read_core_startup_diagnostic(workspace_root: Path) -> str | None:
    """读取核心启动失败诊断（只含可读结论，不含原始日志或凭据）。"""
    return read_startup_diagnostic(workspace_root)

def rebuild_query_index(workspace_root: Path) -> int:
    """显式维护：无活动核心时从权威边界全量重建查询索引（#119）。

    先取得工作空间的全生命周期写锁，避免与活动核心形成双写；核心正在运行时
    以 ``WORKSPACE_IN_USE`` 拒绝，调用方必须先关闭核心再重建。
    """
    from aitest.infrastructure.file_store.records import FileRecordRepository

    # 只维护已登记的工作空间：普通目录不得因维护命令被写成数据目录。
    root = Workspace(workspace_root, create=False).root
    FileCoreLaunchStore.reject_links(root)
    FileCommitStore.reject_links(root)
    lock = LifetimeWriterLock(root / "writer.lock")
    lock.acquire()
    try:
        return FileRecordRepository(root).rebuild_query_index()
    finally:
        lock.release()

