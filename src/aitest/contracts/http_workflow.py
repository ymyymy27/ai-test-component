"""Versioned HTTP workflow execution input (phase 1, `aitest.http-workflow/1.0`).

The workflow is stored as one JSON document in the frozen Case inputs of an
HTTP step. It is execution material, not an assertion basis: assertions live in
the same document so the domain can evaluate them from actual responses.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import Field, model_validator

from aitest.contracts.prepared_run import ContractModel

_HTTP_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
_SECRET_PLACEHOLDER = re.compile(r"\{\{secret:([A-Za-z_][A-Za-z0-9_]*)\}\}")
_JSON_PATH_PART = re.compile(r"[^.\[\]]+(?:\[(?:0|[1-9][0-9]*)\])?\Z")
_PLACEHOLDER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class HttpWorkflowAssertion(ContractModel):
    assertion_id: str = Field(min_length=1)
    json_path: str
    operator: Literal["equals", "contains", "exists"] = "equals"
    expected: object = None

    @model_validator(mode="after")
    def _path_and_identity_are_exact(self) -> Self:
        if self.json_path and not all(
            _JSON_PATH_PART.fullmatch(part) for part in self.json_path.split(".")
        ):
            raise ValueError("HTTP assertion requires a complete dotted JSON path")
        if self.operator != "exists" and "expected" not in self.model_fields_set:
            raise ValueError("HTTP assertion requires an expected value unless it checks existence")
        return self


class HttpWorkflowRequest(ContractModel):
    request_id: str = Field(min_length=1)
    method: str = Field(min_length=1)
    url: str = Field(min_length=1)
    headers: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    body: str | None = None
    extract: dict[str, str] = Field(default_factory=dict)
    assertions: tuple[HttpWorkflowAssertion, ...] = Field(default_factory=tuple)
    timeout_seconds: float = Field(default=10.0, gt=0, le=3600)
    max_response_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)

    @model_validator(mode="after")
    def _request_fields_are_exact(self) -> Self:
        if not _HTTP_TOKEN.fullmatch(self.method):
            raise ValueError("HTTP method requires its complete token")
        if len(self.body or "") > 1024 * 1024:
            raise ValueError("HTTP body exceeds its explicit text budget")
        seen_headers: set[str] = set()
        for item in self.headers:
            if len(item) != 2:
                raise ValueError("HTTP headers require complete name/value pairs")
            name, value = item
            if (
                not _HTTP_TOKEN.fullmatch(name)
                or name.lower() in seen_headers
                or any(ord(character) == 127 or ord(character) < 32 and character != "\t"
                       for character in value)
            ):
                raise ValueError("HTTP header name/value is invalid or duplicated")
            seen_headers.add(name.lower())
        for name, path in self.extract.items():
            if not _PLACEHOLDER_NAME.fullmatch(name) or not path or not all(
                _JSON_PATH_PART.fullmatch(part) for part in path.split(".")
            ):
                raise ValueError("HTTP extraction requires a name and a complete JSON path")
        assertion_ids = {item.assertion_id for item in self.assertions}
        if len(assertion_ids) != len(self.assertions):
            raise ValueError("HTTP assertion identities must be unique")
        if not self.url.startswith(("http://", "https://", "{{")):
            raise ValueError("HTTP workflow URL must be an explicit http/https target")
        return self


class HttpWorkflowSpec(ContractModel):
    schema_version: Literal["aitest.http-workflow/1.0"] = "aitest.http-workflow/1.0"
    requests: tuple[HttpWorkflowRequest, ...] = Field(min_length=1)
    #: 占位名 -> SecretPort 引用；正文只保存引用，不保存凭据正文。
    secret_refs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _chain_is_resolvable(self) -> Self:
        available: set[str] = set()
        request_ids: set[str] = set()
        for request in self.requests:
            if request.request_id in request_ids:
                raise ValueError("HTTP workflow request identities must be unique")
            request_ids.add(request.request_id)
            for value in (
                request.url,
                request.body or "",
                *(value for _, value in request.headers),
            ):
                for name in _PLACEHOLDER.findall(value):
                    if name not in available:
                        raise ValueError(
                            f"HTTP workflow references an identifier before extraction: {name}"
                        )
            for name in request.extract:
                if name in available:
                    raise ValueError(
                        "HTTP workflow extraction names must be unique across the chain"
                    )
                available.add(name)
        for name, reference in self.secret_refs.items():
            if not _PLACEHOLDER_NAME.fullmatch(name) or not reference.strip():
                raise ValueError("HTTP secret references require a name and nonempty reference")
        for request in self.requests:
            for value in (
                request.url,
                request.body or "",
                *(value for _, value in request.headers),
            ):
                for name in _SECRET_PLACEHOLDER.findall(value):
                    if name not in self.secret_refs:
                        raise ValueError(
                            f"HTTP secret placeholder is not registered: {name}"
                        )
        return self


def parse_http_workflow(inputs: tuple[str, ...]) -> HttpWorkflowSpec:
    """Parse the single frozen JSON document held by an HTTP step's Case inputs."""

    if len(inputs) != 1 or not inputs[0].strip():
        raise ValueError("an HTTP step requires exactly one frozen workflow document")
    return HttpWorkflowSpec.model_validate_json(inputs[0])


__all__ = [
    "HttpWorkflowAssertion",
    "HttpWorkflowRequest",
    "HttpWorkflowSpec",
    "parse_http_workflow",
]
