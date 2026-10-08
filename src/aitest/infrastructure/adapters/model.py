"""模型响应和错误归一，不重试或决定通过。

通过可替换的传输层（默认有界直接 HTTP）发送 OpenAI 兼容的
``/chat/completions`` 请求：

- 请求体只含**已投影**材料，凭据只放在 Authorization 头；
- 归一认证/限流/输入限制/传输/结构错误为 error_kind 字符串；
- 成功只返回草稿文本与供应方请求号，不产生任何业务结论；
- 不重试、不切换供应方。
"""

from __future__ import annotations

import json
import time
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aitest.application.ports import (
    CredentialResolution,
    CredentialStatus,
    ModelCall,
    ModelCallResult,
    ModelCallStatus,
)
from aitest.contracts.redaction import scrub_secret_text
from aitest.contracts.secrets import ResolvedSecret
from aitest.domain.json_material import decode_json
from aitest.infrastructure.adapters.execution.http import HttpRequestSpec
from aitest.infrastructure.adapters.execution.http_transport import exchange
from aitest.infrastructure.security import (
    KnownSecretRegistry,
    UnsafeMaterialError,
    guard_value,
    scrub_text,
)

_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ModelAdapterError(RuntimeError):
    """请求构造不合法（如缺少凭据）。"""


class ModelCredentialResolver:
    """按用途报告已配置凭据状态，B 端口不获得正文。"""

    def __init__(self, secret: ResolvedSecret) -> None:
        self._secret = secret

    def resolve(self, *, purpose: str) -> CredentialResolution:
        if self._secret.purpose != purpose:
            return CredentialResolution(
                status=CredentialStatus.PURPOSE_MISMATCH,
                purpose=purpose,
                detail="purpose mismatch",
            )
        if not self._secret.reveal():
            return CredentialResolution(
                status=CredentialStatus.MISSING,
                purpose=purpose,
                detail="credential unavailable",
            )
        return CredentialResolution(status=CredentialStatus.AVAILABLE, purpose=purpose)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    body_complete: bool = True
    error_class: str | None = None


class HttpTransport(Protocol):
    def post(
        self, url: str, *, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse: ...


class UrllibTransport:
    """兼容既有装配名；直接 POST，实际总截止且完整/有界读取。"""

    def __init__(self, *, max_response_bytes: int = _MAX_RESPONSE_BYTES) -> None:
        # Reuse the transport's one wire/budget validator, including actual scalar types.
        HttpRequestSpec(
            "model-budget", "POST", "https://example.invalid",
            max_response_bytes=max_response_bytes,
        )
        if max_response_bytes > _MAX_RESPONSE_BYTES:
            raise ValueError("model response budget cannot exceed 4MiB")
        self._max_response_bytes = max_response_bytes

    def post(
        self, url: str, *, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse:
        request = HttpRequestSpec(
            "model-request", "POST", url,
            headers=tuple(headers.items()), body=body, timeout_seconds=timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        result = exchange(
            method=request.method, url=request.url, headers=request.headers, body=request.body,
            expires=time.monotonic() + request.timeout_seconds,
            max_response_bytes=request.max_response_bytes,
        )
        # Complete HTTP error/redirect responses keep their existing status classification.
        error_class = result.error_class
        if result.body_complete and error_class in {"http_status", "redirect_blocked"}:
            error_class = None
        return HttpResponse(result.status or 0, result.body, result.body_complete, error_class)


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
        on_result: Callable[[ModelCallResult], None] | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._transport = transport or UrllibTransport()
        self._secret = secret
        self._secret_reference = secret.reference if secret is not None else None
        self._on_result = on_result
        self._registry = KnownSecretRegistry()
        if secret is not None:
            self._registry.register(secret.reveal())

    def call(self, request: ModelCall) -> ModelCallResult:
        result = self._call(request)
        if self._on_result is not None:
            self._on_result(result)
        return result

    def _call(self, request: ModelCall) -> ModelCallResult:
        if self._secret is None:
            raise ModelAdapterError("模型请求缺少已解析凭据")
        # 逐次证明：实际请求目标必须与本次调用策略确认的 endpoint_address 相同，
        # 且必须等于构造时锁定的提供方端点；不一致绝不发出请求。
        confirmed_endpoint = request.endpoint_address.rstrip("/")
        if confirmed_endpoint != self._endpoint:
            return ModelCallResult(
                status=ModelCallStatus.FAILED,
                error_kind="endpoint_mismatch",
                error_detail=("策略确认端点与已配置提供方端点不一致，已拒绝发送请求"),
            )
        if (
            self._secret.purpose != "model"
            or not self._secret_reference
            or self._secret.reference != self._secret_reference
            or not self._secret.reveal()
        ):
            return ModelCallResult(
                status=ModelCallStatus.FAILED, error_kind="auth",
                error_detail="模型凭据用途、引用或可用状态无法核对",
            )
        self._registry.register(self._secret.reveal())
        url = confirmed_endpoint + self._CHAT_PATH
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
            HttpRequestSpec(
                "model-request", "POST", url, headers=tuple(headers.items()), body=body,
                timeout_seconds=request.timeout_seconds, max_response_bytes=_MAX_RESPONSE_BYTES,
            )
        except (ValueError, UnicodeError):
            return ModelCallResult(
                status=ModelCallStatus.FAILED, error_kind="request_invalid",
                error_detail="模型请求目标、头字段或时限不合法，已拒绝发送",
            )
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
                error_detail=self._safe_detail(str(error)),
            )

        if (
            type(response.status) is not int
            or not isinstance(response.body, bytes)
            or type(response.body_complete) is not bool
        ):
            return ModelCallResult(
                status=ModelCallStatus.FAILED, error_kind="structure",
                error_detail="模型传输未提供准确响应事实",
            )
        if not response.body_complete or response.error_class is not None:
            kind = "structure"
            if response.error_class in {"timeout", "network"}:
                kind = "connectivity"
            elif response.error_class == "response_too_large":
                kind = "response_limit"
            return ModelCallResult(
                status=ModelCallStatus.FAILED, error_kind=kind,
                error_detail="模型响应未在预算和总截止内完整取得",
            )
        if not 100 <= response.status <= 599:
            return ModelCallResult(
                status=ModelCallStatus.FAILED, error_kind="structure",
                error_detail="模型响应状态不是有效HTTP状态码",
            )
        if len(response.body) > _MAX_RESPONSE_BYTES:
            return ModelCallResult(
                status=ModelCallStatus.FAILED, error_kind="response_limit",
                error_detail="模型响应超出正文预算",
            )
        if response.status in {401, 403}:
            return self._failed("auth", response)
        if response.status == 429:
            return self._failed("rate_limit", response)
        if 300 <= response.status < 400:
            # 传输层不自动跟随重定向，Authorization 不会转发到未确认的目标域。
            return self._failed("unconfirmed_redirect", response)
        if response.status in {400, 413, 422}:
            return self._failed("input_limit", response)
        if response.status >= 400:
            return self._failed("provider_error", response)
        if response.status != 200:
            return self._failed("provider_error", response)

        try:
            payload = decode_json(response.body)
        except ValueError:
            return ModelCallResult(
                status=ModelCallStatus.FAILED,
                error_kind="structure",
                error_detail="成功状态码但正文不是合法 JSON",
            )
        draft = self._extract_draft(payload)
        if draft is None:
            return ModelCallResult(
                status=ModelCallStatus.FAILED,
                error_kind="structure",
                error_detail="成功正文缺少 choices[0].message.content 字符串",
            )
        provider_id = payload.get("id") if isinstance(payload, dict) else None
        if isinstance(payload, dict) and "id" in payload and (
            not isinstance(provider_id, str) or not provider_id.strip()
        ):
            return ModelCallResult(
                status=ModelCallStatus.FAILED, error_kind="structure",
                error_detail="供应方请求号不是非空字符串",
            )
        return ModelCallResult(
            status=ModelCallStatus.OK,
            draft_text=self._safe_detail(draft, limit=None),
            provider_request_id=self._safe_detail(provider_id) if provider_id else None,
        )

    @staticmethod
    def _extract_draft(payload: object) -> str | None:
        """严格校验成功正文结构；任何层级类型不符都视为结构错误。"""
        if not isinstance(payload, dict):
            return None
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        return content

    def _failed(self, kind: str, response: HttpResponse) -> ModelCallResult:
        try:
            value = decode_json(response.body)
            value, _ = guard_value(value, self._registry)
            value, _ = guard_value(value)
            detail = self._safe_detail(json.dumps(value, ensure_ascii=False))
        except (ValueError, RecursionError, UnsafeMaterialError):
            detail = f"供应方返回HTTP {response.status}，正文无法安全解析"
        return ModelCallResult(
            status=ModelCallStatus.FAILED,
            error_kind=kind,
            error_detail=detail,
        )

    def _safe_detail(self, detail: str, *, limit: int | None = 512) -> str:
        """失败详情同样过凭据脱敏，防止供应方回显凭据正文。"""
        redacted, _changed = scrub_text(detail, self._registry)
        redacted, _changed = scrub_text(redacted)
        redacted, _changed = scrub_secret_text(redacted)
        return redacted if limit is None else redacted[:limit]


__all__ = [
    "HttpModelProvider",
    "HttpResponse",
    "HttpTransport",
    "ModelAdapterError",
    "UrllibTransport",
]
