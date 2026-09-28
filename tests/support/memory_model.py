"""模型出站的内存实现：让编排在没有 A 的端口时也能真实验证。

**这是测试支撑，不是生产实现，也不注册为可用能力。**
真实实现属 A（`ProjectionPort` / `ModelProvider` / `SecretPort`），B 不碰其目录。

三个替身的共同底线与生产要求一致：

- `MemoryProjector` 做**真实脱敏**（去掉疑似凭据行），并把无法安全处理的项目放进 `excluded`
  而不是伪造完整；
- `MemoryCredentialResolver` **不持有也不回传凭据正文**，只有状态；
- `MemoryModelCaller` **不判断业务通过**，只回草稿文本或归类错误。
"""

from __future__ import annotations

from collections.abc import Mapping

from aitest.application.planning.model_ports import (
    CredentialResolution,
    CredentialStatus,
    ModelCall,
    ModelCallResult,
    ModelCallStatus,
    ProjectedMaterial,
    Projection,
    ProjectionStatus,
)
from aitest.domain.planning.model_outbound import MaterialKind

#: 疑似凭据的标记；命中即视为**无法安全投影**（不猜测、不替换后放行）。
_CREDENTIAL_MARKERS = (
    "api_key=",
    "apikey=",
    "authorization:",
    "bearer ",
    "password=",
    "secret=",
    "token=",
)


def _looks_like_credential(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _CREDENTIAL_MARKERS)


def _redact(text: str) -> str:
    """保守脱敏：命中疑似凭据标记的**整行**丢弃，保留其余行。"""
    kept = [
        line
        for line in text.splitlines()
        if line.strip() and not _looks_like_credential(line)
    ]
    return "\n".join(kept)


class MemoryProjector:
    """内存投影器：逐项脱敏，无法安全处理的进 `excluded`。

    `force_exclusions` 用于测试"投影不全 → 阻塞并显示缺口"这条路径。
    """

    def __init__(
        self, *, force_exclusions: tuple[MaterialKind, ...] = ()
    ) -> None:
        self._forced = set(force_exclusions)

    def project(
        self,
        *,
        material: Mapping[MaterialKind, str],
        source_snippets_enabled: bool,
    ) -> Projection:
        projected: list[ProjectedMaterial] = []
        excluded: list[tuple[MaterialKind, str]] = []
        for kind, text in material.items():
            path = f"material.{kind.value}"
            if kind in self._forced or _looks_like_credential(text):
                excluded.append((kind, path))
                continue
            redacted = _redact(text)
            if not redacted:
                excluded.append((kind, path))
                continue
            projected.append(
                ProjectedMaterial(
                    material_kind=kind,
                    field_path=path,
                    projected_text=redacted,
                    digest=f"sha256:projected-{kind.value}-{len(redacted)}",
                )
            )
        if not projected:
            return Projection(
                status=ProjectionStatus.PARTIAL,
                excluded=tuple(excluded) or ((MaterialKind.PROJECT_CONTEXT, "material"),),
            )
        projection_digest = (
            "sha256:projection-"
            + "-".join(sorted(item.material_kind.value for item in projected))
        )
        return Projection(
            status=ProjectionStatus.PARTIAL if excluded else ProjectionStatus.COMPLETE,
            projected=tuple(projected),
            excluded=tuple(excluded),
            projection_digest=projection_digest,
            policy_revision=1,
        )


class MemoryCredentialResolver:
    """内存凭据解析：**只有状态，没有正文**。

    `available_purposes` 之外的用途返回 `PURPOSE_MISMATCH` —— 对应需求
    "模型/HTTP/数据库核验/GitHub 凭据分用途，不能只允许模型密钥"。
    """

    def __init__(
        self,
        *,
        available_purposes: tuple[str, ...] = ("model",),
        detail: str = "",
    ) -> None:
        self._available = set(available_purposes)
        self._detail = detail

    def resolve(self, *, purpose: str) -> CredentialResolution:
        if purpose in self._available:
            return CredentialResolution(
                status=CredentialStatus.AVAILABLE, purpose=purpose
            )
        if not self._available:
            return CredentialResolution(
                status=CredentialStatus.MISSING,
                purpose=purpose,
                detail=self._detail or "no credential reference is registered",
            )
        return CredentialResolution(
            status=CredentialStatus.PURPOSE_MISMATCH,
            purpose=purpose,
            detail=self._detail or "no credential is registered for this purpose",
        )


class MemoryModelCaller:
    """内存模型调用者：回草稿文本或归类错误。

    **不判断业务通过**，也不重试、不切换供应方（那是编排与用户的决定）。
    """

    def __init__(
        self,
        *,
        draft_text: str = "draft: proposed checks",
        error_kind: str | None = None,
        error_detail: str = "",
        provider_request_id: str | None = "provider-request-1",
    ) -> None:
        self._draft_text = draft_text
        self._error_kind = error_kind
        self._error_detail = error_detail
        self._provider_request_id = provider_request_id
        self.calls: list[ModelCall] = []

    def call(self, request: ModelCall) -> ModelCallResult:
        self.calls.append(request)
        if self._error_kind is not None:
            return ModelCallResult(
                status=ModelCallStatus.FAILED,
                error_kind=self._error_kind,
                error_detail=self._error_detail,
                provider_request_id=self._provider_request_id,
            )
        return ModelCallResult(
            status=ModelCallStatus.OK,
            draft_text=self._draft_text,
            provider_request_id=self._provider_request_id,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


__all__ = [
    "MemoryCredentialResolver",
    "MemoryModelCaller",
    "MemoryProjector",
]
