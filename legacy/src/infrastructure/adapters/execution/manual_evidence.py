"""Manual operation capture with explicit provenance and attachments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aitest.domain.evidence.evidence import (
    EvidenceCaptureSource,
    TraceNode,
    TraceNodeState,
    TraceNodeType,
)


class ManualOperationType(StrEnum):
    BUTTON = "button"
    PAGINATION = "pagination"
    NAVIGATION = "navigation"
    FORM = "form"
    SCROLL = "scroll"
    MOUSE = "mouse"
    ERROR_PROMPT = "error_prompt"
    DATA_DISPLAY = "data_display"


@dataclass(frozen=True, slots=True)
class ManualStep:
    step_id: str
    operation_type: ManualOperationType
    sequence: int
    target_ref: str
    observed_result: str
    business_object_id: str
    input_summary: str = ""
    attachment_refs: tuple[str, ...] = ()
    captured_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "step_id",
            "target_ref",
            "observed_result",
            "business_object_id",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.sequence < 1:
            raise ValueError("manual step sequence must be positive")
        if any(not value.strip() for value in self.attachment_refs):
            raise ValueError("attachment refs must not be empty")


@dataclass(frozen=True, slots=True)
class ManualEvidenceRecord:
    step: ManualStep
    capture_source: EvidenceCaptureSource = EvidenceCaptureSource.MANUAL


class ManualEvidenceAdapter:
    """Record the user's actual step; it does not infer success from prose."""

    def record(self, step: ManualStep) -> ManualEvidenceRecord:
        return ManualEvidenceRecord(step=step)

    def to_trace_node(
        self,
        record: ManualEvidenceRecord,
        *,
        run_id: str,
        state: TraceNodeState = TraceNodeState.UNKNOWN,
    ) -> TraceNode:
        step = record.step
        return TraceNode(
            trace_node_id=f"manual:{step.step_id}",
            run_id=run_id,
            actual_node_id=step.target_ref,
            node_type=TraceNodeType.MANUAL_STEP,
            state=state,
            actual_parent_node_id=step.business_object_id,
            explicit_relation_ref=step.business_object_id,
            assertion_refs=(),
            gap_ids=(),
            navigation_path=step.target_ref,
        )


__all__ = [
    "ManualEvidenceAdapter",
    "ManualEvidenceRecord",
    "ManualOperationType",
    "ManualStep",
]
