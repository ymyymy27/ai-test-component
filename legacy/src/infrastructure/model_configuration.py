"""Read bounded host model routing; credentials remain in the OS secret provider."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from aitest.domain.json_material import decode_json
from aitest.infrastructure.python_environment import RegisteredPythonEnvironmentResolver


def host_model_configuration() -> tuple[str, tuple[str, str]] | None:
    """Freeze an explicitly selected host configuration at core startup."""
    name = os.environ.get("AITEST_MODEL_CONFIGURATION")
    if not name:
        return None
    try:
        path = Path(name)
        if not path.is_absolute():
            raise ValueError
        RegisteredPythonEnvironmentResolver._no_links(path)
        with path.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError
        data = decode_json(raw)
        return validate_model_configuration(data)
    except (OSError, ValueError, TypeError):
        raise ValueError("宿主模型配置无效；仅接受 HTTPS 端点与凭据引用") from None


def validate_model_configuration(data: object) -> tuple[str, tuple[str, str]]:
    """Validate public routing before any configuration bytes are saved."""
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "endpoint", "credential_reference"}
        or data["schema_version"] != "aitest.model-provider/1"
    ):
        raise ValueError
    endpoint, reference = data["endpoint"], data["credential_reference"]
    if not isinstance(endpoint, str) or not isinstance(reference, str):
        raise ValueError
    parts = urlsplit(endpoint)
    if (
        len(endpoint) > 2048 or endpoint != endpoint.strip()
        or any(ord(c) <= 32 or ord(c) == 127 for c in endpoint)
        or parts.scheme != "https" or not parts.hostname
        or parts.username is not None or parts.password is not None
        or parts.query or parts.fragment or "\\" in endpoint
        or not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", reference)
    ):
        raise ValueError
    _ = parts.port
    return endpoint.rstrip("/"), ("model", reference)
