"""全链路演示：建项目 → 发布计划（领域门禁）→ prepare → `PreparedRun`。

这条链**在两个 Sprint 里都是卡点**（设计说明第 1.1 节）：

- Sprint 2 的产物是"可校验的 `PreparedRun`"；
- Sprint 6 的"交接一"要用**真实 `PreparedRun`** 替换 C 的夹具。

本测试证明：**在 A 的端口缺席的情况下，这条链已经能真实跑通**（用内存底座）。
它不能证明存储侧验收（崩溃恢复、写锁、备份闭包），那些仍待真实文件后端。
"""

from datetime import UTC, datetime

from aitest.application.planning.preparation import InputRevisions
from aitest.application.planning.prepare_run import PreparationInputs, prepare_run
from aitest.application.planning.substrate import RecordQuery
from aitest.contracts.prepared_run import (
    AssertionBasisEntry,
    AssertionBasisStateFact,
    BindingFormFact,
    CaseLinks,
    CaseRevisionRef,
    EnvironmentIsolationModeFact,
    EnvironmentRefFact,
    ExecutionSourceBinding,
    FrozenCase,
    FrozenCaseStep,
    PlanRevisionRef,
    PreparedRun,
    PreparedRunStatusFact,
    RuleVersionRef,
    RunDriverFact,
    RunTierFact,
    SnapshotRef,
    TemplateVersionRef,
)
from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseImportance,
    CaseLayer,
    Plan,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
    validate_plan_publication,
)
from aitest.domain.planning.plans import (
    CaseLink as DomainCaseLink,
)
from aitest.domain.planning.plans import (
    CaseRevisionRef as DomainCaseRevisionRef,
)
from aitest.domain.planning.plans import (
    RuleRevisionRef as DomainRuleRevisionRef,
)
from aitest.domain.planning.plans import (
    TemplateVersionRef as DomainTemplateVersionRef,
)
from aitest.domain.project.context import (
    BindingForm,
    LocalProject,
    LocalProjectBinding,
    Module,
)
from tests.support.memory_substrate import (
    FixedClock,
    MemoryReader,
    MemoryStore,
    MemoryUnitOfWork,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _build_project_and_binding() -> tuple[LocalProject, LocalProjectBinding]:
    module = Module(
        module_id="module-1",
        project_id="project-1",
        name="ticket core",
        responsibility="create and read tickets",
        interface_note="HTTP handlers",
        inputs=("ticket payload",),
        outputs=("ticket id",),
    )
    project = LocalProject(
        local_project_id="project-1",
        workspace_id="ws-1",
        name="ticket service",
        goal="verify ticket creation",
        created_at_commit="commit-1",
        modules=(module,),
    )
    binding = LocalProjectBinding(
        binding_id="binding-1",
        binding_revision=1,
        project_id="project-1",
        canonical_path=r"C:\work\ticket",
        binding_form=BindingForm.GIT,
        repository_id="origin",
        branch="main",
        base_commit="9c44344bcd612df1a7d033efa1e7a47c810c49cf",
        local_owner="feix-a",
    )
    return project, binding


def _build_case() -> Case:
    return Case(
        case_id="case-1",
        revision=1,
        layer=CaseLayer.L2,
        objective="create a ticket through the registered entry",
        preconditions=("the service is running locally",),
        inputs=("ticket payload",),
        steps=("create a ticket", "read the ticket back independently"),
        expected="the persisted ticket matches the submission",
        verification_method="registered pytest entry",
        links=DomainCaseLink(
            acceptance_item_ids=frozenset({"ai-1"}),
            module_ids=frozenset({"module-1"}),
            environment_ids=frozenset({"env-1"}),
            critical_path_ids=frozenset({"path-1"}),
        ),
        assertion_basis=AssertionBasis(
            revision=1,
            state=AssertionBasisState.PRESENT_UNCONFIRMED,
            text="independent read-back proves persistence",
            text_digest="sha256:basis-1",
        ),
        independent_verification="read-only query against the same ticket ID",
        importance=CaseImportance.P0,
    )


def _build_plan(case: Case) -> Plan:
    scope = AcceptanceScope(
        scope_id="scope-1",
        revision=1,
        name="ticket creation scope",
        required_case_ids=frozenset({case.case_id}),
        template_case_ids=frozenset({case.case_id}),
        objective="prove ticket creation is persisted",
    )
    return Plan(
        plan_id="plan-1",
        revision=1,
        scope=scope,
        case_revisions=(
            DomainCaseRevisionRef(
                case_id=case.case_id, revision=case.revision, digest="sha256:case-1"
            ),
        ),
        rule_revisions=(
            DomainRuleRevisionRef(rule_id="rule-1", revision=1, digest="sha256:rule-1"),
        ),
        template_versions=(
            DomainTemplateVersionRef(
                template_id="ticket-workflow", version="1.0.0", digest="sha256:tpl-1"
            ),
        ),
        run_tier=RunTier.FULL,
        initial_driver=RunDriver.PLANNED,
        status=PlanPublicationStatus.PUBLISHED,
        confirmation_id="confirmation-1",
    )


def _inputs_from(plan: Plan, case: Case) -> PreparationInputs:
    return PreparationInputs(
        project_id="project-1",
        workspace_id="ws-1",
        binding_id="binding-1",
        binding_revision=3,
        binding_form=BindingFormFact.GIT,
        client_id="client-1",
        prepare_request_id="req-1",
        input_revisions=InputRevisions(
            project_revision=1,
            binding_revision=1,
            snapshot_revision=1,
            environment_revision=1,
            plan_revision=plan.revision,
            rules_revision=1,
            template_revision=1,
            scope_revision=plan.scope.revision,
        ),
        snapshot=SnapshotRef(
            source_snapshot_id="snap-1",
            purpose="prepare",
            content_identity="sha256:content-1",
        ),
        selected_paths=("src/ticket",),
        exclusion_rules=("**/__pycache__/**",),
        environment=EnvironmentRefFact(
            environment_id="env-1",
            revision=1,
            isolation_mode=EnvironmentIsolationModeFact.VENV,
            interpreter_identity="cpython-3.13.3-windows-amd64",
            dependency_set_digest="sha256:deps-1",
        ),
        execution_source=ExecutionSourceBinding(
            registered_entry="python -m pytest",
            entry_arguments=("tests/acceptance",),
            cwd_mapping="<project-root> -> workdirs/run-1",
            test_config_ref="pyproject.toml#tool.pytest",
            resolved_input_digest="sha256:resolved-1",
        ),
        plan_revision=PlanRevisionRef(
            revision_id=plan.plan_id,
            revision_no=plan.record_revision or plan.revision,
            digest="sha256:plan-1",
        ),
        acceptance_scope_revision=plan.scope.revision,
        scope_id=plan.scope.scope_id,
        rule_versions=(RuleVersionRef(rule_id="rule-1", revision=1, digest="sha256:rule-1"),),
        template_versions=(
            TemplateVersionRef(
                template_id="ticket-workflow", version="1.0.0", digest="sha256:tpl-1"
            ),
        ),
        case_revisions=(
            CaseRevisionRef(case_id=case.case_id, revision=case.revision, digest="sha256:case-1"),
        ),
        frozen_cases=(
            FrozenCase(
                case_id=case.case_id,
                revision=case.revision,
                layer="L2",
                required=True,
                independent_verification=case.independent_verification or "",
                importance=case.importance.value,
                steps=tuple(
                    FrozenCaseStep(
                        step_id=f"step-{index}",
                        layer="L2",
                        objective=step,
                        expected=case.expected,
                    )
                    for index, step in enumerate(case.steps, start=1)
                ),
                links=CaseLinks(
                    acceptance_item_ids=("ai-1",),
                    module_ids=("module-1",),
                    environment_ids=("env-1",),
                    critical_path_ids=("path-1",),
                ),
            ),
        ),
        assertion_bases=(
            AssertionBasisEntry(
                case_id=case.case_id,
                basis_revision=1,
                basis_text_digest="sha256:basis-1",
                assertion_basis_state=AssertionBasisStateFact.PRESENT_UNCONFIRMED,
            ),
        ),
        git_base_commit="9c44344bcd612df1a7d033efa1e7a47c810c49cf",
        git_diff_digest="sha256:diff-1",
        run_tier=RunTierFact.FULL,
        initial_driver=RunDriverFact.PLANNED,
        template_required_case_ids=(case.case_id,),
        frozen_required_case_ids=(case.case_id,),
        selected_case_ids=(case.case_id,),
    )


def test_full_chain_produces_a_contract_valid_prepared_run() -> None:
    store = MemoryStore()
    unit_of_work = MemoryUnitOfWork(store)
    reader = MemoryReader(store)
    clock = FixedClock(NOW)

    project, binding = _build_project_and_binding()

    # 1) 建立项目与绑定。
    #    不变量 4 禁止同一事务内重复暂存同一记录，因此每个修订一个短事务。
    unit_of_work.open(project.project_id)
    unit_of_work.stage_record(
        aggregate_kind="project",
        record_id=project.project_id,
        expected_revision=None,
        payload={"project_id": project.project_id, "name": project.name},
    )
    assert unit_of_work.commit().commit_seq == "commit-1"

    unit_of_work.open(project.project_id)
    unit_of_work.stage_record(
        aggregate_kind="project",
        record_id=project.project_id,
        expected_revision=1,
        payload={"project_id": project.project_id, "name": project.name},
    )
    assert unit_of_work.commit().commit_seq == "commit-2"

    unit_of_work.open(project.project_id)
    staged_project = unit_of_work.stage_record(
        aggregate_kind="project",
        record_id=project.project_id,
        expected_revision=2,
        payload={"project_id": project.project_id, "name": project.name},
    )
    unit_of_work.stage_record(
        aggregate_kind="binding",
        record_id=binding.binding_id,
        expected_revision=None,
        payload={"project_id": project.project_id, "binding_form": "git"},
    )
    committed = unit_of_work.commit()
    # 修订号来自底座的实际状态，不是测试里猜的。
    assert staged_project.revision == 3
    assert committed.revision_of("binding", binding.binding_id).revision == 1
    assert committed.commit_seq == "commit-3"

    # 2) 发布计划：走真实的领域发布门禁。
    case = _build_case()
    plan = _build_plan(case)
    validate_plan_publication(plan, [case])

    # 3) prepare：产出真实 PreparedRun。
    prepared = prepare_run(
        _inputs_from(plan, case),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
    )

    assert isinstance(prepared, PreparedRun)
    assert prepared.status is PreparedRunStatusFact.PREPARED
    assert prepared.project_id == project.project_id
    assert prepared.project_id == project.local_project_id
    # 绑定修订由调用方给出（它必须先读到实际修订）。
    # **已知缺口**：`prepare_run` 尚未从底座读回实际修订来核对这个值，
    # 那是 A 的记录仓储该提供的读入口，已记入设计说明第 7 节"未验证"。
    assert prepared.binding_revision == 3
    assert prepared.plan_revision.revision_no == plan.revision
    assert prepared.acceptance_scope_revision == plan.scope.revision
    assert prepared.case_revisions[0].case_id == case.case_id
    assert prepared.created_at == NOW
    assert prepared.created_at_commit == "commit-4"
    assert prepared.blocking_reasons == ()

    # 4) 同一请求重放：幂等，且不再产生提交。
    replayed = prepare_run(
        _inputs_from(plan, case),
        unit_of_work=unit_of_work,
        reader=reader,
        clock=clock,
        existing=prepared,
    )
    assert replayed is prepared
    assert unit_of_work.commit_seq() == "commit-4"


def test_full_chain_records_are_readable_by_plan_and_case_revision() -> None:
    """记录按准确修订读取，查询按项目范围分页（存储与恢复第 13 节）。"""
    store = MemoryStore()
    unit_of_work = MemoryUnitOfWork(store)
    reader = MemoryReader(store)

    unit_of_work.open("project-1")
    unit_of_work.stage_record(
        aggregate_kind="plan",
        record_id="plan-1",
        expected_revision=None,
        payload={"project_id": "project-1", "plan_id": "plan-1"},
    )
    unit_of_work.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1", "case_id": "case-1"},
    )
    unit_of_work.commit()

    plan_record = reader.read(aggregate_kind="plan", record_id="plan-1", revision=1)
    assert plan_record.payload["plan_id"] == "plan-1"

    page = reader.query(RecordQuery(project_id="project-1", limit=10))
    assert {item.aggregate_kind for item in page.items} == {"plan", "case"}
    assert page.next_cursor is None
