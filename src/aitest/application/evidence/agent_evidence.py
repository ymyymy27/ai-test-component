"""Record structured Agent tool calls/evaluations without trusting prose."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from aitest.application.execution.commands import ExecutionCommands, InvalidExecutionCommand
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import _trace_node_fact, _verification_fact
from aitest.contracts.agent_evidence import AgentEvidenceInput
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.evidence.agent_claims import (
    AgentAdapter,
    AgentEvaluation,
    AgentToolCall,
)
from aitest.domain.evidence.evidence import (
    TraceNode,
    TraceNodeState,
    TraceNodeType,
    TruthClass,
    VerificationObservation,
)


class _EvidenceBoundary:
    def __init__(self, facts: ExecutionFacts, attempt_id: str) -> None:
        self._known = {
            item.evidence_id
            for item in facts.evidence_refs
            if item.attempt_id == attempt_id
        }

    def exists(self, evidence_ref: str) -> bool:
        return evidence_ref in self._known


class SavedAgentEvidence:
    def __init__(
        self,
        coordinator: ExecutionCommitCoordinator,
        *,
        workspace_id: str,
        instance_id: str,
    ) -> None:
        self.coordinator = coordinator
        self.workspace_id = workspace_id
        self.instance_id = instance_id

    def apply(self, command: Command) -> Mapping[str, object]:
        project, _intent = ExecutionCommands._identity(command)
        if (
            command.action != "record_agent_evidence"
            or command.target != "agent-evidence"
            or set(command.parameters) != {"document"}
        ):
            raise InvalidExecutionCommand(
                "Agent evidence requires only its versioned document"
            )
        document = AgentEvidenceInput.model_validate(command.parameters["document"])
        current = self.coordinator.read_runtime_revision_facts(
            project_id=project, run_id=document.run_id
        )
        if current.current_attempt_by_step.get(document.step_id) != document.attempt_id or not any(
            item.attempt_id == document.attempt_id for item in current.attempts
        ):
            raise InvalidExecutionCommand("Agent evidence does not match the current attempt")
        boundary = _EvidenceBoundary(current, document.attempt_id)
        adapter = AgentAdapter(evidence_validator=boundary)
        for call in document.tool_calls:
            adapter.record_tool_call(
                AgentToolCall(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    arguments_digest=call.arguments_digest,
                    result_digest=call.result_digest,
                    success=call.success,
                    evidence_refs=call.evidence_refs,
                )
            )
        verifications = []
        for index, evaluation in enumerate(document.evaluations, start=1):
            verifications.append(
                adapter.evaluate(
                    AgentEvaluation(
                        verification_id=(
                            f"agent:{document.attempt_id}:{index}:{uuid4().hex[:12]}"
                        ),
                        verification_of=document.attempt_id,
                        business_object_id=evaluation.business_object_id,
                        observation=VerificationObservation(evaluation.observation),
                        tool_call_ids=evaluation.tool_call_ids,
                        evidence_refs=evaluation.evidence_refs,
                        query_method="agent_tool_replay",
                    )
                )
            )
        unit = self.coordinator._uow
        unit.begin("agent-evidence-" + uuid4().hex, project)
        try:
            latest = self.coordinator.read_runtime_revision_facts(
                project_id=project, run_id=document.run_id
            )
            if latest != current:
                raise InvalidExecutionCommand("Agent evidence basis changed before publication")
            facts = tuple(_verification_fact(item) for item in verifications)
            traces = tuple(
                TraceNode(
                    trace_node_id=f"trace:{document.attempt_id}:{call.call_id}",
                    run_id=document.run_id,
                    actual_node_id=call.call_id,
                    node_type=TraceNodeType.EXTERNAL_CALL,
                    state=(
                        TraceNodeState.SUCCEEDED
                        if call.success
                        else TraceNodeState.FAILED
                    ),
                    attempt_id=document.attempt_id,
                    explicit_relation_ref=(
                        call.evidence_refs[0] if call.evidence_refs else None
                    ),
                    truth_class=TruthClass.UNKNOWN,
                    gap_ids=() if call.evidence_refs else ("agent_evidence_missing",),
                    navigation_path=call.tool_name,
                )
                for call in document.tool_calls
            )
            trace_facts = tuple(_trace_node_fact(item) for item in traces)
            updated = latest.model_copy(
                update={
                    "verifications": (*latest.verifications, *facts),
                    "trace_nodes": (*latest.trace_nodes, *trace_facts),
                }
            )
            self.coordinator._stage_snapshot(updated)
            unit.commit()
        except BaseException:
            unit.rollback()
            raise
        return {
            "run_id": document.run_id,
            "attempt_id": document.attempt_id,
            "verification_ids": tuple(item.verification_id for item in verifications),
            "gap_ids": tuple(
                gap for item in verifications for gap in item.gap_ids
            ),
            "source_instance_id": self.instance_id,
        }


__all__ = ["SavedAgentEvidence"]
