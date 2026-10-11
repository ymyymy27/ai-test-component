"""Streaming byte redaction applied before command output reaches the spool."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from aitest.infrastructure.security import UnsafeMaterialError

MAX_PENDING_BYTES = 1024 * 1024
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
    ("key_value", _KV_PATTERN, lambda match: match.group(1) + _REDACTED),
    ("bearer", _BEARER_PATTERN, lambda match: match.group(1) + _REDACTED),
    ("basic", _BASIC_PATTERN, lambda match: match.group(1) + _REDACTED),
    ("github_token", _GH_TOKEN_PATTERN, lambda match: _REDACTED),
    ("openai_key", _OPENAI_KEY_PATTERN, lambda match: _REDACTED),
    ("jwt", _JWT_PATTERN, lambda match: _REDACTED),
)


@dataclass(frozen=True, slots=True)
class RedactionStats:
    input_bytes: int
    output_bytes: int
    replacement_count: int
    replacement_categories: tuple[tuple[str, int], ...] = ()


class StreamingRedactor:
    """Redact complete lines while preserving secrets split across input chunks."""

    def __init__(
        self, secrets: Iterable[str] = (), *, max_pending_bytes: int = MAX_PENDING_BYTES,
    ) -> None:
        if type(max_pending_bytes) is not int or not 0 < max_pending_bytes <= MAX_PENDING_BYTES:
            raise ValueError("redaction pending budget must be a positive bounded integer")
        self._max_pending_bytes = max_pending_bytes
        encoded = {secret.encode("utf-8") for secret in secrets if secret}
        self._secrets = tuple(sorted(encoded, key=len, reverse=True))
        self._pending = bytearray()
        self._input_bytes = 0
        self._output_bytes = 0
        self._replacement_count = 0
        self._replacement_categories: dict[str, int] = {}
        self._closed = False

    def feed(self, data: bytes) -> bytes:
        if self._closed:
            raise RuntimeError("redactor is already closed")
        self._input_bytes += len(data)
        # Validate before any redaction stats change or emissions from this feed.
        offset = 0
        while offset < len(data):
            newline = data.find(b"\n", offset)
            end = len(data) if newline < 0 else newline + 1
            size = end - offset + (len(self._pending) if offset == 0 else 0)
            if size > self._max_pending_bytes:
                self._pending.clear()
                self._closed = True
                raise UnsafeMaterialError("command redaction unresolved line exceeds memory budget")
            offset = end
        output = bytearray()
        offset = 0
        while offset < len(data):
            newline = data.find(b"\n", offset)
            end = len(data) if newline < 0 else newline + 1
            self._pending.extend(data[offset:end])
            if newline >= 0:
                output.extend(self._redact(bytes(self._pending)))
                self._pending.clear()
            offset = end
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
            replacement_categories=tuple(sorted(self._replacement_categories.items())),
        )

    def _redact(self, data: bytes) -> bytes:
        redacted = data
        for secret in self._secrets:
            redacted, count = re.subn(re.escape(secret), _REDACTED, redacted)
            self._record_replacements("resolved_secret", count)
        for category, pattern, replacement in _DEFAULT_PATTERNS:
            redacted, count = pattern.subn(replacement, redacted)
            self._record_replacements(category, count)
        return redacted

    def _record_replacements(self, category: str, count: int) -> None:
        if not count:
            return
        self._replacement_count += count
        self._replacement_categories[category] = (
            self._replacement_categories.get(category, 0) + count
        )


def redact_bytes(data: bytes, secrets: Iterable[str] = ()) -> bytes:
    redactor = StreamingRedactor(secrets)
    return redactor.feed(data) + redactor.finish()


__all__ = ["RedactionStats", "StreamingRedactor", "redact_bytes"]
