"""C 侧 start 来源绑定解析：把 A 的物化映射变成 `source_binding_digest`。

依据现行接口合同：

- `docs/接口对接/进行中/AB-001-端口与保存/contract.md` 第 3.4 节（1.34）：`materialize`
  成功时返回 `paths`（期望来源路径→workdir 实际路径，含 `sha256`/`size`）与 `content_digest`；
- `docs/接口对接/进行中/BC-001-PreparedRun/contract.md` 第 6 节：
  `source_binding_digest` 由 **C 在 start** 计算，是"在固定 workdir 中解析出的
  期望→实际路径映射"摘要，**不可**与 B 在 prepare 冻结的 `resolved_input_digest` 合并或互替。

本组件只解析并**独立**核对映射，不启动执行、不写业务记录、不声称来源通过：
入口/解释器/实际加载来源是否与冻结绑定相符，仍由既有来源核对决定。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from aitest.application.ports import SourceSnapshotPort
from aitest.domain.evidence.evidence import EvidenceGapKind
from aitest.domain.execution.sources import ExecutionSourceVerification, SourceVerificationState

_DIGEST_PREFIX = "sha256:"
_CWD_PREFIX = "workdir:"
_HASH_BLOCK = 1024 * 1024
_DRIVE = re.compile(r"[A-Za-z]:")


def _escapes_via_link(root: Path, candidate: Path) -> bool:
    """从固定 workdir 到目标的每一段都不得是链接。"""
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


#: 裁定 4A：start 前的四类来源失败。
_START_SOURCE_FAILURES: Final[frozenset[str]] = frozenset(
    {
        "entry_missing",
        "source_mismatch",
        "environment_unregistered",
        "materialization_failed",
    }
)


class SourceBindingUnverified(ValueError):
    code = "SOURCE_BINDING_UNVERIFIED"


class StartSourceBindingResolver:
    """在 start 时把物化结果解析为可核对的来源绑定事实。"""

    def __init__(self, snapshots: SourceSnapshotPort) -> None:
        self._snapshots = snapshots

    def admit_start(
        self,
        *,
        snapshot_id: str,
        destination: str,
        cwd_mapping: str,
        frozen_arguments: Sequence[str],
        actual_arguments: Sequence[str],
        adapter_versions: Mapping[str, str],
        expected_relative_paths: Sequence[str] | None = None,
        expected_source_binding_digest: str | None = None,
        frozen_entry: str | None = None,
        observed_entry: str | None = None,
        frozen_interpreter: str | None = None,
        observed_executable: str | None = None,
    ) -> Mapping[str, object]:
        """一次 start 准入：物化映射核对 + 裁定 1A/2A/3A；任一步失败即抛出来源阻塞。

        只在全部核对通过后返回可直接用于 start 的事实（workdir/cwd/参数/适配器/摘要）。
        本方法不启动执行、不写运行事实；默认装配尚未注入。
        """
        resolved = self.resolve(
            snapshot_id=snapshot_id,
            destination=destination,
            expected_relative_paths=expected_relative_paths,
            expected_source_binding_digest=expected_source_binding_digest,
        )
        workdir = str(resolved["workdir"])
        admitted: dict[str, object] = {
            "snapshot_id": resolved["snapshot_id"],
            "workdir": workdir,
            "paths": resolved["paths"],
            "source_binding_digest": resolved["source_binding_digest"],
            "cwd": self.resolve_cwd(workdir=workdir, cwd_mapping=cwd_mapping),
            "arguments": self.require_frozen_arguments(
                frozen=frozen_arguments, actual=actual_arguments
            ),
            "adapter_kind": self.resolve_adapter_kind(adapter_versions),
        }
        if frozen_entry is not None or observed_entry is not None:
            admitted["registered_entry"] = self.require_registered_entry(
                frozen_entry=str(frozen_entry), observed_entry=str(observed_entry)
            )
        if frozen_interpreter is not None or observed_executable is not None:
            admitted["interpreter"] = self.require_interpreter(
                frozen_interpreter=str(frozen_interpreter),
                observed_executable=str(observed_executable),
            )
        return admitted

    def failure_receipt(self, reason: str) -> Mapping[str, object]:
        """按裁定 4A：start 前四类来源失败一律为不可重试阻塞，并登记来源证据缺口。

        依据需求 P1-AC03/13/25（"来源无法证明不得声称该源码完整通过"、"启动前核对实际来源，
        不符则阻塞"）与 AGENTS 第4节（按具体动作的真实依赖降级，不扩散为全部核心能力故障）：
        回执只声明本次动作的来源不可核实，缺口取领域既有的 `source_unverified`；
        登记、查询、模板与本地保存等不依赖该动作的能力不受影响。
        """
        if reason not in _START_SOURCE_FAILURES:
            raise SourceBindingUnverified(f"unknown start source failure: {reason}")
        return {
            "code": SourceBindingUnverified.code,
            "retryable": False,
            "gap": str(EvidenceGapKind.SOURCE_UNVERIFIED),
            "reason": reason,
            "next_step": "核对冻结来源与实际物化材料；如需更换入口或参数，请重新 prepare",
        }

    def resolve_cwd(self, *, workdir: str, cwd_mapping: str) -> str:
        """按裁定 1A 解析冻结的工作目录映射（`workdir:<安全相对路径>`）。

        依据[架构01 第12节](docs/项目文档/一期/架构文档/01-项目与计划.md)：物化始终在
        固定 workdir 内，"拒绝路径穿越与链接逃逸"。解析结果必须是该 workdir 内的真实目录。
        """
        if not isinstance(cwd_mapping, str) or not cwd_mapping.startswith(_CWD_PREFIX):
            raise SourceBindingUnverified("cwd mapping must use the fixed workdir prefix")
        relative = cwd_mapping[len(_CWD_PREFIX) :]
        if (
            not relative.strip()
            or relative.startswith(("/", "\\"))
            or Path(relative).is_absolute()
            or _DRIVE.fullmatch(relative[:2])
            or ".." in Path(relative).parts
        ):
            raise SourceBindingUnverified("cwd mapping must be a safe relative path")
        if not isinstance(workdir, str) or not workdir.strip():
            raise SourceBindingUnverified("cwd mapping requires the fixed workdir")
        root = Path(workdir).resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise SourceBindingUnverified("cwd mapping escapes the fixed workdir")
        if _escapes_via_link(root, candidate):
            raise SourceBindingUnverified("cwd mapping must not traverse links")
        if not candidate.is_dir():
            raise SourceBindingUnverified(
                "cwd mapping does not resolve to a directory inside the workdir"
            )
        return candidate.as_posix()

    def require_verified_source(
        self, verification: ExecutionSourceVerification
    ) -> ExecutionSourceVerification:
        """start 准入要求来源核验为 `verified`；其余状态一律来源不符阻塞。

        复用既有 [`source_checks`](source_checks.py) 产出的核验事实（不重复实现缺口判定）：
        `mismatch`/`unverified`/`blocked`/`unknown` 都不得启动，并按 4A 给出阻塞。
        """
        if not isinstance(verification, ExecutionSourceVerification):
            raise SourceBindingUnverified("a source verification fact is required")
        if verification.state is not SourceVerificationState.VERIFIED:
            gaps = ",".join(str(item) for item in verification.gap_ids)
            raise SourceBindingUnverified(
                f"source verification is {verification.state.value}: {gaps}"
            )
        return verification

    def require_interpreter(self, *, frozen_interpreter: str, observed_executable: str) -> str:
        """冻结的解释器身份必须与实际可执行文件一致（A-08 的"实际解释器"核对）。

        依据架构01 第12节（prepare 冻结"解释器/可执行文件身份"）与需求 P1-AC13；两侧都必须
        是非空文本且逐字一致，不同即来源不符（不匹配不得启动）。
        """
        for value in (frozen_interpreter, observed_executable):
            if not isinstance(value, str) or not value.strip():
                raise SourceBindingUnverified("interpreter identity must be nonempty text")
        if frozen_interpreter != observed_executable:
            raise SourceBindingUnverified(
                "observed executable differs from the frozen interpreter identity"
            )
        return frozen_interpreter

    def require_registered_entry(self, *, frozen_entry: str, observed_entry: str) -> str:
        """冻结的已登记入口必须与实际使用的入口一致。

        依据架构01 第12节（prepare 冻结"已登记入口"）与需求 P1-AC13（"启动前核对实际来源，
        不符则阻塞"）：两侧都必须是非空文本，且逐字一致；不同即来源不符。
        """
        for value in (frozen_entry, observed_entry):
            if not isinstance(value, str) or not value.strip():
                raise SourceBindingUnverified("registered entry identity must be nonempty text")
        if frozen_entry != observed_entry:
            raise SourceBindingUnverified(
                "observed entry differs from the frozen registered entry"
            )
        return frozen_entry

    def require_frozen_arguments(
        self,
        *,
        frozen: Sequence[str],
        actual: Sequence[str],
        substitutions: Mapping[str, object] | None = None,
    ) -> tuple[str, ...]:
        """按裁定 2A：执行参数必须**逐字**取自冻结绑定，不接受任何代入或改写。

        依据架构01 第12节（prepare 冻结"已登记入口与参数"，`resolved_input_digest` 覆盖）；
        架构02 第7节把"模型正文决定新命令"列为禁止项。要换命令只能重新 prepare（新意图）。
        """
        if substitutions:
            raise SourceBindingUnverified(
                "entry arguments must not be substituted; a different command needs a new prepare"
            )
        if not isinstance(frozen, (list, tuple)) or not isinstance(actual, (list, tuple)):
            raise SourceBindingUnverified("entry arguments must be exact string sequences")
        for item in (*frozen, *actual):
            if not isinstance(item, str):
                raise SourceBindingUnverified(
                    "entry arguments must be exact text, not coerced values"
                )
        if tuple(frozen) != tuple(actual):
            raise SourceBindingUnverified(
                "executed arguments differ from the frozen registered entry arguments"
            )
        return tuple(frozen)

    def resolve_adapter_kind(self, adapter_versions: Mapping[str, str]) -> str:
        """按裁定 3A：所选适配器由 `adapter_versions` 的**唯一键**决定。

        依据架构01 第12节（"依赖集合与适配器版本"属 prepare 冻结的来源约束）与架构05
        第9节（能力协商含"执行入口类型"）：键缺失、多于一个或键值空白即拒绝，要求重新 prepare。
        """
        if not isinstance(adapter_versions, Mapping) or not adapter_versions:
            raise SourceBindingUnverified("the frozen source binding lacks an adapter version")
        keys = tuple(adapter_versions)
        if len(keys) != 1:
            raise SourceBindingUnverified(
                "exactly one adapter version key must select the adapter"
            )
        key = keys[0]
        value = adapter_versions[key]
        if (
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
        ):
            raise SourceBindingUnverified("adapter version key and value must be nonempty text")
        return key

    def resolve(
        self,
        *,
        snapshot_id: str,
        destination: str,
        expected_relative_paths: Sequence[str] | None = None,
        expected_source_binding_digest: str | None = None,
    ) -> Mapping[str, object]:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise SourceBindingUnverified("start source binding requires a saved snapshot identity")
        if not isinstance(destination, str) or not destination.strip():
            raise SourceBindingUnverified("start source binding requires a fixed workdir")
        result = self._snapshots.materialize(snapshot_id, destination)
        if not isinstance(result, Mapping):
            raise SourceBindingUnverified("materialization did not return a verifiable result")
        if result.get("state") != "materialized" or result.get("verified") is not True:
            refused = result.get("refused")
            detail = (
                ",".join(sorted(str(item) for item in refused))
                if isinstance(refused, (list, tuple))
                else ""
            )
            raise SourceBindingUnverified(f"materialization is not verified: {detail}")
        returned_snapshot = result.get("snapshot_id")
        if returned_snapshot != snapshot_id:
            raise SourceBindingUnverified("materialization belongs to another snapshot")
        workdir = str(result.get("destination") or "")
        if not workdir:
            raise SourceBindingUnverified("materialization lacks its actual workdir")
        entries = _entries(result.get("paths"))
        recorded = result.get("content_digest")
        if not isinstance(recorded, str) or not _is_digest(recorded):
            raise SourceBindingUnverified("materialization lacks an exact mapping digest")
        recomputed = _mapping_digest(entries)
        if recomputed != recorded:
            raise SourceBindingUnverified("mapping digest differs from the materialization claim")
        root = Path(workdir)
        if root != Path(destination).resolve():
            raise SourceBindingUnverified(
                "materialization workdir differs from the requested fixed workdir"
            )
        for entry in entries:
            actual = Path(str(entry["actual_path"]))
            digest, size = entry["sha256"], entry["size"]
            if not isinstance(digest, str) or type(size) is not int:
                raise SourceBindingUnverified("mapping entry digest or size is not exact")
            if not actual.is_absolute() or not actual.is_relative_to(root):
                raise SourceBindingUnverified("mapped actual path escapes the fixed workdir")
            _verify_file(actual, digest, size)
        if expected_relative_paths is not None:
            _require_exact_coverage(entries, expected_relative_paths)
        if expected_source_binding_digest is not None:
            if (
                not isinstance(expected_source_binding_digest, str)
                or not _is_digest(expected_source_binding_digest)
            ):
                raise SourceBindingUnverified(
                    "a saved source binding digest must be an exact sha256 digest"
                )
            if expected_source_binding_digest != recomputed:
                raise SourceBindingUnverified(
                    "re-resolved mapping differs from the saved source binding digest"
                )
        return {
            "snapshot_id": snapshot_id,
            "workdir": root.as_posix(),
            "paths": entries,
            "source_binding_digest": recomputed,
        }


def _require_exact_coverage(
    entries: list[dict[str, object]], expected_relative_paths: Sequence[str]
) -> None:
    """调用方给出冻结期望时，映射必须**恰好**覆盖它：不缺项、不多项、不重复。"""
    expected: list[str] = []
    for item in expected_relative_paths:
        if not isinstance(item, str) or not item.strip():
            raise SourceBindingUnverified("expected relative paths must be nonempty text")
        expected.append(item)
    if len(set(expected)) != len(expected):
        raise SourceBindingUnverified("expected relative paths must be unique")
    actual = [str(entry["relative_path"]) for entry in entries]
    if sorted(actual) != sorted(expected):
        raise SourceBindingUnverified(
            "resolved mapping does not cover the frozen expected source paths exactly"
        )


def _entries(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SourceBindingUnverified("materialization lacks its expected-to-actual path mapping")
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    actuals: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise SourceBindingUnverified("mapping entries must be mappings")
        relative, actual = item.get("relative_path"), item.get("actual_path")
        digest, size = item.get("sha256"), item.get("size")
        if (
            not isinstance(relative, str)
            or not relative.strip()
            or relative.startswith(("/", "\\"))
            or ".." in Path(relative).parts
            or not isinstance(actual, str)
            or not actual.strip()
            or not isinstance(digest, str)
            or not _is_digest(digest)
            or type(size) is not int
            or size < 0
        ):
            raise SourceBindingUnverified(
                "mapping entry identity, type or digest cannot be verified"
            )
        if relative in seen:
            raise SourceBindingUnverified("mapping entries must be unique per expected path")
        seen.add(relative)
        actual_key = _actual_key(actual)
        if actual_key in actuals:
            raise SourceBindingUnverified(
                "two expected source paths must not resolve to the same actual file"
            )
        actuals.add(actual_key)
        entries.append(
            {"relative_path": relative, "actual_path": actual, "sha256": digest, "size": size}
        )
    return entries


def _actual_key(actual: str) -> str:
    """实际路径的归属键；Windows 大小写不敏感，避免同一文件被当成两个来源。"""
    text = Path(actual).as_posix()
    return text.casefold() if os.name == "nt" else text


def _verify_file(path: Path, digest: str, size: int) -> None:
    """有界核对：读取至多 ``size + 1`` 字节，长度与摘要都必须精确相符。"""
    try:
        if not path.is_file() or path.is_symlink():
            raise SourceBindingUnverified("mapped actual path is not a regular file")
        if os.stat(path).st_nlink != 1:
            raise SourceBindingUnverified(
                "mapped actual path shares its bytes with material outside the workdir"
            )
        hasher = hashlib.sha256()
        read = 0
        with path.open("rb") as handle:
            while block := handle.read(min(_HASH_BLOCK, size + 1 - read)):
                hasher.update(block)
                read += len(block)
                if read > size:
                    break
    except OSError as error:
        raise SourceBindingUnverified("mapped actual path cannot be read") from error
    if read != size or _DIGEST_PREFIX + hasher.hexdigest() != digest:
        raise SourceBindingUnverified("mapped actual bytes differ from their exact reference")


def _mapping_digest(entries: list[dict[str, object]]) -> str:
    encoded = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return _DIGEST_PREFIX + hashlib.sha256(encoded).hexdigest()


def _is_digest(value: str) -> bool:
    if not value.startswith(_DIGEST_PREFIX):
        return False
    hexdigest = value[len(_DIGEST_PREFIX) :]
    return len(hexdigest) == 64 and all(char in "0123456789abcdef" for char in hexdigest)


__all__ = [
    "SourceBindingUnverified",
    "StartSourceBindingResolver",
]
