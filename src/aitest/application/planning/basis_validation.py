"""Verify authoritative records behind a candidate, without producing execution facts."""

from __future__ import annotations

from typing import Any

from aitest.application.planning.draft import text_digest
from aitest.application.planning.drift import check_frozen_basis
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
    confirmation_from_payload,
)
from aitest.application.planning.substrate import AggregateKind, RecordReader
from aitest.application.ports import BasisConfirmationProof, ControlledWriteProof
from aitest.contracts.prepared_run import BlockingReason, PreparedRun
from aitest.domain.approvals import ApprovalRequired
from aitest.domain.planning.plans import effective_assertion_basis_state


def validate_prepared_material(
    prepared: PreparedRun,
    *,
    reader: RecordReader,
    workspace_id: str | None = None,
    approvals: BasisConfirmationProof | None = None,
    controlled_writes: ControlledWriteProof | None = None,
) -> tuple[BlockingReason, ...]:
    report = check_frozen_basis(prepared, reader=reader)
    problems = [
        f"{check.source_kind} {check.record_id}@{check.revision}: "
        + (check.detail or "frozen content cannot be verified")
        for check in report.missing
    ]
    if report.uncovered:
        problems.append("uncovered frozen sources: " + ", ".join(report.uncovered))
    if workspace_id is not None and prepared.workspace_id != workspace_id:
        problems.append("preparation belongs to another workspace")
    if not problems:
        try:
            _verify_links(prepared, reader, approvals, controlled_writes)
        except (ApprovalRequired, ValueError, TypeError, KeyError, OSError) as error:
            problems.append(str(error) or "frozen content is malformed")
    return tuple(BlockingReason(code="basis_unverified", message=problem) for problem in problems)


def _verify_links(
    prepared: PreparedRun,
    reader: RecordReader,
    approvals: BasisConfirmationProof | None = None,
    controlled_writes: ControlledWriteProof | None = None,
) -> None:
    def read(kind: AggregateKind, record: str, revision: int) -> dict[str, Any]:
        stored = reader.read(
            aggregate_kind=kind,
            record_id=record,
            revision=revision,
        )
        if stored.payload.get("project_id", stored.payload.get("local_project_id")) != (
            prepared.project_id
        ):
            raise ValueError(f"{kind} belongs to another project")
        return dict(stored.payload)

    plan = read("plan", prepared.plan_revision.revision_id, prepared.plan_revision.revision_no)
    if controlled_writes is not None:
        controlled_writes.validate_saved_write(
            project_id=prepared.project_id,
            action="publish_plan",
            aggregate_kind="plan",
            record_id=prepared.plan_revision.revision_id,
            record_revision=prepared.plan_revision.revision_no,
            payload=plan,
        )
        for rule in prepared.rule_versions:
            controlled_writes.validate_saved_write(
                project_id=prepared.project_id,
                action="publish_rules",
                aggregate_kind="rule_version",
                record_id=rule.rule_id,
                record_revision=rule.revision,
                payload=read("rule_version", rule.rule_id, rule.revision),
            )
    scope = acceptance_scope_from_payload(
        read("acceptance_scope", prepared.scope_id or "missing", prepared.acceptance_scope_revision)
    )
    if (
        plan.get("scope_id") != scope.scope_id
        or plan.get("scope_revision") != scope.revision
        or set(plan.get("required_case_ids", ())) != scope.required_case_ids
        or set(plan.get("template_case_ids", ())) != scope.template_case_ids
        or set(prepared.frozen_required_case_ids) != scope.required_case_ids
        or set(prepared.template_required_case_ids) != scope.template_case_ids
    ):
        raise ValueError("frozen required/template scope differs from the saved plan and scope")
    for key, values in (
        ("case_revisions", prepared.case_revisions),
        ("rule_revisions", prepared.rule_versions),
        ("template_versions", prepared.template_versions),
    ):
        actual = [value.model_dump(mode="json") for value in values]
        frozen = plan.get(key)
        if not isinstance(frozen, (list, tuple)) or sorted(actual, key=str) != sorted(
            (dict(value) for value in frozen), key=str
        ):
            raise ValueError(f"{key} differs from the saved plan")
    if plan.get("run_tier") != prepared.run_tier.value:
        raise ValueError("run tier differs from the published plan")
    if plan.get("initial_driver") != prepared.initial_driver.value:
        raise ValueError("initial driver differs from the published plan")
    project = read("project", prepared.project_id, prepared.project_revision or 1)
    if project.get("workspace_id") != prepared.workspace_id:
        raise ValueError("saved project belongs to another workspace")
    binding = read("binding", prepared.binding_id, prepared.binding_revision)
    if controlled_writes is not None:
        controlled_writes.validate_saved_write(
            project_id=prepared.project_id,
            action="save_binding",
            aggregate_kind="binding",
            record_id=prepared.binding_id,
            record_revision=prepared.binding_revision,
            payload=binding,
        )
    if binding.get("confirmed") is not True or binding.get("binding_form") != (
        prepared.binding_form.value
    ):
        raise ValueError("source binding is unconfirmed or differs from the frozen form")
    snapshot = read(
        "source_snapshot", prepared.snapshot.source_snapshot_id, prepared.snapshot.record_revision
    )
    if (
        snapshot.get("binding_id") != prepared.binding_id
        or snapshot.get("binding_revision") != prepared.binding_revision
        or snapshot.get("purpose") != "prepare"
        or prepared.snapshot.purpose != "prepare"
        or not isinstance(snapshot.get("pinned_snapshot_id"), str)
        or tuple(snapshot.get("selected_paths", ())) != prepared.selected_paths
        or tuple(snapshot.get("exclusion_rules", ())) != prepared.exclusion_rules
        or tuple(snapshot.get("refetch_dependencies", ())) != prepared.refetch_dependencies
    ):
        raise ValueError("source snapshot does not prove this preparation binding and selection")
    for key in ("git_base_commit", "git_diff_digest", "plain_manifest_digest"):
        if snapshot.get(key) != getattr(prepared, key):
            raise ValueError(f"{key} differs from the actual saved source snapshot")
    environment = read(
        "environment", prepared.environment.environment_id, prepared.environment.revision
    )
    if environment.get("isolation_mode") != prepared.environment.isolation_mode.value:
        raise ValueError("environment isolation differs from the saved carrier")
    if prepared.environment.isolation_mode.value != "venv":
        if controlled_writes is None:
            raise ValueError("non-default environment requires exact controlled confirmation proof")
        controlled_writes.validate_saved_write(
            project_id=prepared.project_id,
            action="save_environment",
            aggregate_kind="environment",
            record_id=prepared.environment.environment_id,
            record_revision=prepared.environment.revision,
            payload=environment,
        )
    refs = {ref.case_id: ref for ref in prepared.case_revisions}
    bases = {basis.case_id: basis for basis in prepared.assertion_bases}
    for frozen in prepared.frozen_cases:
        ref = refs[frozen.case_id]
        case = case_from_payload(read("case", ref.case_id, ref.revision))
        if (
            frozen.revision != case.revision
            or frozen.layer != case.layer.value
            or frozen.required != (case.case_id in scope.required_case_ids)
            or frozen.independent_verification != case.independent_verification
            or tuple(sorted(frozen.mock_scope)) != tuple(sorted(case.mock_scope))
            or frozen.importance != case.importance.value
            or tuple(step.objective for step in frozen.steps) != case.steps
            or any(
                step.layer != case.layer.value or step.expected != case.expected
                for step in frozen.steps
            )
        ):
            raise ValueError(f"frozen execution content differs from saved case {case.case_id}")
        for key in ("acceptance_item_ids", "module_ids", "environment_ids", "critical_path_ids"):
            if set(getattr(frozen.links, key)) != getattr(case.links, key):
                raise ValueError(f"frozen case {case.case_id} has different {key}")
        entry = bases[case.case_id]
        if text_digest(case.assertion_basis.text) != case.assertion_basis.text_digest:
            raise ValueError(f"saved assertion text does not prove its digest for {case.case_id}")
        if entry.basis_revision != case.assertion_basis.revision or (
            entry.basis_text_digest != case.assertion_basis.text_digest
        ):
            raise ValueError(f"assertion basis differs from saved case {case.case_id}")
        confirmations = []
        for ref_confirmation in entry.confirmation_refs:
            saved_confirmation = read("case_link", ref_confirmation.confirmation_id, 1)
            if approvals is not None:
                approvals.validate_basis_confirmation(
                    project_id=prepared.project_id, payload=saved_confirmation
                )
            confirmation = confirmation_from_payload(saved_confirmation)
            intent = saved_confirmation.get("intent_id")
            declared_digest = payload_digest(
                [
                    prepared.project_id,
                    case.case_id,
                    saved_confirmation.get("case_revision"),
                    entry.basis_revision,
                    entry.basis_text_digest,
                ]
            )
            if (
                not isinstance(intent, str)
                or not intent
                or saved_confirmation.get("input_digest") != declared_digest
                or confirmation.confirmation_id
                != "confirmation-" + (payload_digest([prepared.project_id, intent])[7:])
            ):
                raise ValueError(
                    "legacy or unbound confirmation requires a controlled new confirmation"
                )
            if (
                confirmation.case_id != case.case_id
                or confirmation.basis_revision != entry.basis_revision
                or confirmation.basis_text_digest != entry.basis_text_digest
                or confirmation.confirmed_at_commit != ref_confirmation.confirmed_at_commit
            ):
                raise ValueError("confirmation does not prove this exact assertion basis")
            confirmations.append(confirmation)
        state = effective_assertion_basis_state(
            case.assertion_basis, confirmations, case_id=case.case_id
        )
        if entry.assertion_basis_state.value != state.value:
            raise ValueError(f"self-reported confirmation is unverified for {case.case_id}")
