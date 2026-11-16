"""Shared result types for the lightweight runner layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RunnerStatus = Literal["passed", "failed", "blocked", "error"]


@dataclass(slots=True)
class RunnerResult:
    """Outcome of one command, suite parse, or HTTP sequence."""

    status: RunnerStatus
    exit_code: int | None = None
    stdout: bytes = b""
    stderr: bytes = b""
    artifacts: list[str] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
