"""`prepare_run` 用例：把业务输入编排成不可变的 `PreparedRun`。

严格对应一期架构文档《01-项目与计划》第 8 节「发布、准备与启动的调用次序」与
第 11 节「准备请求与业务身份合同」。

四条硬性约束的落点是本模块的核心（设计说明第 4.3 节）：

| 约束 | 落点 |
| --- | --- |
| 上下文缺失**列缺口并阻塞**，不编造依赖与结论 | 缺口分支返回 `status=blocked`，**不抛异常** |
| **不把新字节塞进旧意图** | `NEEDS_REPREPARE` 分支**不调用** `stage_preparation` |
| **同键异摘要返回冲突，不覆盖** | `CONFLICTED` 分支抛出，不写任何记录 |
| **同请求同输入幂等返回** | `REUSED` 分支直接返回既有结果，提交序号不前进 |

`intent_id` 由**应用生成**，不接受调用方传入的传输号；`prepare_request_id` 只是请求身份。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from aitest.application.planning.preparation import (
    InputRevisions,
    PreparationDecision,
    PreparationRecord,
    PreparationRequest,
    decide_preparation,
    payload_hash,
    preparation_identity_digest,
    preparation_intent_id,
)
from aitest.application.planning.substrate import (
    PreparationConflictError,
    RecordReader,
    UnitOfWork,
    transaction,
)
from aitest.application.ports import Clock
from aitest.contracts.prepared_run import (
    AssertionBasisEntry,
    AuthorizationRequirement,
    BindingFormFact,
    BlockingReason,
    CaseRevisionRef,
    EnvironmentRefFact,
    ExclusionEntry,
    ExecutionSourceBinding,
    FrozenCase,
    GapEntry,
    InvalidationRule,
    PlanRevisionRef,
    PreparedRun,
    PreparedRunStatusFact,
    RuleVersionRef,
    RunDriverFact,
    RunTierFact,
    SkippedScopeEntry,
    SnapshotRef,
    TemplateVersionRef,
    conclusion_ceiling_for,
)

#: 各来源变化对应的失效规则描述；`needs_reprepare` 时据此生成提示。
_INVALIDATION_DESCRIPTIONS: Mapping[str, str] = {
    "project_revision": "project context changed",
    "binding_revision": "binding changed",
    "snapshot_revision": "source bytes changed",
    "environment_revision": "environment changed",
    "plan_revision": "plan republished",
    "rules_revision": "rules republished",
    "template_revision": "template version changed",
    "scope_revision": "acceptance scope changed",
    "case_revisions": "frozen case revisions changed",
}

_STATUS_BLOCKED = PreparedRunStatusFact.BLOCKED
_STATUS_PREPARED = PreparedRunStatusFact.PREPARED


@dataclass(frozen=True, slots=True)
class PreparationInputs:
    """`prepare_run` 的全部业务输入；不含任何传输层参数。"""

    project_id: str
    workspace_id: str
    binding_id: str
    binding_revision: int
    binding_form: BindingFormFact
    client_id: str
    prepare_request_id: str
    input_revisions: InputRevisions
    snapshot: SnapshotRef
    selected_paths: tuple[str, ...]
    environment: EnvironmentRefFact
    execution_source: ExecutionSourceBinding
    plan_revision: PlanRevisionRef
    acceptance_scope_revision: int
    rule_versions: tuple[RuleVersionRef, ...]
    template_versions: tuple[TemplateVersionRef, ...]
    case_revisions: tuple[CaseRevisionRef, ...]
    frozen_cases: tuple[FrozenCase, ...]
    assertion_bases: tuple[AssertionBasisEntry, ...]
    #: 项目上下文缺口；非空即阻塞（P1-AC17）。
    context_gaps: tuple[GapEntry, ...] = field(default_factory=tuple)
    authorization_requirements: tuple[AuthorizationRequirement, ...] = field(
        default_factory=tuple
    )
    model_outbound_policy_revision: int | None = None
    source_snippets_enabled: bool = False
    exclusion_rules: tuple[str, ...] = field(default_factory=tuple)
    refetch_dependencies: tuple[str, ...] = field(default_factory=tuple)
    git_base_commit: str | None = None
    git_diff_digest: str | None = None
    plain_manifest_digest: str | None = None
    run_tier: RunTierFact = RunTierFact.QUICK
    initial_driver: RunDriverFact = RunDriverFact.PLANNED
    template_required_case_ids: tuple[str, ...] = field(default_factory=tuple)
    frozen_required_case_ids: tuple[str, ...] = field(default_factory=tuple)
    selected_case_ids: tuple[str, ...] = field(default_factory=tuple)
    skipped_scope: tuple[SkippedScopeEntry, ...] = field(default_factory=tuple)
    applicability_exclusions: tuple[ExclusionEntry, ...] = field(default_factory=tuple)


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def preparation_payload(inputs: PreparationInputs) -> dict[str, object]:
    """参与业务输入摘要的字段：**请求要什么**。

    摘要只覆盖**业务意图**与**人工选择**，键集合与 `PAYLOAD_FIELDS` 严格一致
    （`tests/unit/test_prepare_run.py` 有对照测试，防止两处再次分叉）：

    - **不放传输层参数**（`request_id`、重试次数、接收时间）：放进来会让"同号重传"
      被误判为"输入不同"，与"重传不变"直接矛盾；
    - **不放"实际观察到的来源事实"**：来源修订、计划修订标识、快照内容身份、
      用例/规则/模板的**修订号**都是**观察结果**，不是请求内容。来源或依据变了要报
      "依据需重新准备"（架构文档第 11 节），而不是"同键异输入冲突"。
      判定次序是"冲突 > 需重新准备"，所以把观察结果混进摘要会把前者盖住后者；
    - **只放标识、不放修订号**：`case_revision_ids` / `rule_version_ids` /
      `template_version_ids` 记的是"这一轮涉及哪些对象"，对象的修订变化由
      `InputRevisions` 单独比对。

    来源修订与依据修订的比对由 `decide_preparation()` 单独完成。

    **`execution_source` 分成两半**（B-02）：

    - **请求侧的实际输入**（`registered_entry`、`entry_arguments`、`cwd_mapping`、
      `allowed_env_keys`、`secret_refs`、`test_config_ref`、`adapter_versions`）
      是"这一轮要按什么执行"的人工给定内容，**进摘要**；漏了它们就会出现
      "同意图换实际输入却摘要不变"，旧意图被当成可复用；
    - **`resolved_input_digest` 是解析结果**（观察事实），**不进摘要**——
      它变了要报"依据需重新准备"，而不是"同键异输入冲突"
      （判定次序"冲突 > 需重新准备"，把观察结果混进摘要会盖住后者）。
      该值由 `PreparationRequest.observed_resolved_input_digest` 单独比对。
    """
    return {
        "binding_form": inputs.binding_form.value,
        "selected_paths": list(inputs.selected_paths),
        "exclusion_rules": list(inputs.exclusion_rules),
        "refetch_dependencies": list(inputs.refetch_dependencies),
        "run_tier": inputs.run_tier.value,
        "driver": inputs.initial_driver.value,
        "case_revision_ids": sorted({ref.case_id for ref in inputs.case_revisions}),
        "rule_version_ids": sorted({ref.rule_id for ref in inputs.rule_versions}),
        "template_version_ids": sorted(
            {ref.template_id for ref in inputs.template_versions}
        ),
        "selected_case_ids": sorted(inputs.selected_case_ids),
        "skipped_scope": [
            [entry.case_id, entry.reason] for entry in inputs.skipped_scope
        ],
        "applicability_exclusions": [
            [entry.case_id, entry.reason] for entry in inputs.applicability_exclusions
        ],
        "source_snippets_enabled": inputs.source_snippets_enabled,
        # 执行来源的**请求侧**字段：任一变化都必须改变摘要（B-02）。
        "execution_source": {
            "registered_entry": inputs.execution_source.registered_entry,
            "entry_arguments": list(inputs.execution_source.entry_arguments),
            "cwd_mapping": inputs.execution_source.cwd_mapping,
            "allowed_env_keys": list(inputs.execution_source.allowed_env_keys),
            "secret_refs": list(inputs.execution_source.secret_refs),
            "test_config_ref": inputs.execution_source.test_config_ref,
            "adapter_versions": dict(inputs.execution_source.adapter_versions),
        },
    }


def _prepared_run_id(inputs: PreparationInputs) -> str:
    """`PreparedRun` 的业务标识。

    带 `project`/`client` 命名空间：同一个 `prepare_request_id` 在不同项目或不同客户端下
    是**不同的业务对象**，不带命名空间会让两者的准备结果互相覆盖。
    """
    digest = preparation_identity_digest(
        project_id=inputs.project_id,
        client_id=inputs.client_id,
        prepare_request_id=inputs.prepare_request_id,
    )
    return f"prepared-{digest}"


def _invalidation_rules_for(changed: tuple[str, ...]) -> tuple[InvalidationRule, ...]:
    return tuple(
        InvalidationRule(
            source_kind=name,
            description=_INVALIDATION_DESCRIPTIONS.get(name, f"{name} changed"),
        )
        for name in changed
    )


def _build(
    inputs: PreparationInputs,
    *,
    intent_id: str,
    digest: str,
    created_at: datetime,
    created_at_commit: str,
    status: PreparedRunStatusFact,
    blocking_reasons: tuple[BlockingReason, ...] = (),
    invalidation_rules: tuple[InvalidationRule, ...] = (),
) -> PreparedRun:
    """构造一份 `PreparedRun`；缺口与阻塞原因由调用分支给出。"""
    return PreparedRun(
        prepared_run_id=_prepared_run_id(inputs),
        project_id=inputs.project_id,
        workspace_id=inputs.workspace_id,
        binding_id=inputs.binding_id,
        binding_revision=inputs.binding_revision,
        binding_form=inputs.binding_form,
        client_id=inputs.client_id,
        prepare_request_id=inputs.prepare_request_id,
        intent_id=intent_id,
        payload_hash=digest,
        status=status,
        created_at=created_at,
        created_at_commit=created_at_commit,
        snapshot=inputs.snapshot,
        git_base_commit=inputs.git_base_commit,
        git_diff_digest=inputs.git_diff_digest,
        plain_manifest_digest=inputs.plain_manifest_digest,
        selected_paths=inputs.selected_paths,
        exclusion_rules=inputs.exclusion_rules,
        refetch_dependencies=inputs.refetch_dependencies,
        environment=inputs.environment,
        execution_source=inputs.execution_source,
        plan_revision=inputs.plan_revision,
        acceptance_scope_revision=inputs.acceptance_scope_revision,
        rule_versions=inputs.rule_versions,
        template_versions=inputs.template_versions,
        case_revisions=inputs.case_revisions,
        run_tier=inputs.run_tier,
        initial_driver=inputs.initial_driver,
        conclusion_ceiling=conclusion_ceiling_for(inputs.run_tier),
        template_required_case_ids=inputs.template_required_case_ids,
        frozen_required_case_ids=inputs.frozen_required_case_ids,
        selected_case_ids=inputs.selected_case_ids,
        skipped_scope=inputs.skipped_scope,
        applicability_exclusions=inputs.applicability_exclusions,
        assertion_bases=inputs.assertion_bases,
        frozen_cases=inputs.frozen_cases,
        authorization_requirements=inputs.authorization_requirements,
        model_outbound_policy_revision=inputs.model_outbound_policy_revision,
        source_snippets_enabled=inputs.source_snippets_enabled,
        invalidation_rules=invalidation_rules,
        blocking_reasons=blocking_reasons,
    )


def prepare_run(
    inputs: PreparationInputs,
    *,
    unit_of_work: UnitOfWork,
    reader: RecordReader,
    clock: Clock,
    existing: PreparedRun | None = None,
) -> PreparedRun:
    """编排一次准备；返回可直接展示的 `PreparedRun`。

    `existing` 是同一准备请求已保存的 `PreparedRun`（若调用方已取到）。
    判定为幂等复用时直接返回它，**不新建记录**。
    """
    _require_text(inputs.project_id, "project_id")
    _require_text(inputs.workspace_id, "workspace_id")
    _require_text(inputs.binding_id, "binding_id")
    _require_text(inputs.client_id, "client_id")
    _require_text(inputs.prepare_request_id, "prepare_request_id")

    intent_id = preparation_intent_id(
        project_id=inputs.project_id,
        client_id=inputs.client_id,
        prepare_request_id=inputs.prepare_request_id,
    )
    digest = payload_hash(preparation_payload(inputs))
    now = clock.now()

    # 步骤 1：项目上下文缺口 → 列缺口并阻塞，不编造依赖与结论（P1-AC17）。
    if inputs.context_gaps:
        return _build(
            inputs,
            intent_id=intent_id,
            digest=digest,
            created_at=now,
            created_at_commit=unit_of_work.commit_seq(),
            status=_STATUS_BLOCKED,
            blocking_reasons=tuple(
                BlockingReason(code=gap.kind, message=gap.detail)
                for gap in inputs.context_gaps
            ),
        )

    # 步骤 3—4：按三元组查已有准备记录并判定。
    record = reader.find_preparation(
        project_id=inputs.project_id,
        client_id=inputs.client_id,
        prepare_request_id=inputs.prepare_request_id,
    )
    decision = decide_preparation(
        PreparationRequest(
            project_id=inputs.project_id,
            client_id=inputs.client_id,
            prepare_request_id=inputs.prepare_request_id,
            payload_hash=digest,
            input_revisions=inputs.input_revisions,
            observed_case_revisions=tuple(
                (ref.case_id, ref.revision) for ref in inputs.case_revisions
            ),
            # 解析出来的实际执行输入摘要随请求一起参与**观察比对**（B-02）：
            # 它不进 `payload_hash`（那是请求内容），但变了必须报"依据需重新准备"。
            observed_resolved_input_digest=inputs.execution_source.resolved_input_digest,
        ),
        record,
    )

    if decision.decision is PreparationDecision.CONFLICTED:
        assert record is not None  # CONFLICTED 只可能来自已有记录
        raise PreparationConflictError(
            project_id=inputs.project_id,
            client_id=inputs.client_id,
            prepare_request_id=inputs.prepare_request_id,
            existing_intent_id=record.intent_id,
            existing_payload_hash=record.request.payload_hash,
            existing_created_at_commit=record.created_at_commit,
        )

    if decision.decision is PreparationDecision.NEEDS_REPREPARE:
        # 关键：**不调用 stage_preparation**，不把新字节塞进旧意图。
        return _build(
            inputs,
            intent_id=record.intent_id if record is not None else intent_id,
            digest=digest,
            created_at=now,
            created_at_commit=unit_of_work.commit_seq(),
            status=_STATUS_BLOCKED,
            blocking_reasons=(
                BlockingReason(
                    code="needs_reprepare",
                    message="Basis needs re-preparation: "
                    + ", ".join(decision.changed_inputs),
                ),
            ),
            invalidation_rules=_invalidation_rules_for(decision.changed_inputs),
        )

    if decision.decision is PreparationDecision.REUSED:
        if existing is not None:
            return existing
        return _build(
            inputs,
            intent_id=decision.intent_id or intent_id,
            digest=digest,
            created_at=now,
            created_at_commit=(
                record.created_at_commit if record is not None else unit_of_work.commit_seq()
            ),
            status=_STATUS_PREPARED,
        )

    # 步骤 4d—5：新建。登记准备意图并与记录同一次提交。
    # 用事务上下文而不是裸 `open()`：真实底座的 `open()` 会取工作空间级排他写锁，
    # 这里若抛异常或提前返回，锁必须还回去（`substrate.Transaction` 负责收尾）。
    with transaction(unit_of_work, inputs.project_id) as tx:
        tx.stage_preparation(
            record=PreparationRecord(
                request=PreparationRequest(
                    project_id=inputs.project_id,
                    client_id=inputs.client_id,
                    prepare_request_id=inputs.prepare_request_id,
                    payload_hash=digest,
                    input_revisions=inputs.input_revisions,
                    observed_case_revisions=tuple(
                        (ref.case_id, ref.revision) for ref in inputs.case_revisions
                    ),
                    observed_resolved_input_digest=(
                        inputs.execution_source.resolved_input_digest
                    ),
                ),
                intent_id=intent_id,
                # 记录里的序号必须是**本次提交后**的序号，不能取提交前的当前值。
                created_at_commit=tx.next_commit_seq(),
            ),
        )
        result = tx.commit()

    return _build(
        inputs,
        intent_id=intent_id,
        digest=digest,
        created_at=now,
        created_at_commit=result.commit_seq,
        status=_STATUS_PREPARED,
    )


__all__ = ["PreparationInputs", "prepare_run", "preparation_payload"]
