"""start 物化与工作目录解析：把固定快照物化到 `workdirs/<run_id>` 并完成准入。

依据架构01 第12节："物化始终在 `workdirs/<run_id>`，包含所选模块依赖闭包、入口、共享配置
和必要资源，排除凭据/生成物，**拒绝路径穿越与链接逃逸**"；"start 在固定 workdir 中解析
实际路径，保存期望→实际路径映射和 `source_binding_digest`"。

复用既有实现，不重复判定：
- `SourceSnapshotPort.materialize`（AB-001 1.34：返回 `paths`/`content_digest`，拒绝时 `refused`）；
- `StartSourceBindingResolver`（映射六处核对、cwd 受限解析、适配器唯一键、参数逐字、失败回执）。

本组件只做**物化与解析**：不启动进程、不写业务记录、不改变默认装配。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from aitest.application.execution.start_source_binding import (
    StartSourceBindingResolver,
)
from aitest.application.ports import SourceSnapshotPort
from aitest.contracts.prepared_run import ExecutionSourceBinding


class StartMaterializationBlocked(ValueError):
    """内部阻塞标识：**不是**已登记的协议错误码。"""

    code = "START_MATERIALIZATION_BLOCKED"


class StartMaterializer:
    """把快照物化到固定 workdir，并按冻结绑定解析工作目录/适配器/参数与映射摘要。"""

    def __init__(self, snapshots: SourceSnapshotPort, *, workspace_root: Path) -> None:
        self._snapshots = snapshots
        self._root = Path(workspace_root).resolve()
        self._resolver = StartSourceBindingResolver(snapshots)

    def materialize(
        self,
        *,
        snapshot_id: str,
        run_id: str,
        binding: ExecutionSourceBinding,
    ) -> Mapping[str, object]:
        """物化并准入；任一步失败即阻塞，不返回半个事实。"""
        if not isinstance(binding, ExecutionSourceBinding):
            raise StartMaterializationBlocked("a frozen execution source binding is required")
        destination = self.workdir(run_id)
        expected = self._expected_paths(snapshot_id)
        # 先做**纯**校验：任何绑定侧不合法都必须在落盘之前阻塞，避免留下半份物化产物。
        adapter_kind = self._resolver.resolve_adapter_kind(binding.adapter_versions)
        arguments = self._resolver.require_frozen_arguments(
            frozen=binding.entry_arguments, actual=binding.entry_arguments
        )
        # 跨入口重传：同一 run/快照已物化且字节一致时**读取同一冻结结果**，不重复物化、不覆盖。
        existing = self._existing(snapshot_id, destination)
        resolved = (
            existing
            if existing is not None
            else self._resolver.resolve(
                snapshot_id=snapshot_id,
                destination=str(destination),
                expected_relative_paths=expected,
            )
        )
        workdir = str(resolved["workdir"])
        return {
            "snapshot_id": snapshot_id,
            "run_id": run_id,
            "workdir": workdir,
            "paths": resolved["paths"],
            "source_binding_digest": resolved["source_binding_digest"],
            "cwd": self._resolver.resolve_cwd(
                workdir=workdir, cwd_mapping=binding.cwd_mapping
            ),
            "adapter_kind": adapter_kind,
            "arguments": arguments,
        }

    def _existing(self, snapshot_id: str, destination: Path) -> Mapping[str, object] | None:
        """已物化的同一 run：逐文件核对字节后返回同一冻结映射；不一致则返回 None 走正常物化。"""
        if not destination.is_dir():
            return None
        record = self._snapshots.read_pinned(snapshot_id)
        raw = record.get("files") if isinstance(record, Mapping) else None
        if not isinstance(raw, (list, tuple)) or not raw:
            return None
        entries: list[dict[str, object]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                return None
            name, digest, size = item.get("relative_path"), item.get("sha256"), item.get("size")
            if not isinstance(name, str) or not isinstance(digest, str) or type(size) is not int:
                return None
            actual = destination / name
            try:
                if not actual.is_file() or actual.stat().st_size != size:
                    return None
                content = actual.read_bytes()
            except OSError:
                return None
            if hashlib.sha256(content).hexdigest() != digest:
                return None
            entries.append(
                {
                    "relative_path": name,
                    "actual_path": actual.resolve().as_posix(),
                    "sha256": "sha256:" + digest,
                    "size": size,
                }
            )
        encoded = json.dumps(
            entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return {
            "snapshot_id": snapshot_id,
            "workdir": destination.resolve().as_posix(),
            "paths": entries,
            "source_binding_digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        }

    def workdir(self, run_id: str) -> Path:
        """`workdirs/<run_id>`；拒绝非安全单段标识，绝不越出 workdirs。"""
        if not isinstance(run_id, str) or not run_id.strip():
            raise StartMaterializationBlocked("a nonempty run identity is required")
        if run_id in {".", ".."} or any(char in run_id for char in ("/", "\\", ":")):
            raise StartMaterializationBlocked("run identity must be one safe path segment")
        base = (self._root / "workdirs").resolve()
        candidate = (base / run_id).resolve()
        if candidate == base or not candidate.is_relative_to(base):
            raise StartMaterializationBlocked("the fixed workdir escapes the workspace")
        return candidate

    def _expected_paths(self, snapshot_id: str) -> tuple[str, ...]:
        record = self._snapshots.read_pinned(snapshot_id)
        if not isinstance(record, Mapping):
            raise StartMaterializationBlocked("the pinned snapshot record cannot be verified")
        raw = record.get("files")
        if not isinstance(raw, (list, tuple)) or not raw:
            raise StartMaterializationBlocked("the pinned snapshot has no frozen file manifest")
        names: list[str] = []
        for item in raw:
            name = item.get("relative_path") if isinstance(item, Mapping) else None
            if not isinstance(name, str) or not name.strip():
                raise StartMaterializationBlocked("a frozen manifest entry lacks its path")
            names.append(name)
        return tuple(names)


__all__ = ["StartMaterializationBlocked", "StartMaterializer"]
