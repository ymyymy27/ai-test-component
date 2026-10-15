"""Resolved actions and the one-use authorization state are separate facts."""

from dataclasses import dataclass
from enum import StrEnum

from aitest.domain.execution.runs import Attempt, AttemptState, ExecutionRequest
from aitest.domain.execution.sources import ExecutionSourceVerification


class AuthorizationState(StrEnum):
    UNUSED = "unused"
    OCCUPIED = "occupied"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class ResolvedExecutionAction:
    """Produced by a registered core resolver, before any external execution."""

    attempt: Attempt
    request: ExecutionRequest
    environment_content_identity: str
    source_content_identity: str
    source_verification: ExecutionSourceVerification | None = None

    def __post_init__(self) -> None:
        attempt, request = self.attempt, self.request
        authorization = request.authorization_ref
        source = self.source_verification
        if request.source_capture is not None and (
            source is None or request.source_capture != source.capture_plan
        ):
            raise ValueError("capture policy differs from original frozen source scope")
        if source is not None and (
            not isinstance(source, ExecutionSourceVerification)
            or source.project_id != request.project_id
            or source.plan_revision_ref != request.expected_plan_revision_ref
            or source.materialized_snapshot_ref != request.materialized_snapshot_ref
            or source.expected_source_content_digest != self.source_content_identity
            or source.observed_source_digest != self.source_content_identity
            or (
                source.raw_output_object is not None
                and source.raw_output_object.project_id != request.project_id
            )
        ):
            raise ValueError("resolved source observation has a different frozen execution scope")
        if (
            not self.environment_content_identity
            or not self.source_content_identity
            or attempt.state is not AttemptState.INTENT_RECORDED
            or attempt.execution_handle_ref is not None
            or attempt.intent_digest
            or attempt.runtime_source is not None
            or authorization.consumed_by_attempt_id is not None
            or attempt.authorization_ref != authorization
            or (
                attempt.attempt_id,
                attempt.run_id,
                attempt.step_id,
                attempt.intent_id,
                attempt.resolved_input_digest,
                attempt.source_binding_digest,
                attempt.side_effect_class,
                attempt.adapter_kind,
                attempt.timeout_ms,
                attempt.expected_plan_revision_ref,
            )
            != (
                request.attempt_id,
                request.run_id,
                request.step_id,
                request.intent_id,
                request.resolved_input_digest,
                request.source_binding_digest,
                request.side_effect_class,
                request.registered_entry.adapter_kind,
                request.timeout_ms,
                request.expected_plan_revision_ref,
            )
            or (
                authorization.intent_id,
                authorization.step_id,
                authorization.resolved_input_digest,
                authorization.step_revision_ref,
                authorization.plan_revision_ref,
            )
            != (
                request.intent_id,
                request.step_id,
                request.resolved_input_digest,
                attempt.step_revision_ref,
                attempt.expected_plan_revision_ref,
            )
        ):
            raise ValueError("resolved execution action has inconsistent or already used input")


def require_unused(state: AuthorizationState, occupied_by: str | None) -> None:
    if state is not AuthorizationState.UNUSED or occupied_by is not None:
        raise ValueError("original authorization is revoked or already occupied")
