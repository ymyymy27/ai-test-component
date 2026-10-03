"""模型出站的端口协议：**B 侧窄接口**，与薄底座同一路线。

**这不是 A 的 `application/ports.py`。** 架构文档第 9 节把
`ModelProvider` / `ProjectionPort` / `SecretPort` 归 A。

**2026-10-03 实测更新**：A 的 `application/ports.py` 已冻结三个端口的签名，
并且——关键——它**直接 import 本模块的类型**（`ModelCall` / `ModelCallResult` /
`Projection` / `ProjectedMaterial` / `ProjectionStatus` 均以 `X as X` 形式引用），
因此**不存在两套同义类型**，两边的形状差异只剩凭据一处（见
`docs/接口对接/进行中/AB-001-端口与保存/contract.md` 第 8.16 节）。

本模块定义的三个协议是**编排的依赖面**，与 A 的端口对应关系：

- `MaterialProjector` ↔ A 的 `ProjectionPort`——**同一套类型**，形状无差异；
- `ModelCaller` ↔ A 的 `ModelProvider`——**同一套类型**，形状无差异；
- `CredentialResolver` ↔ A 的 `SecretPort`——**有差异**（两处）：
  ① A 的 `resolve(reference, *, purpose) -> ResolvedSecret`（`ports.py` 第 423 行）
  **要求 `reference`**，而本模块的调用方只传 `purpose`；
  ② 返回类型是 `ResolvedSecret`（受控对象、不是裸字符串），
  本协议只回"状态"，类型不同（见 AB-001 第 8.16 节）。

三者的**共同底线**（需求 §7、三期上传白名单同理）：

1. **凭据正文不进配置、日志、面板、导出**：`CredentialResolver` 只回"可用/不可用/用途不匹配"，
   类型层面**没有**返回正文的方法。
2. **脱敏在投影时完成**：编排只把**投影后**的字节交给 `ModelCaller`，
   本模块不重复实现红action。
3. **无法安全投影则排除并显示缺口**：`Projection` 带 `excluded`，编排据此登记缺口。

真实实现属 A；本模块另提供内存实现（`tests/support/memory_model.py`）用于验证编排。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from aitest.domain.planning.model_outbound import MaterialKind


class ProjectionStatus(StrEnum):
    """投影结果状态。**不得用"投影成功"掩盖被排除的材料。**"""

    COMPLETE = "complete"
    #: 有材料无法安全投影而被排除：材料**不完整**，必须显示缺口。
    PARTIAL = "partial"


class ModelCallStatus(StrEnum):
    """一次模型调用的结果状态（与错误分类分开：`ok` 之外都由 `error_kind` 说明）。"""

    OK = "ok"
    FAILED = "failed"


class CredentialStatus(StrEnum):
    """凭据解析结果。**没有"返回正文"这一档。**"""

    AVAILABLE = "available"
    MISSING = "missing"
    PURPOSE_MISMATCH = "purpose_mismatch"


@dataclass(frozen=True, slots=True)
class ProjectedMaterial:
    """一项**已脱敏**的投影材料。"""

    material_kind: MaterialKind
    field_path: str
    projected_text: str
    digest: str

    def __post_init__(self) -> None:
        if not self.field_path.strip():
            raise ValueError("field_path must not be empty")
        if not self.projected_text.strip():
            raise ValueError("projected_text must not be empty")
        if not self.digest.strip():
            raise ValueError("projection digest must not be empty")


@dataclass(frozen=True, slots=True)
class Projection:
    """一次投影的完整结果。

    `projected` 是**真正送出去的**材料；`excluded` 是**无法安全投影而被排除**的材料，
    编排必须把它登记为缺口，不能静默丢弃。
    """

    status: ProjectionStatus
    projected: tuple[ProjectedMaterial, ...] = ()
    excluded: tuple[tuple[MaterialKind, str], ...] = ()
    projection_digest: str = ""
    policy_revision: int = 0

    def __post_init__(self) -> None:
        if self.status is ProjectionStatus.COMPLETE and self.excluded:
            raise ValueError("a complete projection must not exclude material")
        if self.status is ProjectionStatus.PARTIAL and not self.excluded:
            raise ValueError("a partial projection must name the excluded material")
        if self.status is ProjectionStatus.COMPLETE and not self.projected:
            raise ValueError("a complete projection must carry at least one item")
        for item in self.projected:
            if not item.digest.strip():
                raise ValueError("every projected item needs a digest")
        if self.projection_digest.strip() and not self.projected:
            raise ValueError("a projection digest requires projected material")
        if self.projected and not self.projection_digest.strip():
            raise ValueError("projected material requires a projection digest")

    def kinds(self) -> tuple[MaterialKind, ...]:
        return tuple(item.material_kind for item in self.projected)


@dataclass(frozen=True, slots=True)
class CredentialResolution:
    """凭据解析结果；**只有状态，没有正文**。"""

    status: CredentialStatus
    purpose: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.purpose.strip():
            raise ValueError("purpose must not be empty")
        if self.status is not CredentialStatus.AVAILABLE and not self.detail.strip():
            raise ValueError("an unavailable credential must explain why")

    @property
    def usable(self) -> bool:
        return self.status is CredentialStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class ModelCall:
    """一次模型调用的输入。**只包含已投影的字节。**"""

    task_type: str
    projected: tuple[ProjectedMaterial, ...]
    projection_digest: str
    endpoint_address: str
    model_id: str
    timeout_seconds: int
    policy_revision: int

    def __post_init__(self) -> None:
        for name in (
            "task_type",
            "projection_digest",
            "endpoint_address",
            "model_id",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not self.projected:
            raise ValueError("a model call must carry at least one projected item")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.policy_revision < 1:
            raise ValueError("policy_revision must be >= 1")


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """一次模型调用的结果。

    `error_kind` 为 `None` 表示成功。**任何情况下本对象都不代表"结论"** ——
    模型只输出草稿（架构文档第 9 节）。
    """

    status: ModelCallStatus
    draft_text: str = ""
    provider_request_id: str | None = None
    error_kind: str | None = None
    error_detail: str = ""

    def __post_init__(self) -> None:
        if self.status is ModelCallStatus.OK:
            if not self.draft_text.strip():
                raise ValueError("a successful call must carry draft text")
            if self.error_kind is not None:
                raise ValueError("a successful call must not carry an error kind")
            return
        if not self.error_kind:
            raise ValueError("a failed call must classify its error")
        if self.draft_text:
            raise ValueError("a failed call must not carry draft text")


# ------------------------------------------------------------------ 协议


class MaterialProjector(Protocol):
    """把**显式选定**的材料生成脱敏投影。

    实现方必须：按材料逐项脱敏、无法安全处理时放进 `excluded` 而不是伪造完整证据、
    返回**真实字节**的摘要。
    """

    def project(
        self,
        *,
        material: Mapping[MaterialKind, str],
        source_snippets_enabled: bool,
    ) -> Projection: ...


class CredentialResolver(Protocol):
    """按**用途**解析凭据引用；**永不回传正文**。"""

    def resolve(self, *, purpose: str) -> CredentialResolution: ...


class ModelCaller(Protocol):
    """发一次模型请求。**只接收已投影的材料**，不接收原始业务对象。"""

    def call(self, request: ModelCall) -> ModelCallResult: ...


__all__ = [
    "CredentialResolution",
    "CredentialResolver",
    "CredentialStatus",
    "MaterialProjector",
    "ModelCall",
    "ModelCallResult",
    "ModelCallStatus",
    "ModelCaller",
    "ProjectedMaterial",
    "Projection",
    "ProjectionStatus",
]
