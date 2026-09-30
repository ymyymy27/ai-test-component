"""Independent read-only verification contract.

核验通过独立只读路径重新打开同一业务对象，不使用被测路径的缓存句柄或
内存副本；结论只陈述实际重新读取到的事实。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VerificationState(StrEnum):
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    MISSING = "missing"
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class VerificationFact:
    """一次独立核验的结果。"""

    state: VerificationState
    method: str  # fresh_file_read
    sha256: str
    size: int
    detail: str = ""

    @property
    def verified(self) -> bool:
        return self.state is VerificationState.VERIFIED


__all__ = ["VerificationFact", "VerificationState"]
