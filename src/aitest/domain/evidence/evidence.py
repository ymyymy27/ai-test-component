"""Evidence provenance is independent of assertions and authenticity."""

from dataclasses import dataclass
from enum import StrEnum


class Authenticity(StrEnum):
    REAL = "REAL"
    MOCK = "MOCK"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    project_id: str
    run_id: str
    step_id: str
    attempt_id: str
    source_identity: str
    sha256: str
    source: str
    complete: bool
