"""Assess a runtime change using saved authority; persist and recheck separately.

The supplied Plan is a view to verify, never the source of frozen material. The
pure guard remains reusable, but cannot establish repository provenance itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aitest.application.planning.draft import text_digest
from aitest.application.planning.publish import payload_digest, plan_publication_digest
from aitest.application.planning.run_mode import request_runtime_revision
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_content_digest,
    case_from_payload,
    confirmation_from_payload,
)
from aitest.application.planning.substrate import AggregateKind, RecordReader
from aitest.application.ports import RuntimeExecutionReader
from aitest.domain.planning.plans import Case, ConfirmationRecord, Plan
from aitest.domain.planning.runtime_revision import (
    RuntimeRevisionDecision,
    RuntimeRevisionRequest,
)


@dataclass(frozen=True, slots=True)
class SavedRuntimeRevisionAssessment:
    reader: RecordReader
    execution: RuntimeExecutionReader

    def assess(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: Plan,
        request: RuntimeRevisionRequest,
        confirmation_ids: tuple[str, ...] = (),
    ) -> RuntimeRevisionDecision:
        """Return a preview pinned to saved facts, with no write or external effect.

        The eventual commit must reread and reevaluate under the common UOW lock.
        A caller cannot submit facts, frozen cases, or confirmation bodies here.
        """
        facts = self.execution.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        if (facts.project_id, facts.run_id) != (project_id, run_id):
            raise ValueError("runtime facts belong to another project or run")
        if facts.runtime_revision_refs or facts.run.runtime_revision_refs:
            raise ValueError("saved runtime revision sequence is not yet readable")
        ref = facts.plan_revision
        saved = self._read(project_id, "plan", ref.revision_id, ref.revision_no)
        if payload_digest(saved) != ref.digest:
            raise ValueError("saved plan content differs from the frozen digest")
        if (
            plan.plan_id != ref.revision_id
            or type(plan.record_revision) is not int
            or plan.record_revision != ref.revision_no
            or saved.get("plan_id") != ref.revision_id
            or saved.get("revision") != plan.revision
            or saved.get("scope_id") != plan.scope.scope_id
            or saved.get("scope_revision") != plan.scope.revision
        ):
            raise ValueError("plan view does not use the exact saved plan revision")
        expected_refs = [
            {"case_id": ref.case_id, "revision": ref.revision, "digest": ref.digest}
            for ref in plan.case_revisions
        ]
        if saved.get("case_revisions") != expected_refs:
            raise ValueError("plan view differs from the saved frozen case references")
        scope = acceptance_scope_from_payload(
            self._read(project_id, "acceptance_scope", plan.scope.scope_id, plan.scope.revision)
        )
        if scope != plan.scope:
            raise ValueError("plan view differs from the saved acceptance scope")
        cases = self._cases(project_id, plan)
        summaries = saved.get("cases")
        if not isinstance(summaries, (list, tuple)) or any(
            not isinstance(value, Mapping) for value in summaries
        ):
            raise ValueError("saved plan lacks readable case summaries")
        by_id = {case.case_id: case for case in cases}
        ordered: list[Case] = []
        for value in summaries:
            case = by_id.get(value.get("case_id"))
            if case is None or value.get("revision") != case.revision:
                raise ValueError("saved plan case summary differs from its frozen reference")
            ordered.append(case)
        if len(ordered) != len(cases) or len({case.case_id for case in ordered}) != len(cases):
            raise ValueError("saved plan case summaries must cover its unique frozen cases")
        if plan_publication_digest(plan, ordered, project_id=project_id) != ref.digest:
            raise ValueError("plan view differs from the complete saved plan content")
        if (
            set(facts.run.required_scope) != scope.required_case_ids
            or set(facts.coverage.mandatory_case_ids) != scope.required_case_ids
            or set(facts.run.selected_scope) != set(facts.coverage.selected_case_ids)
            or not set(facts.run.selected_scope) <= set(by_id)
            or len(set(facts.run.selected_scope)) != len(facts.run.selected_scope)
            or facts.run.tier.value != plan.run_tier.value
            or {step.case_id for step in facts.steps} != set(facts.run.selected_scope)
            or (
                plan.run_tier.value == "full"
                and not scope.required_case_ids <= set(facts.run.selected_scope)
            )
        ):
            raise ValueError("saved run scope or tier differs from the frozen plan")
        for change in request.case_changes:
            basis = change.next_case.assertion_basis
            if basis.text_digest != (text_digest(basis.text) or ""):
                raise ValueError("proposed assertion text does not prove its digest")
        confirmations = self._confirmations(project_id, cases, request, confirmation_ids)
        decision = request_runtime_revision(
            plan=plan, cases=cases, confirmations=confirmations, request=request, facts=facts
        )
        latest = self.execution.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        if latest != facts:
            raise ValueError("current execution snapshot changed while reading frozen material")
        return decision

    def _read(
        self, project_id: str, kind: AggregateKind, record_id: str, revision: int
    ) -> Mapping[str, object]:
        if type(revision) is not int or revision < 1:
            raise ValueError("runtime basis requires an exact positive repository revision")
        record = self.reader.read(aggregate_kind=kind, record_id=record_id, revision=revision)
        identity = (record.aggregate_kind, record.record_id, record.revision)
        if identity != (kind, record_id, revision):
            raise ValueError("runtime basis reader returned another record identity")
        if (
            not isinstance(record.payload, Mapping)
            or record.payload.get("project_id") != project_id
        ):
            raise ValueError("runtime basis belongs to another or unknown project")
        return record.payload

    def _cases(self, project_id: str, plan: Plan) -> tuple[Case, ...]:
        cases: list[Case] = []
        for ref in plan.case_revisions:
            case = case_from_payload(self._read(project_id, "case", ref.case_id, ref.revision))
            if (
                (case.case_id, case.revision) != (ref.case_id, ref.revision)
                or case_content_digest(case, project_id=project_id) != ref.digest
                or case.assertion_basis.text_digest
                != (text_digest(case.assertion_basis.text) or "")
            ):
                raise ValueError("saved case content differs from the exact frozen case basis")
            cases.append(case)
        return tuple(cases)

    def _confirmations(
        self,
        project_id: str,
        cases: tuple[Case, ...],
        request: RuntimeRevisionRequest,
        identities: tuple[str, ...],
    ) -> tuple[ConfirmationRecord, ...]:
        if any(not identity.strip() for identity in identities) or len(set(identities)) != len(
            identities
        ):
            raise ValueError("confirmation references must be nonempty and unique")
        allowed = {(case.case_id, case.revision): case for case in cases}
        allowed.update(
            ((change.next_case.case_id, change.next_case.revision), change.next_case)
            for change in request.case_changes
        )
        confirmations: list[ConfirmationRecord] = []
        for identity in identities:
            raw = self._read(project_id, "case_link", identity, 1)
            confirmation = confirmation_from_payload(raw)
            case_revision = raw.get("case_revision")
            intent = raw.get("intent_id")
            if type(case_revision) is not int or not isinstance(intent, str) or not intent.strip():
                raise ValueError("legacy or unbound confirmation cannot prove runtime basis")
            case = allowed.get((confirmation.case_id, case_revision))
            if (
                case is None
                or confirmation.confirmation_id != identity
                or identity != "confirmation-" + payload_digest([project_id, intent])[7:]
                or raw.get("input_digest")
                != payload_digest(
                    [
                        project_id,
                        case.case_id,
                        case_revision,
                        confirmation.basis_revision,
                        confirmation.basis_text_digest,
                    ]
                )
                or not confirmation.matches(case.assertion_basis, case_id=case.case_id)
            ):
                raise ValueError("saved confirmation does not prove the exact runtime basis")
            saved_case = case_from_payload(
                self._read(project_id, "case", case.case_id, case_revision)
            )
            if case_content_digest(saved_case, project_id=project_id) != case_content_digest(
                case, project_id=project_id
            ):
                raise ValueError("confirmation case content differs from its saved basis")
            confirmations.append(confirmation)
        return tuple(confirmations)
