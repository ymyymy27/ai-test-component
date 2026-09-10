"""Defect lifecycle models."""

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class DefectStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    READY_FOR_RETEST = "READY_FOR_RETEST"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class Defect:
    defect_id: str
    title: str
    severity: Severity
    status: DefectStatus = DefectStatus.DRAFT

