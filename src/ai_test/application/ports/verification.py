from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VerificationResult:
    authentic: bool
    reasons: tuple[str, ...] = ()


class VerificationPort(Protocol):
    def verify(self, evidence_id: str) -> VerificationResult: ...

