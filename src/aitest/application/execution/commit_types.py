"""提交协调器共享的数据类型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import TypeAdapter

from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    ExecutionFacts,
)
from aitest.domain.evidence.evidence import (
    EvidenceRef,
)
from aitest.domain.execution.runs import (
    RecoveryRecord,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


class CheckpointPayloadCodec(Protocol):
    def to_payload(self, record: RecoveryRecord) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ExecutionCommitBatch:
    checkpoint: RecoveryRecord
    evidence_refs: tuple[EvidenceRef, ...]
    facts: ExecutionFacts
    expected_revisions: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionCommitResult:
    staged: tuple[object, ...]
    committed: object
    facts: ExecutionFacts | None = None
