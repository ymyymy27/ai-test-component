"""AB-001 §8.16.3 第 3 条候选甲：SecretPort → B 侧 CredentialResolver 窄适配。

B 的编排（`request_model_draft`）只传递**用途**、只消费**状态**，从不提供
凭据引用、也拿不到正文；A 的 :class:`SecretPort.resolve` 要求显式
``reference`` 并返回受控的 ``ResolvedSecret``。本适配器在**装配处**收敛两边
形状：

- ``reference`` 由装配方按用途配置（``references: purpose → reference``），
  B 侧无法选择或更换引用；
- 返回值只有 ``CredentialResolution``（状态 + 用途 + 说明），**类型层面没有
  正文**——``ResolvedSecret`` 在本模块内即取即弃，不离开适配器；
- 解析失败按 ``MISSING`` 上报、未配置的用途按 ``PURPOSE_MISMATCH`` 上报，
  不静默回退到其他用途或其他凭据。
"""

from __future__ import annotations

from collections.abc import Mapping

from aitest.application.ports import (
    CredentialResolution,
    CredentialStatus,
    SecretPort,
)


class PurposeBoundCredentialResolver:
    """按用途绑定凭据引用的窄解析器；实现 B 的 ``CredentialResolver`` 协议形状。"""

    def __init__(
        self,
        secret_port: SecretPort,
        references: Mapping[str, str],
    ) -> None:
        self._secret_port = secret_port
        self._references = dict(references)

    def resolve(self, *, purpose: str) -> CredentialResolution:
        reference = self._references.get(purpose)
        if reference is None:
            # 未配置的用途不是"凭据缺失"：调用方请求了本装配未授权的用途。
            return CredentialResolution(
                status=CredentialStatus.PURPOSE_MISMATCH,
                purpose=purpose,
                detail=f"no credential reference configured for purpose: {purpose}",
            )
        try:
            # ResolvedSecret 在本调用内即取即弃：它的存在只证明"可解析"，
            # 正文不返回、不缓存、不进日志。
            self._secret_port.resolve(reference, purpose=purpose)
        except Exception as error:
            return CredentialResolution(
                status=CredentialStatus.MISSING,
                purpose=purpose,
                detail=f"{type(error).__name__}: {error}",
            )
        return CredentialResolution(
            status=CredentialStatus.AVAILABLE,
            purpose=purpose,
        )


__all__ = ["PurposeBoundCredentialResolver"]
