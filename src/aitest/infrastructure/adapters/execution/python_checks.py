"""实际解释器/入口/加载来源探针（A-08，一期执行来源合同）。

B 的应用用例（``application/execution/source_checks.py``）决定**何时**对
哪条入口做来源核对；本模块只提供 A 侧的受控取证能力与机械判定：

- 用**登记的解释器**启动受控子进程（``-E`` 屏蔽环境 PYTHONPATH，只注入
  显式声明的映射路径），在物化目录边界内导入登记模块；
- 记录解释器真实路径（realpath，穿透符号链接）、版本、``sys.path``、
  落入物化范围的路径项、每个登记模块的**实际加载文件**与字节摘要；
- :func:`evaluate_source_binding` 把实际事实与冻结绑定逐项比对：摘要一致
  且来自物化范围才 ``verified``；项目模块被外部同名包覆盖或摘要不符为
  ``mismatch``（阻塞启动）；导入失败/动态来源无法证明为 ``unverified``
  （证据缺口，不冒充通过）。

探针不写业务文件、不改活动源码；产物是纯事实 DTO，供 B 用例持久化。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from aitest.domain.execution.runs import (
    AdapterKind,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExecutionRequest,
    FailureClass,
    OutputCursor,
    StopRequestResult,
)
from aitest.domain.execution.sources import SourceCheckResult, SourceCheckType
from aitest.infrastructure.adapters.execution.command import (
    CommandAdapter,
    CommandRegistration,
    SecretResolver,
)

_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_BEGIN = "@@@ ATEST SOURCE PROBE BEGIN"
_END = "@@@ ATEST SOURCE PROBE END"
_HASH_BLOCK = 64 * 1024

BindingState = Literal["verified", "mismatch", "unverified"]


class PythonSourceProbeError(RuntimeError):
    """解释器不可执行、探针协议被破坏等取证失败。"""


@dataclass(frozen=True, slots=True)
class LoadedModuleFact:
    """一个登记模块在子进程中的实际加载事实。"""

    module: str
    file: str | None
    sha256: str | None
    load_error: str | None

    @property
    def loaded(self) -> bool:
        return self.file is not None and self.load_error is None


@dataclass(frozen=True, slots=True)
class LoadSourceFact:
    """一次受控加载取证的完整事实（执行来源合同 §12）。"""

    requested_executable: str
    executable: str
    version: str
    prefix: str
    pythonpath_env: str | None
    sys_path: tuple[str, ...]
    project_scope_entries: tuple[str, ...]
    modules: tuple[LoadedModuleFact, ...] = ()
    probe_error: str | None = None


@dataclass(frozen=True, slots=True)
class SourceBindingVerdict:
    """实际来源与冻结绑定的机械判定。"""

    state: BindingState
    blocked: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


_PROBE_SCRIPT = r"""
import hashlib, importlib, json, os, sys

args = json.loads(sys.argv[1])
for path in reversed(args["pythonpath"]):
    if path and path not in sys.path:
        sys.path.insert(0, path)

root = os.path.realpath(args["root"])
modules = {}
for name in args["modules"]:
    try:
        imported = importlib.import_module(name)
        file_path = getattr(imported, "__file__", None)
        digest = None
        if file_path:
            hasher = hashlib.sha256()
            with open(file_path, "rb") as handle:
                while block := handle.read(65536):
                    hasher.update(block)
            digest = hasher.hexdigest()
        modules[name] = {"file": file_path, "sha256": digest, "error": None}
    except BaseException as exc:  # noqa: BLE001 - 取证：任何失败都是缺口事实
        modules[name] = {
            "file": None,
            "sha256": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

# 目录归属必须整段比较：前缀 startswith 会把兄弟目录（如 root 的
# "materialized-evil"）误报成项目范围内路径，造成范围事实失真。
scope_entries = [
    path
    for path in sys.path
    if path
    and (
        (real := os.path.realpath(path)) == root
        or real.startswith(root + os.sep)
    )
]
payload = {
    "executable": os.path.realpath(sys.executable),
    "version": sys.version,
    "prefix": sys.prefix,
    "pythonpath_env": os.environ.get("PYTHONPATH"),
    "sys_path": list(sys.path),
    "project_scope_entries": scope_entries,
    "modules": modules,
}
print("@@@ ATEST SOURCE PROBE BEGIN")
json.dump(payload, sys.stdout)
print()
print("@@@ ATEST SOURCE PROBE END")
"""


class PythonLoadSourceProbe:
    """在登记解释器中核对登记模块的实际加载来源。"""

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._executable = executable or sys.executable
        self._timeout = timeout_seconds

    @property
    def executable(self) -> str:
        return os.path.realpath(self._executable)

    def probe(
        self,
        *,
        materialized_root: str | Path,
        modules: tuple[str, ...] | list[str],
        extra_pythonpath: tuple[str, ...] | list[str] = (),
        extra_env: dict[str, str] | None = None,
    ) -> LoadSourceFact:
        root = Path(materialized_root).resolve()
        if not root.is_dir():
            raise PythonSourceProbeError(f"物化目录不存在: {root}")
        names = tuple(modules)
        for name in names:
            if not _MODULE_NAME_RE.fullmatch(name):
                raise PythonSourceProbeError(f"模块名非法（不执行任意代码）: {name}")
        pythonpath = [os.path.realpath(str(root)), *(str(p) for p in extra_pythonpath)]
        env = dict(os.environ)
        # -E 阻止解释器自动消费 PYTHONPATH，探针按声明顺序手工注入；同时把
        # 映射显式放进环境，使子进程报告的 PYTHONPATH 事实就是本次绑定。
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        if extra_env:
            env.update(extra_env)
        payload = json.dumps(
            {"root": str(root), "modules": names, "pythonpath": pythonpath}
        )
        try:
            completed = subprocess.run(
                [self._executable, "-E", "-c", _PROBE_SCRIPT, payload],
                capture_output=True,
                timeout=self._timeout,
                env=env,
                check=False,
            )
        except OSError as exc:
            raise PythonSourceProbeError(f"解释器无法启动: {self._executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PythonSourceProbeError("来源探针超时") from exc

        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace")[-500:]
            return LoadSourceFact(
                requested_executable=self.executable,
                executable=self.executable,
                version="",
                prefix="",
                pythonpath_env=None,
                sys_path=(),
                project_scope_entries=(),
                modules=(),
                probe_error=f"探针进程退出码 {completed.returncode}: {error}",
            )
        parsed = _extract_payload(completed.stdout.decode("utf-8", errors="replace"))
        pythonpath_env = parsed.get("pythonpath_env")
        if pythonpath_env is not None and not isinstance(pythonpath_env, str):
            raise PythonSourceProbeError("探针 pythonpath_env 事实非法")
        sys_path = parsed.get("sys_path")
        scope = parsed.get("project_scope_entries")
        modules_raw = parsed.get("modules")
        if (
            not isinstance(sys_path, list)
            or not isinstance(scope, list)
            or not isinstance(modules_raw, dict)
            or not all(isinstance(p, str) for p in sys_path)
            or not all(isinstance(p, str) for p in scope)
        ):
            raise PythonSourceProbeError("探针事实结构非法")
        module_items: list[LoadedModuleFact] = []
        for raw_name, raw_item in modules_raw.items():
            if not isinstance(raw_name, str) or not isinstance(raw_item, dict):
                raise PythonSourceProbeError("探针模块事实非法")
            file_value = raw_item.get("file")
            digest_value = raw_item.get("sha256")
            error_value = raw_item.get("error")
            module_items.append(
                LoadedModuleFact(
                    module=raw_name,
                    file=file_value if isinstance(file_value, str) else None,
                    sha256=digest_value if isinstance(digest_value, str) else None,
                    load_error=error_value if isinstance(error_value, str) else None,
                )
            )
        return LoadSourceFact(
            requested_executable=self.executable,
            executable=str(parsed["executable"]),
            version=str(parsed["version"]),
            prefix=str(parsed["prefix"]),
            pythonpath_env=pythonpath_env,
            sys_path=tuple(sys_path),
            project_scope_entries=tuple(scope),
            modules=tuple(module_items),
        )


def _extract_payload(stdout: str) -> dict[str, object]:
    try:
        begin = stdout.index(_BEGIN) + len(_BEGIN)
        end = stdout.index(_END, begin)
    except ValueError as exc:
        raise PythonSourceProbeError("探针输出协议标记缺失，拒绝猜测来源") from exc
    try:
        parsed = json.loads(stdout[begin:end].strip())
    except json.JSONDecodeError as exc:
        raise PythonSourceProbeError("探针事实不是合法 JSON") from exc
    if not isinstance(parsed, dict):
        raise PythonSourceProbeError("探针事实结构非法")
    return parsed


def evaluate_source_binding(
    fact: LoadSourceFact,
    *,
    expected_modules: dict[str, str],
    project_root: str | Path,
) -> SourceBindingVerdict:
    """把实际加载事实与冻结绑定（模块→期望 sha256）逐项核对。

    - 探针进程自身失败：``unverified``（取证缺口），不阻塞由调用方按
      "无法验证则阻塞入口"的策略决定；这里同时置 ``blocked=True``，
      因为来源事实缺失时入口不得带着 source_verified 启动；
    - 模块导入失败/无文件：``unverified``；
    - 实际文件不在物化范围（外部同名包覆盖、editable 指回原仓库）或
      摘要不符：``mismatch`` 且 ``blocked=True``；
    - 未提供任何期望绑定：``unverified``（没有任何核对发生，不冒充通过）。
    """
    root = Path(project_root).resolve()
    reasons: list[str] = []
    mismatches = 0
    unverified = 0

    if fact.probe_error is not None:
        return SourceBindingVerdict(
            state="unverified",
            blocked=True,
            reasons=(f"来源探针失败: {fact.probe_error}",),
        )

    if not expected_modules:
        return SourceBindingVerdict(
            state="unverified",
            blocked=False,
            reasons=("未提供任何冻结绑定（模块→期望摘要），没有发生核对",),
        )

    observed = {item.module: item for item in fact.modules}
    for name, expected_digest in expected_modules.items():
        item = observed.get(name)
        if item is None:
            unverified += 1
            reasons.append(f"{name}: 探针未报告该模块")
            continue
        if item.load_error is not None:
            unverified += 1
            reasons.append(f"{name}: 无法导入/动态来源未知（{item.load_error}）")
            continue
        if item.file is None or item.sha256 is None:
            unverified += 1
            reasons.append(f"{name}: 无文件型加载来源，无法证明字节身份")
            continue
        # Windows 文件系统大小写不敏感：按平台规则归一大小写与分隔符后再
        # 比较归属，避免把真实位于物化目录内的文件误判为范围外来源（假阻塞）。
        actual = os.path.normcase(str(Path(item.file).resolve()))
        root_prefix = os.path.normcase(str(root))
        if actual != root_prefix and not actual.startswith(root_prefix + os.sep):
            mismatches += 1
            reasons.append(
                f"{name}: 项目模块被范围外来源覆盖（{item.file} 不在物化目录内）"
            )
            continue
        if item.sha256 != expected_digest:
            mismatches += 1
            reasons.append(
                f"{name}: 实际加载字节摘要 {item.sha256[:12]} 与冻结绑定 "
                f"{expected_digest[:12]} 不符"
            )

    state: BindingState
    if mismatches:
        state = "mismatch"
    elif unverified:
        state = "unverified"
    else:
        state = "verified"
    return SourceBindingVerdict(
        state=state,
        blocked=mismatches > 0,
        reasons=tuple(reasons),
    )


def file_sha256(path: str | Path) -> str:
    """供装配侧在固定/物化后计算期望摘要的同一口径工具。"""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(_HASH_BLOCK):
            hasher.update(block)
    return hasher.hexdigest()


class PythonChecksAdapter:
    """ExecutionPort-compatible wrapper for registered Python checks."""

    adapter_version = "python-checks/1.0"

    def __init__(self, secret_resolver: SecretResolver | None = None) -> None:
        self._commands = CommandAdapter(secret_resolver)

    def register(self, registration: CommandRegistration) -> None:
        self._commands.register(registration)

    def start(self, request: ExecutionRequest) -> ExecutionHandle:
        return self._commands.start(request)

    def inspect(self, handle: ExecutionHandle) -> ExecutionInspectionResult:
        return self._commands.inspect(handle)

    def collect(
        self,
        handle: ExecutionHandle,
        cursors: tuple[OutputCursor, ...] | None = None,
    ) -> ExecutionCollectionResult:
        return self._commands.collect(handle, cursors)

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult:
        return self._commands.request_stop(handle)

    def run_check(
        self,
        request: ExecutionRequest,
        *,
        check_result_id: str,
        check_type: SourceCheckType,
        scope: str,
        source_snapshot_ref: str,
        environment_ref: str,
        rules_revision: str,
        raw_output_evidence_ref: str | None = None,
        expected_exit_code: int = 0,
        poll_interval_seconds: float = 0.01,
        timeout_seconds: float = 60.0,
    ) -> SourceCheckResult:
        """Run one registered Python check and save only its actual facts."""
        handle = self.start(request)
        deadline = time.monotonic() + timeout_seconds
        inspection = self.inspect(handle)
        while (
            inspection.state is ExecutionInspectionState.RUNNING
            and time.monotonic() < deadline
        ):
            time.sleep(poll_interval_seconds)
            inspection = self.inspect(handle)
        collection = self.collect(handle)
        if inspection.state is ExecutionInspectionState.RUNNING:
            self.request_stop(handle)
            failure_class = FailureClass.ENVIRONMENT_UNREACHABLE
        elif collection.exit_fact_ref is None or not collection.complete:
            failure_class = FailureClass.TOOL_FAILURE
        elif collection.exit_fact_ref.real_exit_code == expected_exit_code:
            failure_class = FailureClass.PASSED
        else:
            failure_class = FailureClass.SOURCE_ERROR
        return SourceCheckResult(
            check_result_id=check_result_id,
            attempt_id=request.attempt_id,
            check_type=check_type,
            scope=scope,
            source_snapshot_ref=source_snapshot_ref,
            environment_ref=environment_ref,
            rules_revision=rules_revision,
            adapter_version=self.adapter_version,
            failure_class=failure_class,
            adapter_kind=AdapterKind.PYTHON_CHECKS,
            entry_ref=request.registered_entry.entrypoint,
            argument_refs=request.registered_entry.arguments,
            raw_output_evidence_ref=raw_output_evidence_ref,
        )


__all__ = [
    "LoadedModuleFact",
    "LoadSourceFact",
    "PythonChecksAdapter",
    "PythonLoadSourceProbe",
    "PythonSourceProbeError",
    "SourceBindingVerdict",
    "evaluate_source_binding",
    "file_sha256",
]
