"""模型响应和错误归一，不重试或决定通过。

通过可替换的传输层（默认 urllib HTTPS）发送 OpenAI 兼容的
``/chat/completions`` 请求：

- 请求体只含**已投影**材料，凭据只放在 Authorization 头；
- 归一认证/限流/输入限制/传输/结构错误为 error_kind 字符串；
- 成功只返回草稿文本与供应方请求号，不产生任何业务结论；
- 不重试、不切换供应方。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from aitest.application.ports import (
    ModelCall,
    ModelCallResult,
    ModelCallStatus,
)
from aitest.contracts.secrets import ResolvedSecret


class ModelAdapterError(RuntimeError):
    """请求构造不合法（如缺少凭据）。"""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes


class HttpTransport(Protocol):
    def post(
        self, url: str, *, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse: ...


class UrllibTransport:
    """urllib 传输；网络错误以原始异常抛出，由上层归类。"""

    def post(
        self, url: str, *, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=timeout_seconds
            ) as response:
                return HttpResponse(status=response.status, body=response.read())
        except urllib.error.HTTPError as error:
            return HttpResponse(status=error.code, body=error.read())


def _joined_prompt(request: ModelCall) -> str:
    return "\n\n".join(item.projected_text for item in request.projected)


class HttpModelProvider:
    """ModelProvider 的 OpenAI 兼容 HTTPS 实现。"""

    _CHAT_PATH = "/chat/completions"

    def __init__(
        self,
        endpoint: str,
        *,
        transport: HttpTransport | None = None,
        secret: ResolvedSecret | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._transport = transport or UrllibTransport()
        self._secret = secret

    def call(self, request: ModelCall) -> ModelCallResult:
        if self._secret is None:
            raise ModelAdapterError("模型请求缺少已解析凭据")
        url = self._endpoint + self._CHAT_PATH
        body = json.dumps(
            {
                "model": request.model_id,
                "messages": [{"role": "user", "content": _joined_prompt(request)}],
            }
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._secret.reveal()}",
        }
        try:
            response = self._transport.post(
                url,
                headers=headers,
                body=body,
                timeout_seconds=request.timeout_seconds,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return ModelCallResult(
                status=ModelCallStatus.FAILED,
                error_kind="connectivity",
                error_detail=str(error),
            )

        if response.status in {401, 403}:
            return self._failed("auth", response)
        if response.status == 429:
            return self._failed("rate_limit", response)
        if response.status in {400, 413, 422}:
            return self._failed("input_limit", response)
        if response.status >= 400:
            return self._failed("provider_error", response)

        try:
            payload = json.loads(response.body.decode("utf-8"))
            draft = payload["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, UnicodeDecodeError) as error:
            return ModelCallResult(
                status=ModelCallStatus.FAILED,
                error_kind="structure",
                error_detail=str(error),
            )
        if not str(draft).strip():
            return ModelCallResult(
                status=ModelCallStatus.FAILED,
                error_kind="structure",
                error_detail="empty draft",
            )
        return ModelCallResult(
            status=ModelCallStatus.OK,
            draft_text=str(draft),
            provider_request_id=str(payload["id"]) if payload.get("id") else None,
        )

    @staticmethod
    def _failed(kind: str, response: HttpResponse) -> ModelCallResult:
        detail = response.body[:512].decode("utf-8", errors="replace")
        return ModelCallResult(
            status=ModelCallStatus.FAILED,
            error_kind=kind,
            error_detail=detail,
        )


__all__ = [
    "HttpModelProvider",
    "HttpResponse",
    "HttpTransport",
    "ModelAdapterError",
    "UrllibTransport",
]
