"""Persisted report revisions and local reviews (P1-FR14).

Contracts: phase-one architecture 03 (frozen report/export inputs and blocking
issues), architecture 04 (finite indexes and exact revisions), and CORE-001
(callers do not supply decision or eligibility booleans).

The shared assertion producer and decision rules consume saved execution,
effective controlled basis confirmation and fully proved whole-case reuse.
Finite issue queries collect all scope-related blockers and their canonical
chains; issue_ids cannot remove known blockers. Read/publication sequence
checks reject mixed boundaries. Reports retain exact execution and issue
references for exports; old records without exact sources retain gaps.

Dynamic reuse environment/dependency proof and default business verification
still require trustworthy inputs. policy_version uses the current 1.0 policy;
case_ids_revision uses the frozen scope identity. A local review pins the exact
saved report revision and never rewrites that report.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields
from typing import Any, cast

from aitest.application.planning.preparation import preparation_identity_digest
from aitest.application.planning.preparation_origin import (
    load_saved_preparation,
    validate_preparation_origin,
)
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
)
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import EvidenceObjectStore, RecordQuery
from aitest.application.review import reporting as facade
from aitest.application.review.authoritative_basis import SavedCaseBasisCheck
from aitest.application.review.report_codec import (
    _command_identity,
    _command_parameters,
    _digest,
    _sequence_len,
    _sequence_number,
    _text,
)
from aitest.application.review.report_codec import (
    local_review_from_payload as local_review_from_payload,
)
from aitest.application.review.report_codec import (
    local_review_to_payload as local_review_to_payload,
)
from aitest.application.review.report_codec import (
    report_from_payload as report_from_payload,
)
from aitest.application.review.report_codec import (
    report_to_payload as report_to_payload,
)
from aitest.application.review.report_creation import ReportCreationMixin
from aitest.application.review.report_types import (
    DEFAULT_POLICY_VERSION,
    LOCAL_REVIEW_AGGREGATE,
    LOCAL_REVIEW_SCHEMA,
    REPORT_AGGREGATE,
    REPORT_INTENT_AGGREGATE,
    REPORT_INTENT_SCHEMA,
    REPORT_SCHEMA,
    SUPPORTED_ACTIONS,
    ReportActorReader,
    ReportActorRequired,
    ReportClock,
    ReportGuardRefused,
    ReportIntentConflict,
    ReportManagementError,
    ReportNotSaved,
    ReportRecordRepository,
    ReportRevisionConflict,
    ReportRuntimeFactsReader,
    ReportUnitOfWork,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.reuse_reference import ReuseReference
from aitest.domain.execution.reuse import WholeCaseReuseBasis
from aitest.domain.review.reports import (
    ReportContext,
)

#: 一期尚无带版本的判定策略来源；与 `DecisionContextFacts` 默认值一致。
#: 一期动作表里本模块接管的动作。
_CONTEXT_FIELDS = tuple(field.name for field in fields(ReportContext))




# ------------------------------------------------------------------ 编解码


class SavedReportService(ReportCreationMixin):
    """报告修订与本地复核的生产用例：权威读取、领域冻结、同一工作单元持久化。"""

    def __init__(
        self,
        *,
        unit: ReportUnitOfWork,
        reader: RecordReader,
        records: ReportRecordRepository,
        clock: ReportClock,
        actors: ReportActorReader,
        runtime: ReportRuntimeFactsReader,
        workspace_id: str,
        basis_proof: object | None = None,
        case_basis: Any | None = None,
        reuse_references: Callable[[str, str], tuple[ReuseReference, ...]] | None = None,
        reuse_basis: Callable[
            [ReuseReference, ExecutionFacts, ExecutionFacts], WholeCaseReuseBasis | None
        ]
        | None = None,
        objects: EvidenceObjectStore | None = None,
    ) -> None:
        for name, value in (
            ("unit", unit),
            ("reader", reader),
            ("records", records),
            ("clock", clock),
            ("actors", actors),
            ("runtime", runtime),
        ):
            if value is None:
                raise ReportManagementError(
                    f"report use cases require the {name} capability",
                    code="CAPABILITY_UNAVAILABLE",
                )
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ReportManagementError("report use cases require a workspace identity")
        self._unit, self._reader, self._records = unit, reader, records
        self._clock, self._actors, self._runtime = clock, actors, runtime
        self._workspace_id = workspace_id
        self._bases = SavedCaseBasisCheck(
            reader=reader,
            records=records,
            workspace_id=workspace_id,
            basis_proof=basis_proof,
            override=case_basis,
        )
        #: 整用例复用候选来源（`(project_id, run_id) -> 已保存引用`）。选择动作
        #: 已在默认装配接入；缺端口的组件实例仍用 `None` 并明确登记未接，不伪造空结论。
        self._reuse_references = reuse_references
        self._reuse_basis = reuse_basis
        self._objects = objects

    # ------------------------------------------------------------ 动作入口

    def create_report(self, command: object) -> Mapping[str, object]:
        return self._create_report(command)

    def record_local_review(self, command: object) -> Mapping[str, object]:
        return self._record_local_review(command)

    def aggregate_project_runs(self, command: object) -> Mapping[str, object]:
        """Read-only project aggregation; runs are listed, never merged.

        Each run keeps its own environment reference, plan revision and
        coverage counts. Acceptance-item rows repeat the per-run figures, so a
        multi-interpreter project never looks like one combined execution.
        """

        project_id = _text(getattr(command, "project_id", None), "project_id")
        parameters = getattr(command, "parameters", None) or {}
        if not isinstance(parameters, Mapping) or parameters:
            raise ReportManagementError(
                "aggregate_project_runs accepts no parameters"
            )
        page = self._records.query(RecordQuery(
            project_id=project_id,
            aggregate_kind=cast("Any", "report"),
            limit=500,
            sort="published_sequence",
            descending=True,
        ))
        latest: dict[str, Mapping[str, object]] = {}
        for record in getattr(page, "items", ()):
            payload = record.payload
            context = payload.get("context")
            if not isinstance(context, Mapping):
                continue
            run_id = context.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                continue
            previous = latest.get(run_id)
            if previous is None or _sequence_number(payload) > _sequence_number(previous):
                latest[run_id] = payload

        runs: list[dict[str, object]] = []
        acceptance: dict[str, list[dict[str, object]]] = {}
        unmapped_cases: set[str] = set()
        for run_id in sorted(latest):
            payload = latest[run_id]
            raw_context = payload.get("context")
            context = raw_context if isinstance(raw_context, Mapping) else {}
            decision = payload.get("decision")
            coverage = decision.get("coverage") if isinstance(decision, Mapping) else None
            if not isinstance(coverage, Mapping):
                coverage = {}
            selected = set(coverage.get("selected", ()))
            executed = set(coverage.get("executed", ()))
            verified = set(coverage.get("verified", ()))
            passed = set(coverage.get("passed", ()))
            runs.append({
                "run_id": run_id,
                "report_id": payload.get("report_id"),
                "report_revision": payload.get("content_revision"),
                "environment_ref": context.get("environment_ref"),
                "plan_revision_refs": list(context.get("plan_revision_refs", ())),
                "business_outcome": decision.get("business_outcome")
                if isinstance(decision, Mapping) else None,
                "evidence_grade": decision.get("evidence_grade")
                if isinstance(decision, Mapping) else None,
                "selected_count": len(selected),
                "executed_count": len(executed),
                "verified_count": len(verified),
                "passed_count": len(passed),
                "pytest_failure_count": _sequence_len(payload.get("pytest_failures")),
            })
            case_items, missing = self._acceptance_items_for_report(payload)
            unmapped_cases.update(missing)
            by_item: dict[str, set[str]] = {}
            for case_id, item_ids in case_items.items():
                for item_id in item_ids:
                    by_item.setdefault(item_id, set()).add(case_id)
            for item_id in sorted(by_item):
                cases = by_item[item_id]
                acceptance.setdefault(item_id, []).append({
                    "run_id": run_id,
                    "environment_ref": context.get("environment_ref"),
                    "selected_count": len(cases & selected),
                    "executed_count": len(cases & executed),
                    "verified_count": len(cases & verified),
                    "passed_count": len(cases & passed),
                })
        return {
            "project_id": project_id,
            "runs": runs,
            "acceptance_items": [
                {"acceptance_item_id": item_id, "runs": acceptance[item_id]}
                for item_id in sorted(acceptance)
            ],
            "unmapped_case_ids": sorted(unmapped_cases),
            "note": (
                "Each run keeps its own environment and plan revision; "
                "counts are not merged across runs."
            ),
        }

    # ------------------------------------------------------------ 报告修订

    # ------------------------------------------------------------ 本地复核

    def _record_local_review(self, command: object) -> Mapping[str, object]:
        project_id, intent_id, request_id, expected = _command_identity(command)
        parameters = _command_parameters(command)
        unknown = set(parameters) - {
            "review_id",
            "report_id",
            "conclusion",
            "explanation",
            "report_revision",
        }
        if unknown:
            raise ReportManagementError(
                f"record_local_review received unknown parameters: {sorted(unknown)}"
            )
        review_id = _text(parameters.get("review_id"), "review_id")
        report_id = _text(parameters.get("report_id"), "report_id")
        conclusion = _text(parameters.get("conclusion"), "conclusion")
        explanation = _text(parameters.get("explanation"), "explanation")
        if expected != 0:
            raise ReportRevisionConflict(expected=expected, current=0)

        fingerprint = _digest(
            ["record_local_review", review_id, report_id, conclusion, explanation]
        )
        intent_record_id = "report-intent-" + _digest([project_id, intent_id])[7:]
        recalled = self._recall(
            project_id, intent_record_id, "record_local_review", review_id, fingerprint
        )
        if recalled is not None:
            return recalled

        current = self._current_report(project_id, report_id)
        if current is None:
            raise ReportNotSaved(f"report {report_id} is not saved")
        snapshot = report_from_payload(current["payload"])
        pinned = parameters.get("report_revision")
        if pinned is not None and pinned != snapshot.content_revision:
            raise ReportRevisionConflict(
                expected=cast(int, pinned), current=snapshot.content_revision
            )

        review = facade.record_local_review(
            snapshot,
            review_id=review_id,
            reviewer=self._actor(),
            conclusion=conclusion,
            explanation=explanation,
            report_revision=snapshot.content_revision,
            recorded_at=self._clock.now(),
        )
        sequence = self._next_sequence()
        payload = local_review_to_payload(review)
        payload["published_sequence"] = sequence
        return self._commit(
            action="record_local_review",
            project_id=project_id,
            record_id=review_id,
            intent_id=intent_id,
            request_id=request_id,
            intent_record_id=intent_record_id,
            fingerprint=fingerprint,
            aggregate_kind=LOCAL_REVIEW_AGGREGATE,
            payload=payload,
            expected_revision=0,
            revision=1,
        )

    # ------------------------------------------------------------ 权威读取

    def _facts(self, project_id: str, run_id: str) -> ExecutionFacts:
        try:
            facts = self._runtime.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ReportGuardRefused(f"saved run facts are unavailable: {error}") from error
        if not isinstance(facts, ExecutionFacts):
            raise ReportGuardRefused("saved run facts are unreadable")
        if facts.project_id != project_id:
            raise ReportGuardRefused("saved run facts belong to another project")
        if facts.run_revision < 1:
            raise ReportGuardRefused("saved run revision cannot be verified")
        return facts

    def _prepared(self, project_id: str, run_intent_id: str) -> Any:
        receipt = self._reader.find_preparation_by_intent(intent_id=run_intent_id)
        if receipt is None or getattr(receipt, "cancelled", False):
            raise ReportGuardRefused("the saved preparation receipt is unavailable")
        request = getattr(receipt, "request", None)
        if request is None:
            raise ReportGuardRefused("the saved preparation receipt is unreadable")
        prepared_run_id = "prepared-" + preparation_identity_digest(
            project_id=project_id,
            client_id=request.client_id,
            prepare_request_id=request.prepare_request_id,
        )
        try:
            prepared, _digest_value = load_saved_preparation(
                reader=self._reader,
                project_id=project_id,
                workspace_id=self._workspace_id,
                prepared_run_id=prepared_run_id,
            )
            validate_preparation_origin(prepared, reader=self._reader)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ReportGuardRefused(f"saved preparation cannot be verified: {error}") from error
        return prepared

    def _scope(self, project_id: str, prepared: Any) -> Any:
        if not prepared.scope_id:
            raise ReportGuardRefused("the saved preparation has no acceptance scope reference")
        try:
            record = self._reader.read(
                aggregate_kind="acceptance_scope",
                record_id=prepared.scope_id,
                revision=prepared.acceptance_scope_revision,
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ReportGuardRefused(
                f"the frozen acceptance scope revision is unavailable: {error}"
            ) from error
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping) or payload.get("project_id") != project_id:
            raise ReportGuardRefused("the frozen acceptance scope owner cannot be verified")
        try:
            scope = acceptance_scope_from_payload(payload)
        except ValueError as error:
            raise ReportGuardRefused(
                f"the frozen acceptance scope is unreadable: {error}"
            ) from error
        if (
            scope.scope_id != prepared.scope_id
            or scope.revision != prepared.acceptance_scope_revision
        ):
            raise ReportGuardRefused("the frozen acceptance scope identity differs")
        return scope

    def _current_report(self, project_id: str, report_id: str) -> dict[str, Any] | None:
        payload = self._current(REPORT_AGGREGATE, report_id)
        if payload is None:
            return None
        if payload.get("project_id") != project_id:
            raise ReportNotSaved(f"report {report_id} belongs to another project")
        revision = payload.get("content_revision")
        if type(revision) is not int or revision < 1:
            raise ReportManagementError("saved report content revision cannot be verified")
        return {"payload": payload, "content_revision": revision}

    def _current(self, aggregate_kind: str, record_id: str) -> dict[str, Any] | None:
        revision = self._records.current_revision(
            aggregate_kind=aggregate_kind, record_id=record_id
        )
        if type(revision) is not int or revision < 0:
            raise ReportManagementError("warehouse revision cannot be verified")
        if revision == 0:
            return None
        record = self._records.read(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
        )
        if (
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (aggregate_kind, record_id, revision):
            raise ReportManagementError("saved record envelope cannot be verified")
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping):
            raise ReportManagementError("saved record payload is unreadable")
        return dict(payload)

    def _actor(self) -> str:
        try:
            actor = self._actors.current()
        except (LookupError, RuntimeError, ValueError) as error:
            raise ReportActorRequired(
                "local review requires a controlled interactive session"
            ) from error
        session_id = getattr(actor, "session_id", None)
        entry_kind = getattr(actor, "entry_kind", None)
        if getattr(entry_kind, "value", None) == "agent_relay" or not getattr(
            actor, "interactive", False
        ):
            raise ReportActorRequired("local review cannot come from an AI relay session")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ReportActorRequired("local review requires a session identity")
        return f"session:{session_id}"

    # ------------------------------------------------------------ 提交与幂等

    def _next_sequence(self) -> int:
        try:
            return int(self._unit.next_commit_seq())
        except (TypeError, ValueError) as error:
            raise ReportManagementError("commit sequence cannot be verified") from error

    def _recall(
        self,
        project_id: str,
        intent_record_id: str,
        action: str,
        record_id: str,
        fingerprint: str,
    ) -> Mapping[str, object] | None:
        payload = self._current(REPORT_INTENT_AGGREGATE, intent_record_id)
        if payload is None:
            return None
        if (
            payload.get("schema_version") != REPORT_INTENT_SCHEMA
            or payload.get("project_id") != project_id
            or payload.get("action") != action
            or payload.get("record_id") != record_id
        ):
            raise ReportIntentConflict("report intent belongs to different inputs")
        if payload.get("input_digest") != fingerprint:
            raise ReportIntentConflict("report intent has different frozen input")
        result = payload.get("result")
        if not isinstance(result, Mapping) or set(result) != {
            "aggregate_kind",
            "record_id",
            "revision",
        }:
            raise ReportManagementError("saved report intent result is unreadable")
        return dict(result)

    def _commit(
        self,
        *,
        action: str,
        project_id: str,
        record_id: str,
        intent_id: str,
        request_id: str,
        intent_record_id: str,
        fingerprint: str,
        aggregate_kind: str,
        payload: dict[str, object],
        expected_revision: int,
        revision: int,
        source_sequence: int | None = None,
    ) -> Mapping[str, object]:
        result = {
            "aggregate_kind": aggregate_kind,
            "record_id": record_id,
            "revision": revision,
        }
        self._unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            if source_sequence is not None and self._next_sequence() != source_sequence:
                raise ReportGuardRefused("decision inputs changed before report publication")
            staged = self._unit.stage_record(
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                expected_revision=expected_revision,
                payload=payload,
            )
            staged_revision = getattr(staged, "revision", staged)
            if type(staged_revision) is not int or staged_revision != revision:
                raise ReportManagementError("staged report revision cannot be verified")
            self._unit.stage_record(
                aggregate_kind=REPORT_INTENT_AGGREGATE,
                record_id=intent_record_id,
                expected_revision=0,
                payload={
                    "schema_version": REPORT_INTENT_SCHEMA,
                    "project_id": project_id,
                    "intent_id": intent_id,
                    "action": action,
                    "record_id": record_id,
                    "input_digest": fingerprint,
                    "result": dict(result),
                },
            )
            self._unit.commit(request_id)
        except BaseException:
            self._unit.rollback(request_id)
            raise
        return dict(result)


__all__ = [
    "DEFAULT_POLICY_VERSION",
    "LOCAL_REVIEW_AGGREGATE",
    "LOCAL_REVIEW_SCHEMA",
    "REPORT_AGGREGATE",
    "REPORT_INTENT_AGGREGATE",
    "REPORT_SCHEMA",
    "ReportActorRequired",
    "ReportGuardRefused",
    "ReportIntentConflict",
    "ReportManagementError",
    "ReportNotSaved",
    "ReportRevisionConflict",
    "SUPPORTED_ACTIONS",
    "SavedReportService",
    "local_review_from_payload",
    "local_review_to_payload",
    "report_from_payload",
    "report_to_payload",
]
