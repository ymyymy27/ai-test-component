"""Compare exact historic material with a current target; never grant reuse."""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.frozen_plan import FrozenRunPlanReader
from aitest.application.execution.reuse_basis import (
    consumed_dependency_basis,
    source_evidence_basis,
)
from aitest.application.execution.reuse_sources import CaseReuseSource, CaseReuseSourceReader
from aitest.application.execution.runtime_revision import (
    SavedRuntimeRevisionReader,
    SnapshotContentRef,
)
from aitest.application.planning.basis_proof import SavedCaseBasisReader
from aitest.application.planning.draft import text_digest
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import acceptance_scope_to_payload
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import (
    BasisConfirmationProof,
    EvidenceObjectStore,
    RecordRepository,
    SpoolStore,
)
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import (
    AttemptStateFact,
    CaptureCompletenessFact,
    ExecutionFacts,
)
from aitest.domain.execution.reuse import ReuseIdentity
from aitest.domain.planning.plans import AssertionBasisState, effective_assertion_basis_state


class CaseReuseUnverified(ValueError):
    code = "CASE_REUSE_UNVERIFIED"


@dataclass(frozen=True, slots=True)
class CaseReuseVerification:
    """一次核对的**结构化结果**（只读检查与保存侧共用同一次核对）。

    `reference_inputs` 是保存侧登记 `ReuseReference` 所需的来源身份与修订摘要：
    它们全部来自这次核对读到的冻结材料，**不授予资格**——资格仍由消费侧
    （`derive_reuse_outcome`）按当前事实重新派生。
    """

    project_id: str
    case_id: str
    source_run_id: str
    target_run_id: str
    source_snapshot: Mapping[str, object]
    source_current_snapshot: Mapping[str, object]
    target_snapshot: Mapping[str, object]
    status: str
    denial_reasons: tuple[str, ...]
    step_mapping: tuple[Mapping[str, object], ...]
    source_identity: Mapping[str, object]
    target_identity: Mapping[str, object]
    basis_confirmation: Mapping[str, Mapping[str, object]]
    reference_inputs: Mapping[str, object]


class CaseReuseInspection:
    def __init__(
        self,
        coordinator: ExecutionCommitCoordinator,
        records: RecordRepository,
        preparations: RecordReader,
        objects: EvidenceObjectStore,
        spool: SpoolStore,
        *,
        basis_proof: BasisConfirmationProof | None = None,
    ) -> None:
        self.coordinator, self.records = coordinator, records
        self.sources = CaseReuseSourceReader(
            records,
            preparations,
            objects=objects,
            spool=spool,
        )
        self.bases = SavedCaseBasisReader(records, basis_proof)

    def _current(self, project: str, run: str) -> ExecutionFacts:
        return self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)

    def consumption_basis(
        self, reference: Any, target: ExecutionFacts, source: ExecutionFacts
    ) -> Any:
        """Read exact persisted material for R; unknown live state stays unknown."""
        from aitest.domain.execution.reuse import (
            ReuseCondition,
            ReuseConditionKind,
            ReuseConditionState,
            WholeCaseReuseBasis,
        )

        try:
            saved_source = self.sources.read(
                project_id=source.project_id,
                run_id=source.run_id,
                case_id=reference.case_id,
                reference=SnapshotContentRef.of(source),
            )
            saved_target = self.sources.read(
                project_id=target.project_id,
                run_id=target.run_id,
                case_id=reference.case_id,
                reference=SnapshotContentRef.of(target),
            )
            prepared = saved_source.original_preparation
            left, right = self._identity(saved_source), self._identity(saved_target)
            bindings = (
                reference.source_plan_revision_digest
                == payload_digest(prepared.plan_revision.model_dump(mode="json")),
                reference.environment_revision_digest
                == payload_digest(prepared.environment.model_dump(mode="json")),
                reference.entry_input_digest == left.entry_input_digest,
                reference.rules_revision_digest == left.rules_digest,
                reference.adapter_digest is not None
                and reference.adapter_digest == left.adapter_digest,
            )
            if not all(bindings):
                return None
            basis = self._basis_confirmation(saved_source, saved_target)
            states = {kind: ReuseConditionState.UNKNOWN for kind in ReuseConditionKind}
            states[ReuseConditionKind.EVIDENCE_READABLE] = ReuseConditionState.VERIFIED
            if source.source_verifications and all(
                item.state.value == "verified"
                and not item.gap_ids
                and item.observed_source_digest == left.source_content_identity
                and item.expected_source_binding_digest == left.source_content_identity
                for item in source.source_verifications
            ):
                states[ReuseConditionKind.SOURCE_VERIFIED] = ReuseConditionState.VERIFIED
            if all(side["state"] == "confirmed" for side in basis.values()):
                states[ReuseConditionKind.BASIS_CONFIRMED] = ReuseConditionState.VERIFIED
            actual_mapping = tuple(
                (item.step.step_id, item.attempt.attempt_id)
                for item in saved_source.steps
                if item.step.required_for_case
                and item.attempt is not None
                and item.attempt.is_current
            )
            if set(actual_mapping) == set(reference.source_attempt_by_step):
                states[ReuseConditionKind.SOURCE_CURRENT] = ReuseConditionState.VERIFIED
            attempts = {attempt for _, attempt in actual_mapping}
            if attempts and all(
                any(
                    item.verification_of == attempt
                    and item.observation.value in {"matched", "mismatched"}
                    and item.evidence_refs
                    and not item.gap_ids
                    for item in source.verifications
                )
                for attempt in attempts
            ):
                states[ReuseConditionKind.VERIFICATION_VALID] = ReuseConditionState.VERIFIED
            if (
                consumed_dependency_basis(saved_source)[1]
                and consumed_dependency_basis(saved_target)[1]
            ):
                states[ReuseConditionKind.DEPENDENCIES_VALID] = ReuseConditionState.VERIFIED
            # Saved configuration is not a fresh observation of business state.
            # The existing identities intentionally leave dynamic/dependency
            # qualifications unknown until authoritative observation is available.
            return WholeCaseReuseBasis(
                source_run_id=source.run_id,
                source_snapshot_id=source.snapshot_commit_id,
                source_snapshot_digest=SnapshotContentRef.of(source).digest,
                source_attempt_by_step=reference.source_attempt_by_step,
                source=left,
                target=right,
                conditions=tuple(ReuseCondition(kind, state) for kind, state in states.items()),
            )
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def _identity(self, source: CaseReuseSource) -> ReuseIdentity:
        prepared = source.original_preparation
        case = source.steps[0].content.checked_case()
        frozen = FrozenRunPlanReader(self.records).read(prepared)
        scope = {
            "acceptance_scope": acceptance_scope_to_payload(
                frozen.plan.scope,
                project_id=prepared.project_id,
            ),
            "selected_paths": prepared.selected_paths,
            "exclusion_rules": prepared.exclusion_rules,
            "refetch_dependencies": prepared.refetch_dependencies,
            "steps": [
                (
                    item.content.frozen_step.step_id,
                    item.step.level.value,
                    item.step.required_for_case,
                )
                for item in source.steps
            ],
        }
        entry = prepared.execution_source.model_dump(mode="json", exclude={"adapter_versions"})
        return ReuseIdentity(
            project_id=prepared.project_id,
            case_id=case.case_id,
            source_content_identity=prepared.snapshot.content_identity,
            # A frozen Python/dependency probe is not a current observation of
            # business data, remote deployment or other dynamic conditions.
            environment_dynamic_digest=None,
            check_scope_digest=payload_digest(scope),
            entry_input_digest=payload_digest(
                {
                    "entry": entry,
                    "inputs": case.inputs,
                    "preconditions": case.preconditions,
                }
            ),
            rules_digest=payload_digest(
                {
                    "rules": [x.model_dump(mode="json") for x in prepared.rule_versions],
                    "templates": [x.model_dump(mode="json") for x in prepared.template_versions],
                }
            ),
            adapter_digest=payload_digest(prepared.execution_source.adapter_versions)
            if prepared.execution_source.adapter_versions
            else None,
            case_content_digest=source.case_revision.digest,
            assertion_basis_digest=payload_digest(asdict(case.assertion_basis))
            if case.assertion_basis.text_digest
            else None,
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

    def verify(self, command: Command) -> CaseReuseVerification:
        """结构化核对入口（保存侧登记 `ReuseReference` 与只读检查共用）。"""

        try:
            return self._verify(command)
        except (KeyError, OSError, TypeError, ValueError) as error:
            if isinstance(error, CaseReuseUnverified):
                raise
            raise CaseReuseUnverified("exact case reuse material cannot be verified") from error

    def _basis_confirmation(
        self,
        source: CaseReuseSource,
        target: CaseReuseSource,
    ) -> dict[str, dict[str, object]]:
        case_id = source.steps[0].content.checked_case().case_id
        candidates: dict[str, list[dict[str, object]]] = {}
        for selected in (source, target):
            for entry in selected.original_preparation.assertion_bases:
                if entry.case_id == case_id:
                    for ref in entry.confirmation_refs:
                        candidates.setdefault(ref.confirmation_id, []).append(
                            ref.model_dump(
                                mode="json",
                            )
                        )
            for reference in selected.facts.runtime_revision_refs:
                record = SavedRuntimeRevisionReader(self.records).read_record(
                    project_id=selected.facts.project_id,
                    reference=reference,
                )
                if (record.run_id, record.origin_workspace_id) != (
                    selected.facts.run_id,
                    selected.facts.run.origin_workspace_id,
                ):
                    raise CaseReuseUnverified("basis confirmation runtime owner differs")
                for identity in record.confirmation_ids:
                    candidates.setdefault(identity, [])
        confirmations = []
        for identity, expected in candidates.items():
            actual = self.bases.read(
                project_id=source.original_preparation.project_id,
                confirmation_id=identity,
            )
            actual_ref = {
                "confirmation_id": actual.confirmation_id,
                "case_id": actual.case_id,
                "basis_revision": actual.basis_revision,
                "confirmed_at_commit": actual.confirmed_at_commit,
            }
            if any(payload_digest(value) != payload_digest(actual_ref) for value in expected):
                raise CaseReuseUnverified("basis confirmation differs from its frozen reference")
            confirmations.append(actual)
        result: dict[str, dict[str, object]] = {}
        for side, selected in (("source", source), ("target", target)):
            case = selected.steps[0].content.checked_case()
            if case.assertion_basis.state is not AssertionBasisState.MISSING and (
                text_digest(case.assertion_basis.text) != case.assertion_basis.text_digest
            ):
                raise CaseReuseUnverified("effective case basis text/digest differs")
            result[side] = {
                "state": effective_assertion_basis_state(
                    case.assertion_basis,
                    confirmations,
                    case_id=case.case_id,
                ).value,
                "confirmation_refs": [
                    {
                        "confirmation_id": item.confirmation_id,
                        "case_id": item.case_id,
                        "basis_revision": item.basis_revision,
                        "confirmed_at_commit": item.confirmed_at_commit,
                    }
                    for item in confirmations
                    if item.matches(
                        case.assertion_basis,
                        case_id=case.case_id,
                    )
                ],
            }
        return result

    def _verify(self, command: Command) -> CaseReuseVerification:
        values = command.parameters
        if (
            command.action not in {"inspect_case_reuse", "select_case_reuse"}
            or not command.project_id
            or not command.project_id.strip()
            or set(values)
            != {"case_id", "source_run_id", "source_snapshot", "target_run_id", "target_snapshot"}
            or any(
                not isinstance(value, str) or not value.strip()
                for value in (
                    values.get("case_id"),
                    values.get("source_run_id"),
                    values.get("target_run_id"),
                )
            )
            or command.target != values["case_id"]
            or values["source_run_id"] == values["target_run_id"]
        ):
            raise CaseReuseUnverified("reuse inspection requires exact distinct runs and case")
        case, source_run, target_run = (
            str(values[name]) for name in ("case_id", "source_run_id", "target_run_id")
        )
        source_ref = SnapshotContentRef.model_validate_json(
            json.dumps(values["source_snapshot"], allow_nan=False),
            strict=True,
        )
        target_ref = SnapshotContentRef.model_validate_json(
            json.dumps(values["target_snapshot"], allow_nan=False),
            strict=True,
        )
        project = command.project_id
        target_before, source_before = (
            self._current(project, target_run),
            self._current(
                project,
                source_run,
            ),
        )
        source_current_ref = SnapshotContentRef.of(source_before)
        if SnapshotContentRef.of(target_before) != target_ref:
            raise CaseReuseUnverified("target changed; inspect the exact current target again")
        source = self.sources.read(
            project_id=project,
            run_id=source_run,
            case_id=case,
            reference=source_ref,
        )
        target = self.sources.read(
            project_id=project,
            run_id=target_run,
            case_id=case,
            reference=target_ref,
        )
        current_source = (
            source
            if source_current_ref == source_ref
            else (
                self.sources.read(
                    project_id=project,
                    run_id=source_run,
                    case_id=case,
                    reference=source_current_ref,
                )
            )
        )
        left, right = self._identity(source), self._identity(target)
        reasons = list(left.comparison_denials(right))
        source_layout = tuple(
            (x.content.case_step_index, x.content.frozen_step.step_id) for x in source.steps
        )
        target_layout = tuple(
            (x.content.case_step_index, x.content.frozen_step.step_id) for x in target.steps
        )
        mapping = []
        if source_layout != target_layout:
            reasons.append("case_step_layout_changed")
        else:
            mapping = [
                {
                    "case_step_index": one.content.case_step_index,
                    "logical_step_id": one.content.frozen_step.step_id,
                    "source_step_id": one.step.step_id,
                    "target_step_id": two.step.step_id,
                    "source_attempt_id": one.attempt.attempt_id if one.attempt else None,
                }
                for one, two in zip(source.steps, target.steps, strict=True)
            ]
        target_steps = {x.step.step_id for x in target.steps}
        if any(x.step_id in target_steps for x in target.facts.attempts):
            reasons.append("target_case_already_started")
        if any(x.attempt is None for x in source.steps if x.step.required_for_case):
            reasons.append("source_attempts_missing")
        if any(
            x.attempt is not None
            and (
                x.attempt.state is not AttemptStateFact.COMPLETED
                or x.attempt.capture_completeness is not CaptureCompletenessFact.COMPLETE
            )
            for x in source.steps
            if x.step.required_for_case
        ):
            reasons.append("source_execution_incomplete")
        if tuple((x.step, x.attempt) for x in source.steps) != tuple(
            (x.step, x.attempt) for x in current_source.steps
        ) or payload_digest(source_evidence_basis(source)) != payload_digest(
            source_evidence_basis(current_source)
        ):
            reasons.append("source_case_basis_changed")
        source_dependencies, original_current = consumed_dependency_basis(source)
        current_dependencies, still_current = consumed_dependency_basis(current_source)
        if payload_digest(source_dependencies) != payload_digest(current_dependencies):
            reasons.append("source_dependency_basis_changed")
        if not original_current or not still_current:
            reasons.append("source_dependency_basis_unverified")
        if source.original_preparation.environment != target.original_preparation.environment:
            reasons.append("frozen_environment_basis_changed")
        basis = self._basis_confirmation(source, target)
        if any(value["state"] != AssertionBasisState.CONFIRMED.value for value in basis.values()):
            reasons.append("basis_confirmed_unverified")
        reasons.extend(
            (
                "source_verified_unverified",
                "dependencies_valid_unverified",
                "verification_valid_unverified",
                "evidence_qualification_unverified",
            )
        )
        # Never return a mixed preview if either current pointer moved during
        # material/object reads; callers must request a fresh inspection.
        if SnapshotContentRef.of(self._current(project, target_run)) != target_ref or (
            SnapshotContentRef.of(self._current(project, source_run)) != source_current_ref
        ):
            raise CaseReuseUnverified("run changed while inspecting reuse material")
        prepared = source.original_preparation
        source_attempts = {
            item.attempt.attempt_id for item in source.steps if item.attempt is not None
        }
        evidence_refs = tuple(
            sorted(
                {
                    item.evidence_id
                    for item in source.facts.evidence_refs
                    if item.attempt_id in source_attempts
                }
            )
        )
        status = (
            "incompatible"
            if any(x.endswith("_changed") or x == "target_case_already_started" for x in reasons)
            else "unverified"
        )
        return CaseReuseVerification(
            project_id=project,
            case_id=case,
            source_run_id=source_run,
            target_run_id=target_run,
            source_snapshot=source_ref.model_dump(mode="json"),
            source_current_snapshot=source_current_ref.model_dump(mode="json"),
            target_snapshot=target_ref.model_dump(mode="json"),
            status=status,
            denial_reasons=tuple(dict.fromkeys(reasons)),
            step_mapping=tuple(mapping),
            source_identity=asdict(left),
            target_identity=asdict(right),
            basis_confirmation=basis,
            reference_inputs={
                "source_content_identity": left.source_content_identity,
                "source_plan_revision_digest": payload_digest(
                    prepared.plan_revision.model_dump(mode="json")
                ),
                "environment_revision_digest": payload_digest(
                    prepared.environment.model_dump(mode="json")
                ),
                "entry_input_digest": left.entry_input_digest,
                "rules_revision_digest": left.rules_digest,
                "adapter_digest": left.adapter_digest,
                "evidence_refs": evidence_refs,
            },
        )

    def _inspect(self, command: Command) -> Mapping[str, object]:
        verified = self._verify(command)
        return {
            "schema_version": "aitest.case-reuse-inspection/1.1",
            "project_id": verified.project_id,
            "case_id": verified.case_id,
            "source_run_id": verified.source_run_id,
            "source_snapshot": dict(verified.source_snapshot),
            "source_current_snapshot": dict(verified.source_current_snapshot),
            "target_run_id": verified.target_run_id,
            "target_snapshot": dict(verified.target_snapshot),
            "status": verified.status,
            "source_identity": dict(verified.source_identity),
            "target_identity": dict(verified.target_identity),
            "basis_confirmation": {
                side: dict(value) for side, value in verified.basis_confirmation.items()
            },
            "step_mapping": [dict(item) for item in verified.step_mapping],
            "denial_reasons": list(verified.denial_reasons),
        }
