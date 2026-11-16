"""Report creation/aggregation pipeline extracted from the saved-report service."""

# mypy: disable-error-code="misc,attr-defined,no-any-return"

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from aitest.application.evidence.pytest_failures import (
    collect_pytest_failures,
    compare_failure_nodes,
)
from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.planning.serialization import (
    plan_coverage_contract_from_payload,
)
from aitest.application.ports import AggregateKind, RecordQuery
from aitest.application.review.execution_decision_adapter import (
    AssertionOutcomeFact,
    assertion_outcomes_from_facts,
    build_decision_facts,
)
from aitest.application.review.function_coverage_run import build_run_function_coverage
from aitest.application.review.report_codec import (
    _command_identity,
    _command_parameters,
    _digest,
    _text,
    _text_list,
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
from aitest.application.review.report_types import (
    DEFAULT_POLICY_VERSION,
    REPORT_AGGREGATE,
    ReportGuardRefused,
    ReportManagementError,
    ReportRevisionConflict,
    _LiveDecision,
    _ReuseReading,
)
from aitest.application.review.reuse_consumption import ReuseOutcome, derive_reuse_outcome
from aitest.application.review.saved_defects import (
    ISSUE_AGGREGATE,
    blocking_state,
    issue_from_payload,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.review.defects import IssueRecord, resolve_canonical_issue_id
from aitest.domain.review.function_coverage import FunctionCoverage
from aitest.domain.review.reports import (
    DecisionResult,
    ReportContext,
    ReportDraft,
    create_report_revision,
)

#: 一期尚无带版本的判定策略来源；与 `DecisionContextFacts` 默认值一致。
#: 一期动作表里本模块接管的动作。


class ReportCreationMixin:
    def _read_planning_record(
        self, kind: str, record_id: str, revision: int,
    ) -> Any:
        """Read a frozen plan/case from the warehouse or the planning reader."""

        try:
            return self._records.read(
                aggregate_kind=cast("Any", kind),
                record_id=record_id,
                revision=revision,
            )
        except (OSError, KeyError, TypeError, ValueError):
            return self._reader.read(
                aggregate_kind=cast("Any", kind),
                record_id=record_id,
                revision=revision,
            )


    def _acceptance_items_for_report(
        self, payload: Mapping[str, object],
    ) -> tuple[dict[str, set[str]], set[str]]:
        """Map report-selected cases to acceptance items via the frozen plan."""

        context = payload.get("context")
        refs = context.get("plan_revision_refs") if isinstance(context, Mapping) else None
        if not isinstance(refs, (list, tuple)) or not refs:
            return {}, set()
        reference = str(refs[0])
        plan_id, separator, raw_revision = reference.partition("@")
        if not separator or not plan_id or not raw_revision.isdigit():
            return {}, set()
        try:
            plan = self._read_planning_record("plan", plan_id, int(raw_revision)).payload
        except (OSError, KeyError, TypeError, ValueError):
            return {}, set()
        case_refs = plan.get("case_revisions")
        if not isinstance(case_refs, list):
            return {}, set()
        mapping: dict[str, set[str]] = {}
        missing: set[str] = set()
        for item in case_refs:
            if not isinstance(item, Mapping):
                continue
            case_id = item.get("case_id")
            revision = item.get("revision")
            if not isinstance(case_id, str) or type(revision) is not int:
                continue
            try:
                case = self._read_planning_record(
                    "case", case_id, revision
                ).payload
            except (OSError, KeyError, TypeError, ValueError):
                missing.add(case_id)
                continue
            links = case.get("links")
            item_ids = links.get("acceptance_item_ids") if isinstance(links, Mapping) else None
            if not isinstance(item_ids, (list, tuple)) or not item_ids:
                missing.add(case_id)
                continue
            mapping[case_id] = {str(value) for value in item_ids if str(value).strip()}
        return mapping, missing


    def live_decision(
        self, project_id: str, run_id: str, *, issue_ids: tuple[str, ...] = ()
    ) -> tuple[ExecutionFacts, DecisionResult]:
        """实时判定（只读）：**唯一**装配点，报告与面板 DTO 共用同一次领域判定。

        架构 06 §1/§5 要求运行 DTO 与报告用同一领域判定（等级、覆盖、缺口只能在领域算一次），
        因此把 `create_report` 里的判定装配提成公开方法：它读已提交事实 + 冻结准备 + 范围 +
        问题阻塞 + 依据确认 + R 消费，再调 `evaluate_review`，**不写任何记录**。
        """

        live = self._live(project_id, run_id, issue_ids)
        return live.facts, live.decision


    def _live(self, project_id: str, run_id: str, issue_ids: tuple[str, ...]) -> _LiveDecision:
        source_sequence = self._next_sequence()
        facts = self._facts(project_id, run_id)
        prepared = self._prepared(project_id, facts.run.intent_id)
        scope = self._scope(project_id, prepared)
        issues = self._issues(
            project_id,
            issue_ids,
            selected_cases=frozenset(facts.run.selected_scope),
            selected_modules=frozenset(
                module
                for case in prepared.frozen_cases
                if case.case_id in facts.run.selected_scope
                for module in case.links.module_ids
            ),
        )
        graph = {issue.issue_id: issue for issue in issues}
        blocking_ids: set[str] = set()
        for issue in issues:
            root_id = resolve_canonical_issue_id(issue.issue_id, graph)
            if blocking_state(issue, root=graph[root_id], issues=graph) != "non_blocking":
                blocking_ids.add(root_id)
        blocking = frozenset(blocking_ids)
        confirmed_cases = frozenset(
            case.case_id
            for case in prepared.frozen_cases
            if self._bases.for_facts(facts, case.case_id)
        )
        critical_chain = frozenset(
            item for case in prepared.frozen_cases for item in case.links.critical_path_ids
        )
        reuse = self._reuse_outcome(facts)
        outcomes = assertion_outcomes_from_facts(
            facts,
            basis_revision_for=lambda case_id: self._bases.revision(
                project_id, facts.run.intent_id, case_id, facts=facts
            ),
            basis_confirmed_for=lambda case_id: case_id in confirmed_cases,
        )
        decision = self._decision(
            facts,
            scope_template_cases=frozenset(scope.template_case_ids),
            confirmed_cases=confirmed_cases,
            blocking_issue_ids=blocking,
            critical_chain=critical_chain,
            environment_complete=prepared.environment.resolution is not None,
            reuse=reuse.outcome,
            assertion_outcomes=outcomes,
        )
        if self._next_sequence() != source_sequence:
            raise ReportGuardRefused("decision inputs changed while reading the committed boundary")
        return _LiveDecision(
            facts=facts,
            prepared=prepared,
            scope=scope,
            issues=issues,
            reuse=reuse,
            decision=decision,
            source_sequence=source_sequence,
            blocking_issue_ids=blocking,
        )


    def _run_function_coverage(
        self, facts: ExecutionFacts, decision: DecisionResult
    ) -> tuple[FunctionCoverage, ...]:
        """从运行冻结的计划契约生成报告用的功能×维度收据。

        没有契约、计划记录不可读或契约损坏时返回空元组；报告主体仍可生成，
        但不会伪造一份“已验证”的功能覆盖。
        """
        try:
            ref = facts.plan_revision
            record = self._reader.read(
                aggregate_kind="plan",
                record_id=ref.revision_id,
                revision=ref.revision_no,
            )
            payload = getattr(record, "payload", None)
            if not isinstance(payload, Mapping):
                return ()
            contract = plan_coverage_contract_from_payload(payload)
            if contract is None:
                return ()
            plan_revision = payload.get("revision")
            if type(plan_revision) is not int or plan_revision < 1:
                return ()
            return build_run_function_coverage(
                contract,
                coverage=decision.coverage,
                plan_revision=plan_revision,
                run_id=facts.run_id,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return ()


    def _create_report(self, command: object) -> Mapping[str, object]:
        project_id, intent_id, request_id, expected = _command_identity(command)
        parameters = _command_parameters(command)
        unknown = set(parameters) - {"report_id", "run_id", "issue_ids"}
        if unknown:
            raise ReportManagementError(
                f"create_report received unknown parameters: {sorted(unknown)}"
            )
        report_id = _text(parameters.get("report_id"), "report_id")
        run_id = _text(parameters.get("run_id"), "run_id")
        issue_ids = _text_list(parameters.get("issue_ids"), "issue_ids")

        fingerprint = _digest(["create_report", report_id, expected, run_id, list(issue_ids)])
        intent_record_id = "report-intent-" + _digest([project_id, intent_id])[7:]
        recalled = self._recall(
            project_id, intent_record_id, "create_report", report_id, fingerprint
        )
        if recalled is not None:
            return recalled

        current = self._current_report(project_id, report_id)
        current_revision = 0 if current is None else cast(int, current["content_revision"])
        if expected != current_revision:
            raise ReportRevisionConflict(expected=expected, current=current_revision)

        live = self._live(project_id, run_id, issue_ids)
        facts, decision, prepared, scope = live.facts, live.decision, live.prepared, live.scope
        issues, reuse = live.issues, live.reuse
        function_coverage = self._run_function_coverage(facts, decision)
        if decision.policy_version != DEFAULT_POLICY_VERSION:
            raise ReportGuardRefused("saved decision policy version cannot be verified")
        context = ReportContext(
            project_id=project_id,
            run_id=run_id,
            run_revision=facts.run_revision,
            plan_revision_refs=self._plan_refs(facts),
            scope_revision=f"scope:{scope.scope_id}@{scope.revision}",
            scope_name=scope.name,
            source_identity=prepared.snapshot.content_identity,
            environment_ref=facts.run.environment_ref,
            rules_revision=facts.run.rules_revision,
            acceptance_revision=str(prepared.acceptance_scope_revision),
            template_revision=self._template_revision(prepared),
            policy_version=DEFAULT_POLICY_VERSION,
        )
        previous = None if current is None else report_from_payload(current["payload"])
        evidence = self._report_evidence(facts, reuse)
        snapshot = create_report_revision(
            previous,
            ReportDraft(
                report_id=report_id,
                context=context,
                decision=decision,
                evidence_refs=tuple(sorted(evidence)),
                evidence_revisions=dict(evidence),
                issue_revisions=tuple(sorted((issue.issue_id, issue.revision) for issue in issues)),
            ),
            created_at=self._clock.now(),
        )
        sequence = self._next_sequence()
        payload = report_to_payload(
            snapshot,
            published_sequence=sequence,
            reuse=reuse.payload(),
            execution_snapshot_ref=SnapshotContentRef.of(facts),
            blocking_issue_ids=live.blocking_issue_ids,
            function_coverage=function_coverage,
            pytest_failures=self._pytest_failures(facts),
            pytest_baseline=self._pytest_baseline(
                project_id, facts, tuple(sorted(decision.coverage.selected))
            ),
            layers=self._layer_counts(facts),
        )
        return self._commit(
            action="create_report",
            project_id=project_id,
            record_id=report_id,
            intent_id=intent_id,
            request_id=request_id,
            intent_record_id=intent_record_id,
            fingerprint=fingerprint,
            aggregate_kind=REPORT_AGGREGATE,
            payload=payload,
            expected_revision=current_revision,
            revision=snapshot.content_revision,
            source_sequence=live.source_sequence,
        )


    def _layer_counts(self, facts: ExecutionFacts) -> list[dict[str, object]]:
        """Freeze L1/L2/L3 step counts in the report; never substitute one layer."""

        counts: dict[str, dict[str, int]] = {}
        for step in facts.steps:
            level = step.level.value
            state = step.state.value
            entry = counts.setdefault(level, {
                "total": 0, "completed": 0, "blocked": 0, "failed": 0,
            })
            entry["total"] += 1
            if state in entry:
                entry[state] += 1
        return [
            {"level": level, **counts[level]}
            for level in sorted(counts)
        ]


    def _pytest_failures(self, facts: ExecutionFacts) -> list[dict[str, object]]:
        return [
            {
                "node_id": item.node_id,
                "failure_type": item.failure_type,
                "message": item.message,
                "file": item.file,
                "line": item.line,
                "suspect_file": item.suspect_file,
                "suspect_line": item.suspect_line,
                "location_unknown": item.location_unknown,
                "stack": item.stack,
            }
            for item in collect_pytest_failures(facts, self._objects)
        ]


    def _pytest_baseline(
        self, project_id: str, facts: ExecutionFacts, selected_case_ids: tuple[str, ...],
    ) -> dict[str, object] | None:
        """Same-scope failure comparison against the latest saved report.

        Only an actually saved report is a baseline. The report stores fixed
        input facts; it is not a closure or pass claim.
        """

        query = getattr(self._records, "query", None)
        if not callable(query) or self._objects is None:
            return None
        page = query(RecordQuery(
            project_id=project_id, aggregate_kind=cast("Any", "report"), limit=100,
            sort="published_sequence", descending=True,
        ))
        items = getattr(page, "items", ())
        if not items:
            return None
        current = tuple(
            item.node_id for item in collect_pytest_failures(facts, self._objects)
        )
        selected = set(selected_case_ids)
        ordered = sorted(
            items,
            key=lambda record: int(record.payload.get("published_sequence", 0)),
            reverse=True,
        )
        for record in ordered:
            payload = record.payload
            context = payload.get("context")
            decision = payload.get("decision")
            coverage = decision.get("coverage") if isinstance(decision, Mapping) else None
            if not isinstance(context, Mapping) or not isinstance(coverage, Mapping):
                continue
            if set(coverage.get("selected", ())) != selected:
                continue
            if context.get("run_id") == facts.run_id:
                continue
            previous = payload.get("pytest_failures")
            if not isinstance(previous, list):
                return None
            previous_nodes = tuple(
                str(item["node_id"])
                for item in previous
                if isinstance(item, Mapping) and isinstance(item.get("node_id"), str)
            )
            compared = compare_failure_nodes(current, previous_nodes)
            return {
                "previous_run_id": str(context.get("run_id", "")),
                "previous_report_id": str(payload.get("report_id", "")),
                "previous_revision": int(payload.get("content_revision", 1)),
                "new_node_ids": list(compared.new_node_ids),
                "existing_node_ids": list(compared.existing_node_ids),
                "fixed_node_ids": list(compared.fixed_node_ids),
            }
        return None


    def _report_evidence(
        self, facts: ExecutionFacts, reuse: _ReuseReading
    ) -> Mapping[str, int]:
        evidence = {item.evidence_id: item for item in facts.evidence_refs}
        outcome = reuse.outcome
        if outcome is None or not outcome.reused:
            return {identity: evidence[identity].evidence_revision for identity in sorted(evidence)}
        if {item.case_id for item in outcome.references} != outcome.reused:
            raise ReportGuardRefused("qualified reuse lacks its exact saved references")
        sources = {item.run_id: item for item in outcome.source_snapshots}
        if len(sources) != len(outcome.source_snapshots):
            raise ReportGuardRefused("qualified reuse has conflicting source snapshots")
        for reference in outcome.references:
            source = sources.get(reference.source_run_id)
            if source is None or source.project_id != facts.project_id:
                raise ReportGuardRefused("qualified reuse source cannot be frozen")
            source_evidence = {item.evidence_id: item for item in source.evidence_refs}
            for identity in reference.evidence_refs:
                item = source_evidence.get(identity)
                if (
                    item is None
                    or item.project_id != facts.project_id
                    or item.run_id != source.run_id
                ):
                    raise ReportGuardRefused("qualified reuse evidence belongs elsewhere")
                previous = evidence.get(identity)
                if previous is not None and previous != item:
                    raise ReportGuardRefused("frozen evidence identity has conflicting sources")
                evidence[identity] = item
        return {identity: evidence[identity].evidence_revision for identity in sorted(evidence)}


    def _decision(
        self,
        facts: ExecutionFacts,
        *,
        scope_template_cases: frozenset[str],
        confirmed_cases: frozenset[str],
        blocking_issue_ids: frozenset[str],
        critical_chain: frozenset[str],
        environment_complete: bool,
        reuse: ReuseOutcome | None,
        assertion_outcomes: tuple[AssertionOutcomeFact, ...] = (),
    ) -> DecisionResult:
        """只用已提交事实求判定；断言结果由 `assertion_outcomes_from_facts` 从事实派生。"""

        from aitest.application.review.execution_decision_adapter import DecisionContextFacts
        from aitest.domain.review.reports import evaluate_review

        context = DecisionContextFacts(
            template_required_case_ids=scope_template_cases,
            assertion_basis_confirmed_case_ids=confirmed_cases,
            required_critical_chain_item_ids=critical_chain,
            environment_evidence_complete=environment_complete,
            blocking_issue_ids=blocking_issue_ids,
            policy_version=DEFAULT_POLICY_VERSION,
        )
        return evaluate_review(
            build_decision_facts(
                facts,
                assertion_outcomes=assertion_outcomes,
                context=context,
                reuse=reuse,
            )
        )


    def _reuse_outcome(self, facts: ExecutionFacts) -> _ReuseReading:
        """整用例复用（R）的消费：候选来源未接时显式记为"未接"。

        候选由 select_case_reuse 保存；来源未装配时显式记为未接。
        缺完整依据时拒绝 R，并给出具名原因；动态资格和来源材料不得猜测。
        """

        if self._reuse_references is None:
            return _ReuseReading(outcome=None, candidates=None)
        references = self._reuse_references(facts.project_id, facts.run_id)
        if not references:
            return _ReuseReading(outcome=ReuseOutcome(reused=frozenset()), candidates=0)
        return _ReuseReading(
            outcome=derive_reuse_outcome(
                facts,
                references,
                source_facts=lambda run_id: self._source_facts(facts.project_id, run_id),
                evidence_readable=self._evidence_saved,
                basis_confirmed=lambda case_id: self._bases.for_facts(facts, case_id),
                complete_basis=self._reuse_basis,
                basis_revision_for=lambda source, case: self._bases.revision(
                    source.project_id,
                    source.run.intent_id,
                    case,
                    facts=source,
                ),
            ),
            candidates=len(references),
        )


    def _source_facts(self, project_id: str, run_id: str) -> ExecutionFacts | None:
        try:
            return self._runtime.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        except (OSError, KeyError, TypeError, ValueError):
            return None


    def _evidence_saved(self, evidence_id: str) -> bool:
        revision = self._records.current_revision(
            aggregate_kind="evidence_ref", record_id=evidence_id
        )
        return type(revision) is int and revision >= 1


    def _plan_refs(self, facts: ExecutionFacts) -> tuple[str, ...]:
        refs = [
            f"{facts.plan_revision.revision_id}@{facts.plan_revision.revision_no}",
            *facts.runtime_revision_refs,
        ]
        if not refs:
            raise ReportGuardRefused("the saved run has no plan revision reference")
        return tuple(dict.fromkeys(refs))


    def _template_revision(self, prepared: Any) -> str:
        versions = [f"{item.template_id}@{item.version}" for item in prepared.template_versions]
        if not versions:
            raise ReportGuardRefused("the saved preparation has no frozen template version")
        return versions[0]


    def _issues(
        self,
        project_id: str,
        issue_ids: tuple[str, ...],
        *,
        selected_cases: frozenset[str] | None = None,
        selected_modules: frozenset[str] = frozenset(),
    ) -> tuple[IssueRecord, ...]:
        # The caller may request additional issues, but cannot subtract saved
        # blockers. Pages use the finite ALL issue index and its stable cursor.
        payloads: dict[str, Mapping[str, object]] = {}
        cursor = None
        seen: set[str] = set()
        while True:
            page = self._records.query(
                RecordQuery(
                    project_id=project_id,
                    aggregate_kind=cast(AggregateKind, ISSUE_AGGREGATE),
                    view="ALL",
                    sort="updated_sequence",
                    limit=100,
                    cursor=cursor,
                )
            )
            if getattr(page, "status", None) != "ok":
                if cursor is None and self._next_sequence() == 1:
                    break  # Authoritative empty workspace, before its first commit.
                raise ReportGuardRefused("saved issue scope cannot be read completely")
            for record in page.items:
                payloads[record.record_id] = record.payload
            cursor = page.next_cursor
            if cursor is None:
                break
            if cursor in seen:
                raise ReportGuardRefused("saved issue query repeated its cursor")
            seen.add(cursor)
        for issue_id in issue_ids:
            current = payloads.get(issue_id)
            if current is None:
                raise ReportGuardRefused(f"issue {issue_id} is not saved")
        records: dict[str, IssueRecord] = {}
        for issue_id, current in payloads.items():
            issue = issue_from_payload(current)
            if issue.project_id != project_id:
                raise ReportGuardRefused(f"issue {issue_id} belongs to another project")
            links = current.get("links")
            case_id = links.get("case_id") if isinstance(links, Mapping) else None
            modules = (
                set(links.get("module_ids", ())) - {"unknown"}
                if isinstance(links, Mapping)
                else set()
            )
            # Unknown scope is retained, never interpreted as unrelated.
            if (
                selected_cases is None
                or case_id in selected_cases
                or issue_id in issue_ids
                or not modules
                or not selected_modules
                or bool(modules & selected_modules)
            ):
                records[issue_id] = issue
        # Include exact canonical dependencies even when their own scope differs.
        for issue in tuple(records.values()):
            current_issue = issue
            chain: set[str] = set()
            while current_issue.canonical_issue_id is not None:
                root_id = current_issue.canonical_issue_id
                if root_id in chain or root_id not in payloads:
                    raise ReportGuardRefused("issue canonical dependency is missing or cyclic")
                chain.add(root_id)
                current_issue = issue_from_payload(payloads[root_id])
                records[root_id] = current_issue
        return tuple(records[key] for key in sorted(records))


