"""用**真实链路**生成 `PreparedRun` 功能夹具。

用途（实施方案第 4 节"只做两次业务交接"的第一次）：

> 先以 B 的真实 `PreparedRun` 替换 C 的夹具，核对准备/启动间来源变化。

Sprint 0 的三份夹具（`tests/contracts/fixtures/prepared_run/`）是**手写的合同级样例**，
未覆盖 Sprint 1 新增的项目上下文对象（项目、绑定、模块、环境、依赖图）。
B-C 合同第 13.3 节把这一条登记为缺口。**本模块用真实编排产出功能夹具**，
因此字段值（`payload_hash`、提交序号、来源修订）都是**算出来的**，不是编的。

**这是测试支撑，不注册为可用能力。** 组装顺序就是产品链路：

    项目上下文 → 领域发布门禁 → prepare_run → PreparedRun

`PreparedRun` 本身不落盘（B 的对外交付是对象），因此链路上的提交序号依次为：
项目 1、绑定 2、计划 3、准备记录 4。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aitest.application.planning.preparation import InputRevisions
from aitest.application.planning.prepare_run import PreparationInputs, prepare_run
from aitest.application.project.context import (
    GAP_MISSING_ENVIRONMENT_CARRIER,
    BindingInputs,
    ContextGap,
    EnvironmentInputs,
    blocking_gaps,
    create_binding,
    create_environment,
    create_project,
    detect_context_gaps,
    register_graph,
)
from aitest.contracts.prepared_run import (
    AssertionBasisEntry,
    AssertionBasisStateFact,
    BindingFormFact,
    CaseLinks,
    CaseRevisionRef,
    ConfirmationRef,
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
    CaseLink,
    Plan,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
    validate_plan_publication,
)
from aitest.domain.planning.plans import (
    CaseRevisionRef as DomainCaseRevisionRef,
)
from aitest.domain.planning.plans import (
    RuleRevisionRef as DomainRuleRevisionRef,
)
from aitest.domain.planning.plans import (
    TemplateVersionRef as DomainTemplateRef,
)
from aitest.domain.project.context import (
    BindingForm,
    Dependency,
    DriveKind,
    IsolationMode,
    LocalProject,
    Module,
    SecretRef,
)
from tests.support.memory_substrate import (
    FixedClock,
    MemoryReader,
    MemoryStore,
    MemoryUnitOfWork,
)

#: 固定时间：夹具必须可复现，**不使用系统时间**。
FIXED_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

PROJECT_ID = "project-ticket"
WORKSPACE_ID = "ws-ticket"
BINDING_ID = "binding-ticket"
CLIENT_ID = "client-trae"
BASE_COMMIT = "9c44344bcd612df1a7d033efa1e7a47c810c49cf"
MANIFEST_DIGEST = "sha256:manifest-plain-1"
SELECTED_PATHS = ("src/ticket", "tests")
EXCLUSION_RULES = ("**/__pycache__/**", "**/*.pyc")

#: 四种形态；后两者是**阻塞**分支。
SCENARIOS = ("git", "plain", "blocked", "needs_reprepare")

#: `plain` 形态的 payload 中必须**不出现**的键（不是空值占位，是键不存在）。
_GIT_ONLY_KEYS = ("git_base_commit", "git_diff_digest")


@dataclass(frozen=True, slots=True)
class Scenario:
    """一个场景的完整世界；夹具生成与测试共用。"""

    name: str
    prepared_run: PreparedRun
    project: LocalProject
    plan: Plan
    cases: tuple[Case, ...]
    unit_of_work: MemoryUnitOfWork
    reader: MemoryReader


# ------------------------------------------------------------------ 领域对象


def _modules() -> tuple[Module, Module]:
    return (
        Module(
            module_id="module-ticket",
            project_id=PROJECT_ID,
            name="ticket core",
            responsibility="create and read tickets",
            interface_note="registered HTTP handlers",
            inputs=("ticket payload",),
            outputs=("ticket id",),
        ),
        Module(
            module_id="module-store",
            project_id=PROJECT_ID,
            name="ticket store",
            responsibility="persist tickets",
            interface_note="local database",
            inputs=("ticket record",),
            outputs=("stored row",),
        ),
    )


def _cases() -> tuple[Case, Case]:
    first = Case(
        case_id="case-create-ticket",
        revision=1,
        layer=CaseLayer.L2,
        objective="create a ticket through the registered entry",
        preconditions=("the service is running locally",),
        inputs=("ticket payload",),
        steps=(
            "create a ticket through the registered entry",
            "read the ticket back through an independent read-only query",
        ),
        expected="the persisted ticket matches the submitted fields",
        verification_method="registered pytest entry",
        links=CaseLink(
            acceptance_item_ids=frozenset({"acceptance-create-ticket"}),
            module_ids=frozenset({"module-ticket", "module-store"}),
            environment_ids=frozenset({"env-local"}),
            critical_path_ids=frozenset({"path-ticket-write-read"}),
        ),
        assertion_basis=AssertionBasis(
            revision=1,
            state=AssertionBasisState.PRESENT_UNCONFIRMED,
            text="an independent read-back proves the ticket was persisted",
            text_digest="sha256:basis-create-ticket",
        ),
        independent_verification="read-only query against the same ticket id",
        importance=CaseImportance.P0,
    )
    second = Case(
        case_id="case-change-status",
        revision=1,
        layer=CaseLayer.L2,
        objective="change a ticket status and read it back",
        preconditions=("a ticket already exists",),
        inputs=("ticket id", "new status"),
        steps=("change the ticket status", "re-read the ticket independently"),
        expected="the new status is persisted",
        verification_method="registered pytest entry",
        links=CaseLink(
            acceptance_item_ids=frozenset({"acceptance-change-status"}),
            module_ids=frozenset({"module-ticket", "module-store"}),
            environment_ids=frozenset({"env-local"}),
            critical_path_ids=frozenset({"path-ticket-write-read"}),
        ),
        assertion_basis=AssertionBasis(
            revision=1,
            state=AssertionBasisState.CONFIRMED,
            text="re-reading the ticket proves the status change was persisted",
            text_digest="sha256:basis-change-status",
        ),
        independent_verification="read-only query after changing the status",
        importance=CaseImportance.P1,
    )
    return first, second


def _plan(cases: tuple[Case, ...]) -> Plan:
    scope = AcceptanceScope(
        scope_id="scope-ticket",
        revision=1,
        name="ticket lifecycle scope",
        required_case_ids=frozenset(case.case_id for case in cases),
        template_case_ids=frozenset(case.case_id for case in cases),
        objective="prove ticket creation and status change are persisted",
        dependency_closure_ids=frozenset({"module-store"}),
    )
    return Plan(
        plan_id="plan-ticket",
        revision=1,
        scope=scope,
        case_revisions=tuple(
            DomainCaseRevisionRef(
                case_id=case.case_id,
                revision=case.revision,
                digest=f"sha256:case-{case.case_id}",
            )
            for case in cases
        ),
        rule_revisions=(
            DomainRuleRevisionRef(
                rule_id="rule-ticket", revision=1, digest="sha256:rule-ticket"
            ),
        ),
        template_versions=(
            DomainTemplateRef(
                template_id="ticket-workflow",
                version="1.0.0",
                digest="sha256:tpl-ticket",
            ),
        ),
        run_tier=RunTier.FULL,
        initial_driver=RunDriver.PLANNED,
        status=PlanPublicationStatus.PUBLISHED,
        confirmation_id="commit-3",
    )


# ------------------------------------------------------------------ 契约映射


def _frozen_case(case: Case) -> FrozenCase:
    return FrozenCase(
        case_id=case.case_id,
        revision=case.revision,
        layer=case.layer.value,  # type: ignore[arg-type]
        required=True,
        independent_verification=case.independent_verification or "",
        mock_scope=case.mock_scope,
        importance=case.importance.value,
        steps=tuple(
            FrozenCaseStep(
                step_id=f"step-{index}",
                layer=case.layer.value,  # type: ignore[arg-type]
                objective=step,
                expected=case.expected,
            )
            for index, step in enumerate(case.steps, start=1)
        ),
        links=CaseLinks(
            acceptance_item_ids=tuple(sorted(case.links.acceptance_item_ids)),
            module_ids=tuple(sorted(case.links.module_ids)),
            environment_ids=tuple(sorted(case.links.environment_ids)),
            critical_path_ids=tuple(sorted(case.links.critical_path_ids)),
        ),
    )


def _assertion_basis_entry(case: Case) -> AssertionBasisEntry:
    """把用例的断言依据映射成合同条目。

    **`confirmed` 必须带确认引用**（合同校验，源自 Sprint 4 领域规则）：
    确认绑定的是**准确的依据修订与文本摘要**，因此这里按用例自身的依据确定性派生，
    而不是写死一个假的确认号。
    """
    state_map = {
        AssertionBasisState.MISSING: AssertionBasisStateFact.MISSING,
        AssertionBasisState.PRESENT_UNCONFIRMED: (
            AssertionBasisStateFact.PRESENT_UNCONFIRMED
        ),
        AssertionBasisState.CONFIRMED: AssertionBasisStateFact.CONFIRMED,
    }
    state = state_map[case.assertion_basis.state]
    confirmations: tuple[ConfirmationRef, ...] = ()
    if state is AssertionBasisStateFact.CONFIRMED:
        confirmations = (
            ConfirmationRef(
                confirmation_id=f"confirmation-{case.case_id}",
                case_id=case.case_id,
                basis_revision=case.assertion_basis.revision,
                confirmed_at_commit="commit-2",
            ),
        )
    return AssertionBasisEntry(
        case_id=case.case_id,
        basis_revision=case.assertion_basis.revision,
        basis_text_digest=case.assertion_basis.text_digest,
        assertion_basis_state=state,
        confirmation_refs=confirmations,
    )


# ------------------------------------------------------------------ 组装链路


def _prepare_inputs(
    *,
    project: LocalProject,
    binding_form: BindingForm,
    cases: tuple[Case, ...],
    plan: Plan,
    context_gaps: tuple[ContextGap, ...],
    source_revision: int = 1,
    plain_manifest_digest: str | None = None,
) -> PreparationInputs:
    is_git = binding_form is BindingForm.GIT
    return PreparationInputs(
        project_id=PROJECT_ID,
        workspace_id=WORKSPACE_ID,
        binding_id=BINDING_ID,
        binding_revision=2,
        binding_form=(
            BindingFormFact.GIT if is_git else BindingFormFact.PLAIN
        ),
        client_id=CLIENT_ID,
        prepare_request_id="prepare-request-1",
        input_revisions=InputRevisions(
            project_revision=project.revision,
            binding_revision=2,
            snapshot_revision=source_revision,
            environment_revision=1,
            plan_revision=plan.revision,
            rules_revision=1,
            template_revision=1,
            scope_revision=plan.scope.revision,
        ),
        snapshot=SnapshotRef(
            source_snapshot_id="snapshot-ticket-1",
            purpose="prepare",
            content_identity=(
                f"git:{BASE_COMMIT}" if is_git else f"sha256:{MANIFEST_DIGEST}"
            ),
        ),
        selected_paths=SELECTED_PATHS,
        exclusion_rules=EXCLUSION_RULES,
        refetch_dependencies=("git-lfs:assets/*",) if is_git else (),
        environment=EnvironmentRefFact(
            environment_id="env-local",
            revision=1,
            isolation_mode=EnvironmentIsolationModeFact.VENV,
            interpreter_identity="cpython-3.13.3-windows-amd64",
            dependency_set_digest="sha256:dependency-set-1",
        ),
        execution_source=ExecutionSourceBinding(
            registered_entry="python -m pytest",
            entry_arguments=("tests/acceptance",),
            cwd_mapping="<project-root> -> workdirs/run-1",
            allowed_env_keys=("PYTHONPATH",),
            secret_refs=("MODEL_API_KEY",),
            test_config_ref="pyproject.toml#tool.pytest",
            adapter_versions={"command": "1.0.0"},
            resolved_input_digest="sha256:resolved-input-1",
        ),
        plan_revision=PlanRevisionRef(
            revision_id=plan.plan_id,
            revision_no=plan.revision,
            digest="sha256:plan-ticket",
        ),
        acceptance_scope_revision=plan.scope.revision,
        rule_versions=(
            RuleVersionRef(
                rule_id="rule-ticket", revision=1, digest="sha256:rule-ticket"
            ),
        ),
        template_versions=(
            TemplateVersionRef(
                template_id="ticket-workflow",
                version="1.0.0",
                digest="sha256:tpl-ticket",
            ),
        ),
        case_revisions=tuple(
            CaseRevisionRef(
                case_id=case.case_id,
                revision=case.revision,
                digest=f"sha256:case-{case.case_id}",
            )
            for case in cases
        ),
        frozen_cases=tuple(_frozen_case(case) for case in cases),
        assertion_bases=tuple(_assertion_basis_entry(case) for case in cases),
        context_gaps=context_gaps,
        git_base_commit=BASE_COMMIT if is_git else None,
        git_diff_digest="sha256:git-diff-1" if is_git else None,
        plain_manifest_digest=plain_manifest_digest,
        run_tier=RunTierFact.FULL,
        initial_driver=RunDriverFact.PLANNED,
        template_required_case_ids=tuple(case.case_id for case in cases),
        frozen_required_case_ids=tuple(case.case_id for case in cases),
        selected_case_ids=tuple(case.case_id for case in cases),
    )


def build_scenario(name: str) -> Scenario:
    """按场景名组装一次真实的准备链路。

    链路上每次写入都是一个短事务（不变量 4 禁止同一事务重复暂存同一记录），
    因此提交序号是可预期的：项目 1、绑定 2、计划 3、准备记录 4。
    """
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario: {name}")

    store = MemoryStore()
    unit_of_work = MemoryUnitOfWork(store)
    reader = MemoryReader(store)
    clock = FixedClock(FIXED_NOW)

    modules = _modules()
    cases = _cases()
    plan = _plan(cases)
    # 领域发布门禁：计划必须先过门禁才能进入准备（与 `publish_plan()` 用同一函数）。
    validate_plan_publication(plan, list(cases))

    binding_form = BindingForm.PLAIN if name == "plain" else BindingForm.GIT
    plain_manifest_digest = MANIFEST_DIGEST if name == "plain" else None
    source_revision = 9 if name == "needs_reprepare" else 1

    project = create_project(
        project_id=PROJECT_ID,
        workspace_id=WORKSPACE_ID,
        name="ticket service",
        goal="verify ticket creation and status change",
        created_at_commit="commit-0",
        modules=modules,
    )

    # 提交 1：项目。
    unit_of_work.open(PROJECT_ID)
    unit_of_work.stage_record(
        aggregate_kind="project",
        record_id=PROJECT_ID,
        expected_revision=None,
        payload={
            "project_id": PROJECT_ID,
            "workspace_id": WORKSPACE_ID,
            "name": project.name,
            "revision": project.revision,
        },
    )
    assert unit_of_work.commit().commit_seq == "commit-1"

    # 提交 2：绑定。
    binding_inputs = BindingInputs(
        binding_id=BINDING_ID,
        project_id=PROJECT_ID,
        canonical_path=(
            r"C:\work\ticket" if binding_form is BindingForm.GIT else r"C:\work\ticket-plain"
        ),
        binding_form=binding_form,
        drive_kind=DriveKind.FIXED,
        binding_revision=2,
        repository_id="origin" if binding_form is BindingForm.GIT else None,
        branch="main" if binding_form is BindingForm.GIT else None,
        base_commit=BASE_COMMIT if binding_form is BindingForm.GIT else None,
        manifest_digest=plain_manifest_digest,
        local_owner="feix-a",
        confirmed=True,
    )
    binding_result = create_binding(binding_inputs)
    if binding_result.binding is None:  # pragma: no cover - 夹具输入固定，不应发生
        raise AssertionError(f"fixture binding failed: {binding_result.gaps}")
    unit_of_work.open(PROJECT_ID)
    unit_of_work.stage_record(
        aggregate_kind="binding",
        record_id=BINDING_ID,
        expected_revision=None,
        payload={"project_id": PROJECT_ID, "binding_id": BINDING_ID},
    )
    assert unit_of_work.commit().commit_seq == "commit-2"

    # 提交 3：计划。
    unit_of_work.open(PROJECT_ID)
    unit_of_work.stage_record(
        aggregate_kind="plan",
        record_id=plan.plan_id,
        expected_revision=None,
        payload={"project_id": PROJECT_ID, "plan_id": plan.plan_id},
    )
    assert unit_of_work.commit().commit_seq == "commit-3"

    environment = create_environment(
        EnvironmentInputs(
            environment_id="env-local",
            interpreter_requirement="python>=3.13,<3.14",
            dependency_declaration="uv.lock",
            isolation_mode=IsolationMode.VENV,
            data_isolated=True,
            data_reset_policy="drop and recreate the local ticket table",
            target_deployment_identity="local-service",
            request_timeout_seconds=30,
            step_timeout_seconds=120,
            network_targets=("http://127.0.0.1:8080",),
            secret_refs=(SecretRef(env_key="MODEL_API_KEY", purpose="model"),),
        )
    )
    graph = register_graph(
        project_id=PROJECT_ID,
        modules=modules,
        dependencies=(
            Dependency(
                consumer_module_id="module-ticket", provider_module_id="module-store"
            ),
        ),
    )

    context_gaps: tuple[ContextGap, ...] = ()
    if name == "blocked":
        context_gaps = (
            ContextGap(
                kind=GAP_MISSING_ENVIRONMENT_CARRIER,
                subject="environment:env-local",
                detail="the environment declares no dependency carrier",
            ),
        )
    else:
        # 走真实的缺口检测：完整上下文必须**没有**阻塞缺口，否则夹具本身就不合法。
        detected = detect_context_gaps(
            project=project,
            graph=graph,
            environment=environment.environment,
            drive_kind=DriveKind.FIXED,
        )
        blocking = blocking_gaps(detected)
        if blocking:  # pragma: no cover - 夹具上下文完整，不应发生
            raise AssertionError(f"fixture context is incomplete: {blocking}")
        context_gaps = detected

    inputs = _prepare_inputs(
        project=project,
        binding_form=binding_form,
        cases=cases,
        plan=plan,
        context_gaps=context_gaps,
        source_revision=source_revision,
        plain_manifest_digest=plain_manifest_digest,
    )

    if name == "needs_reprepare":
        # "依据需重新准备"只在**已有同键准备记录**时才谈得上：
        # 先用**原始**来源修订（1）登记一次准备（提交 4），再用**变化后**的来源（当前
        # `source_revision`）重放。来源修订不进 `payload_hash`
        # （见 `10-...设计说明.md` 第 3.4 节），因此两次摘要相同、只有修订不同——
        # 这正是 `needs_reprepare` 的判据。
        original = prepare_run(
            _prepare_inputs(
                project=project,
                binding_form=binding_form,
                cases=cases,
                plan=plan,
                context_gaps=context_gaps,
                source_revision=1,
                plain_manifest_digest=plain_manifest_digest,
            ),
            unit_of_work=unit_of_work,
            reader=reader,
            clock=clock,
        )
        assert original.status is PreparedRunStatusFact.PREPARED

        replanned = prepare_run(
            inputs,
            unit_of_work=unit_of_work,
            reader=reader,
            clock=clock,
        )
        assert replanned.status is PreparedRunStatusFact.BLOCKED
        prepared = replanned
    else:
        prepared = prepare_run(
            inputs,
            unit_of_work=unit_of_work,
            reader=reader,
            clock=clock,
        )

    if name == "blocked":
        assert prepared.status is PreparedRunStatusFact.BLOCKED
    return Scenario(
        name=name,
        prepared_run=prepared,
        project=project,
        plan=plan,
        cases=cases,
        unit_of_work=unit_of_work,
        reader=reader,
    )


def fixture_payload(name: str) -> dict[str, object]:
    """夹具文件内容：`PreparedRun` 的 JSON 形态（键排序，便于逐字节比较）。

    **形态不适用的键真正省略**，与 `application/project/serialization.py` 同一条规范：
    需求 P1-AC25 要求 `plain` 项目**不出现**仓库/分支/提交内容，
    "**也不显示为空值或"未知"**"。因此不能直接 `model_dump()` ——
    那会把 `git_base_commit` 写成 `null`，正是被禁止的形态。
    """
    payload = build_scenario(name).prepared_run.model_dump(mode="json")
    if payload["binding_form"] == BindingFormFact.PLAIN.value:
        for key in _GIT_ONLY_KEYS:
            payload.pop(key, None)
    return payload


__all__ = [
    "BASE_COMMIT",
    "CLIENT_ID",
    "EXCLUSION_RULES",
    "FIXED_NOW",
    "MANIFEST_DIGEST",
    "PROJECT_ID",
    "SCENARIOS",
    "SELECTED_PATHS",
    "WORKSPACE_ID",
    "Scenario",
    "build_scenario",
    "fixture_payload",
]
