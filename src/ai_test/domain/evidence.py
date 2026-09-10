"""Evidence, assertion, and authenticity models."""

from dataclasses import dataclass
from enum import StrEnum


class Authenticity(StrEnum):
    REAL = "REAL"
    MOCK = "MOCK"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class AssertionStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: str
    source: str
    sha256: str
    redacted: bool

