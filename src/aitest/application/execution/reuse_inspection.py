"""Compare exact historic material with a current target; never grant reuse."""

import json
from collections.abc import Mapping
from dataclasses import asdict

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.frozen_plan import FrozenRunPlanReader
from aitest.application.execution.reuse_sources import CaseReuseSource, CaseReuseSourceReader
from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import acceptance_scope_to_payload
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import EvidenceObjectStore, RecordRepository, SpoolStore
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import (
    AttemptStateFact,
    CaptureCompletenessFact,
    ExecutionFacts,
)
from aitest.domain.execution.reuse import ReuseIdentity


class CaseReuseUnverified(ValueError):
    code = "CASE_REUSE_UNVERIFIED"


class CaseReuseInspection:
    def __init__(
        self, coordinator: ExecutionCommitCoordinator, records: RecordRepository,
        preparations: RecordReader, objects: EvidenceObjectStore, spool: SpoolStore,
    ) -> None:
        self.coordinator, self.records = coordinator, records
        self.sources = CaseReuseSourceReader(
            records, preparations, objects=objects, spool=spool,
        )

    def _current(self, project: str, run: str) -> ExecutionFacts:
        return self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)

    def _identity(self, source: CaseReuseSource) -> ReuseIdentity:
        prepared = source.original_preparation
        case = source.steps[0].content.checked_case()
        frozen = FrozenRunPlanReader(self.records).read(prepared)
        scope = {
            "acceptance_scope": acceptance_scope_to_payload(
                frozen.plan.scope, project_id=prepared.project_id,
            ),
            "selected_paths": prepared.selected_paths,
            "exclusion_rules": prepared.exclusion_rules,
            "refetch_dependencies": prepared.refetch_dependencies,
            "steps": [(item.content.frozen_step.step_id, item.step.level.value,
                       item.step.required_for_case) for item in source.steps],
        }
        entry = prepared.execution_source.model_dump(mode="json", exclude={"adapter_versions"})
        return ReuseIdentity(
            project_id=prepared.project_id, case_id=case.case_id,
            source_content_identity=prepared.snapshot.content_identity,
            # A frozen Python/dependency probe is not a current observation of
            # business data, remote deployment or other dynamic conditions.
            environment_dynamic_digest=None,
            check_scope_digest=payload_digest(scope),
            entry_input_digest=payload_digest({
                "entry": entry, "inputs": case.inputs, "preconditions": case.preconditions,
            }),
            rules_digest=payload_digest({
                "rules": [x.model_dump(mode="json") for x in prepared.rule_versions],
                "templates": [x.model_dump(mode="json") for x in prepared.template_versions],
            }),
            adapter_digest=payload_digest(prepared.execution_source.adapter_versions)
            if prepared.execution_source.adapter_versions else None,
            case_content_digest=source.case_revision.digest,
            assertion_basis_digest=payload_digest(asdict(case.assertion_basis))
            if case.assertion_basis.text_digest else None,
            # Target consumed-output/condition provenance is not established by
            # the source's old dependency list or matching configuration names.
            dependency_digest=None,
        )

    def inspect(self, command: Command) -> Mapping[str, object]:
        try:
            return self._inspect(command)
        except (KeyError, OSError, TypeError, ValueError) as error:
            if isinstance(error, CaseReuseUnverified):
                raise
            raise CaseReuseUnverified("exact case reuse material cannot be verified") from error

    def _inspect(self, command: Command) -> Mapping[str, object]:
        values = command.parameters
        if (
            command.action != "inspect_case_reuse" or not command.project_id
            or not command.project_id.strip()
            or set(values) != {"case_id", "source_run_id", "source_snapshot",
                                  "target_run_id", "target_snapshot"}
            or any(not isinstance(value, str) or not value.strip() for value in (
                values.get("case_id"), values.get("source_run_id"), values.get("target_run_id"),
            ))
            or command.target != values["case_id"]
            or values["source_run_id"] == values["target_run_id"]
        ):
            raise CaseReuseUnverified("reuse inspection requires exact distinct runs and case")
        case, source_run, target_run = (
            str(values[name]) for name in ("case_id", "source_run_id", "target_run_id")
        )
        source_ref = SnapshotContentRef.model_validate_json(
            json.dumps(values["source_snapshot"], allow_nan=False), strict=True,
        )
        target_ref = SnapshotContentRef.model_validate_json(
            json.dumps(values["target_snapshot"], allow_nan=False), strict=True,
        )
        project = command.project_id
        target_before, source_before = self._current(project, target_run), self._current(
            project, source_run,
        )
        source_current_ref = SnapshotContentRef.of(source_before)
        if SnapshotContentRef.of(target_before) != target_ref:
            raise CaseReuseUnverified("target changed; inspect the exact current target again")
        source = self.sources.read(
            project_id=project, run_id=source_run, case_id=case, reference=source_ref,
        )
        target = self.sources.read(
            project_id=project, run_id=target_run, case_id=case, reference=target_ref,
        )
        current_source = source if source_current_ref == source_ref else (
            self.sources.read(project_id=project, run_id=source_run, case_id=case,
                              reference=source_current_ref)
        )
        left, right = self._identity(source), self._identity(target)
        reasons = list(left.comparison_denials(right))
        source_layout = tuple((x.content.case_step_index, x.content.frozen_step.step_id)
                              for x in source.steps)
        target_layout = tuple((x.content.case_step_index, x.content.frozen_step.step_id)
                              for x in target.steps)
        mapping = []
        if source_layout != target_layout:
            reasons.append("case_step_layout_changed")
        else:
            mapping = [{
                "case_step_index": one.content.case_step_index,
                "logical_step_id": one.content.frozen_step.step_id,
                "source_step_id": one.step.step_id, "target_step_id": two.step.step_id,
                "source_attempt_id": one.attempt.attempt_id if one.attempt else None,
            } for one, two in zip(source.steps, target.steps, strict=True)]
        target_steps = {x.step.step_id for x in target.steps}
        if any(x.step_id in target_steps for x in target.facts.attempts):
            reasons.append("target_case_already_started")
        if any(x.attempt is None for x in source.steps if x.step.required_for_case):
            reasons.append("source_attempts_missing")
        if any(
            x.attempt is not None and (
                x.attempt.state is not AttemptStateFact.COMPLETED
                or x.attempt.capture_completeness is not CaptureCompletenessFact.COMPLETE
            ) for x in source.steps if x.step.required_for_case
        ):
            reasons.append("source_execution_incomplete")
        if tuple((x.step, x.attempt) for x in source.steps) != tuple(
            (x.step, x.attempt) for x in current_source.steps
        ):
            reasons.append("source_case_basis_changed")
        if source.original_preparation.environment != target.original_preparation.environment:
            reasons.append("frozen_environment_basis_changed")
        reasons.extend(("source_verified_unverified", "dependencies_valid_unverified",
                        "basis_confirmed_unverified", "verification_valid_unverified",
                        "evidence_qualification_unverified"))
        # Never return a mixed preview if either current pointer moved during
        # material/object reads; callers must request a fresh inspection.
        if SnapshotContentRef.of(self._current(project, target_run)) != target_ref or (
            SnapshotContentRef.of(self._current(project, source_run)) != source_current_ref
        ):
            raise CaseReuseUnverified("run changed while inspecting reuse material")
        return {
            "schema_version": "aitest.case-reuse-inspection/1.0",
            "project_id": project, "case_id": case,
            "source_run_id": source_run, "source_snapshot": source_ref.model_dump(mode="json"),
            "source_current_snapshot": source_current_ref.model_dump(mode="json"),
            "target_run_id": target_run, "target_snapshot": target_ref.model_dump(mode="json"),
            "status": "incompatible" if any(x.endswith("_changed") or x == (
                "target_case_already_started"
            ) for x in reasons) else "unverified",
            "source_identity": asdict(left), "target_identity": asdict(right),
            "step_mapping": mapping, "denial_reasons": list(dict.fromkeys(reasons)),
        }
