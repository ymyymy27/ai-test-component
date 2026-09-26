"""Streaming byte redaction applied before command output reaches the spool."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_REDACTED = b"[REDACTED]"
_KV_PATTERN = re.compile(
    rb"(?i)(\b(?:password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|"
    rb"secret|client[_-]?secret|api[_-]?key|apikey)\b\s*[:=]\s*)"
    rb"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_PATTERN = re.compile(rb"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9\-._~+/]+=*)")
_BASIC_PATTERN = re.compile(rb"(?i)(authorization\s*:\s*basic\s+)([A-Za-z0-9+/]+=*)")
_GH_TOKEN_PATTERN = re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_OPENAI_KEY_PATTERN = re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b")
_JWT_PATTERN = re.compile(rb"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_DEFAULT_PATTERNS = (
    (_KV_PATTERN, lambda match: match.group(1) + _REDACTED),
    (_BEARER_PATTERN, lambda match: match.group(1) + _REDACTED),
    (_BASIC_PATTERN, lambda match: match.group(1) + _REDACTED),
    (_GH_TOKEN_PATTERN, lambda match: _REDACTED),
    (_OPENAI_KEY_PATTERN, lambda match: _REDACTED),
    (_JWT_PATTERN, lambda match: _REDACTED),
)


@dataclass(frozen=True, slots=True)
class RedactionStats:
    input_bytes: int
    output_bytes: int
    replacement_count: int


class StreamingRedactor:
    """Redact complete lines while preserving secrets split across input chunks."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        encoded = {secret.encode("utf-8") for secret in secrets if secret}
        self._secrets = tuple(sorted(encoded, key=len, reverse=True))
        self._pending = bytearray()
        self._input_bytes = 0
        self._output_bytes = 0
        self._replacement_count = 0
        self._closed = False

    def feed(self, data: bytes) -> bytes:
        if self._closed:
            raise RuntimeError("redactor is already closed")
        self._input_bytes += len(data)
        self._pending.extend(data)
        output = bytearray()
        while True:
            newline = self._pending.find(b"\n")
            if newline < 0:
                break
            line = bytes(self._pending[: newline + 1])
            del self._pending[: newline + 1]
            output.extend(self._redact(line))
        self._output_bytes += len(output)
        return bytes(output)

    def finish(self) -> bytes:
        if self._closed:
            raise RuntimeError("redactor is already closed")
        self._closed = True
        output = self._redact(bytes(self._pending))
        self._pending.clear()
        self._output_bytes += len(output)
        return output

    @property
    def stats(self) -> RedactionStats:
        return RedactionStats(
            input_bytes=self._input_bytes,
            output_bytes=self._output_bytes,
            replacement_count=self._replacement_count,
        )

    def _redact(self, data: bytes) -> bytes:
        redacted = data
        for secret in self._secrets:
            redacted, count = re.subn(re.escape(secret), _REDACTED, redacted)
            self._replacement_count += count
        for pattern, replacement in _DEFAULT_PATTERNS:
            redacted, count = pattern.subn(replacement, redacted)
            self._replacement_count += count
        return redacted


def redact_bytes(data: bytes, secrets: Iterable[str] = ()) -> bytes:
    redactor = StreamingRedactor(secrets)
    return redactor.feed(data) + redactor.finish()


__all__ = ["RedactionStats", "StreamingRedactor", "redact_bytes"]
