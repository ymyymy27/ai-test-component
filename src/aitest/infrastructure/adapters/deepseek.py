"""可配置提供方及模型，不硬编码型号。

DeepSeek 作为一期默认提供方配置：仅冻结经实测/显式传入的端点与模型，
响应为 OpenAI 兼容结构，归一逻辑复用 ``model.HttpModelProvider``。
"""

from __future__ import annotations

from dataclasses import dataclass

from aitest.application.ports import (
    ModelCall,
    ModelCallResult,
)
from aitest.contracts.secrets import ResolvedSecret
from aitest.infrastructure.adapters.model import (
    HttpModelProvider,
    HttpTransport,
)

DEFAULT_BASE_ADDRESS = "https://api.deepseek.com"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """冻结供应方、地址、模型、凭据引用、输出 Schema 与参数。"""

    provider: str
    base_address: str
    model_id: str
    secret_reference: str
    output_schema: str = "openai.chat-completions"
    parameters: tuple[tuple[str, str], ...] = ()


class DeepSeekModelProvider:
    """默认 DeepSeek 端点；模型 id 必须显式提供。"""

    def __init__(
        self,
        model_id: str,
        *,
        secret: ResolvedSecret,
        base_address: str = DEFAULT_BASE_ADDRESS,
        transport: HttpTransport | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id 必须显式提供，不硬编码默认型号")
        self._delegate = HttpModelProvider(
            base_address, transport=transport, secret=secret
        )
        self._model_id = model_id
        self._base_address = base_address.rstrip("/")

    def call(self, request: ModelCall) -> ModelCallResult:
        return self._delegate.call(request)

    @property
    def profile(self) -> ModelProfile:
        return ModelProfile(
            provider="deepseek",
            base_address=self._base_address,
            model_id=self._model_id,
            secret_reference="model/deepseek",
        )


__all__ = [
    "DeepSeekModelProvider",
    "DEFAULT_BASE_ADDRESS",
    "ModelProfile",
]
