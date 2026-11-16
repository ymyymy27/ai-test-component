"""Independent GET of one registered business object with an absolute cutoff."""

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlsplit

from aitest.contracts.redaction import SENSITIVE_KEYS
from aitest.contracts.secrets import ResolvedSecret
from aitest.domain.evidence.polling import MAX_QUERY_DEADLINE_MS
from aitest.domain.json_material import decode_json, require_json_text
from aitest.infrastructure.security import known_secrets, scrub_text

from .http import HttpRequestSpec
from .http_transport import exchange

_OBJECT_SLOT = "{business_object_id}"


@dataclass(frozen=True, slots=True)
class HttpBusinessQuerySpec:
    target_deployment_ref: str
    url_template: str
    object_id_field: str = "object_id"
    credential_reference: str | None = None
    max_response_bytes: int = 1024 * 1024
    single_query_timeout_ms: int = 10_000

    def __post_init__(self) -> None:
        for value in (self.target_deployment_ref, self.url_template, self.object_id_field):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("business query requires its registered target and object field")
            require_json_text(value)
        if self.credential_reference is not None and (
            not isinstance(self.credential_reference, str) or not self.credential_reference.strip()
        ):
            raise ValueError("business query credential requires a reference")
        if self.credential_reference is not None:
            require_json_text(self.credential_reference)
        target = urlsplit(self.url_template)
        if (
            self.url_template.count(_OBJECT_SLOT) != 1
            or _OBJECT_SLOT in target.netloc
            or any(char in self.url_template.replace(_OBJECT_SLOT, "") for char in "{}")
            or any(
                name.lower().replace("-", "_") in SENSITIVE_KEYS
                for name, _ in parse_qsl(target.query, keep_blank_values=True)
            )
            or scrub_text(self.url_template)[1]
            or type(self.max_response_bytes) is not int
            or not 0 < self.max_response_bytes <= 1024 * 1024
            or type(self.single_query_timeout_ms) is not int
            or not 0 < self.single_query_timeout_ms <= MAX_QUERY_DEADLINE_MS
        ):
            raise ValueError("business query target, slot or budget is unsafe")
        HttpRequestSpec(
            "independent-query-config",
            "GET",
            self.url_template.replace(_OBJECT_SLOT, "registered-object"),
        )
        if self.credential_reference is not None and target.scheme != "https":
            raise ValueError("credentialed business query requires HTTPS")


class HttpBusinessQueryReader:
    """DeadlineBusinessQueryPort; endpoint configuration belongs to trusted assembly."""

    def __init__(
        self, spec: HttpBusinessQuerySpec, *, secret: ResolvedSecret | None = None
    ) -> None:
        if not isinstance(spec, HttpBusinessQuerySpec):
            raise ValueError("business query requires a frozen registered spec")
        self._spec, self._secret = spec, secret
        self._headers()

    def _headers(self) -> tuple[tuple[str, str], ...]:
        spec, secret = self._spec, self._secret
        if spec.credential_reference is None:
            if secret is not None:
                raise ValueError("business query has no frozen credential reference")
            return (("Accept", "application/json"),)
        if (
            not isinstance(secret, ResolvedSecret)
            or secret.purpose != "http"
            or secret.reference != spec.credential_reference
        ):
            raise ValueError("business query credential scope differs")
        value = secret.reveal()
        if not isinstance(value, str) or not value.strip():
            raise ValueError("business query credential is unavailable")
        known_secrets().register(value)
        # Validate the complete header without retaining its value in a spec,
        # a request record, an exception detail, or any captured JSON.
        headers = (("Accept", "application/json"), ("Authorization", "Bearer " + value))
        try:
            HttpRequestSpec(
                "independent-query-auth",
                "GET",
                spec.url_template.replace(_OBJECT_SLOT, "registered-object"),
                headers=headers,
            )
        except ValueError:
            raise ValueError("business query credential is not wire safe") from None
        return headers

    def read_business_object(
        self,
        *,
        business_object_id: str,
        target_deployment_ref: str,
    ) -> Mapping[str, object] | None:
        return self.read_business_object_before(
            business_object_id=business_object_id,
            target_deployment_ref=target_deployment_ref,
            deadline_monotonic=time.monotonic() + self._spec.single_query_timeout_ms / 1000,
        )

    def read_business_object_before(
        self,
        *,
        business_object_id: str,
        target_deployment_ref: str,
        deadline_monotonic: float,
    ) -> Mapping[str, object] | None:
        try:
            valid_deadline = type(deadline_monotonic) in (int, float) and math.isfinite(
                deadline_monotonic
            )
        except OverflowError:
            valid_deadline = False
        if (
            not isinstance(business_object_id, str)
            or not business_object_id.strip()
            or business_object_id in (".", "..")
            or scrub_text(business_object_id)[1]
            or target_deployment_ref != self._spec.target_deployment_ref
            or not valid_deadline
        ):
            raise ValueError("business query object/deployment/cutoff is invalid")
        require_json_text(business_object_id)
        if len(business_object_id.encode("utf-8")) > 4096:
            raise ValueError("business query object identity exceeds its budget")
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        if remaining > MAX_QUERY_DEADLINE_MS / 1000:
            raise ValueError("business query cutoff exceeds its frozen maximum")
        url = self._spec.url_template.replace(_OBJECT_SLOT, quote(business_object_id, safe=""))
        headers = self._headers()
        # All URI/header validation precedes DNS and network I/O.
        HttpRequestSpec("independent-query", "GET", url, headers=headers)
        result = exchange(
            method="GET",
            url=url,
            headers=headers,
            body=None,
            expires=deadline_monotonic,
            max_response_bytes=self._spec.max_response_bytes,
        )
        if result.error_class == "timeout":
            raise TimeoutError
        if not result.body_complete or result.error_class not in (None, "http_status"):
            raise OSError("independent_query_transport")
        if result.status == 404:
            return None
        if result.status != 200 or result.error_class is not None:
            raise OSError("independent_query_status")
        content_types = [
            value.split(";", 1)[0].strip().lower()
            for name, value in result.headers
            if name.lower() == "content-type"
        ]
        if len(content_types) != 1 or content_types[0] != "application/json":
            raise ValueError("business query requires one JSON content type")
        fields = decode_json(result.body)
        if (
            not isinstance(fields, dict)
            or fields.get(self._spec.object_id_field) != business_object_id
        ):
            raise ValueError("business query response belongs to another object")
        return fields
