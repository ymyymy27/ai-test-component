"""计划、用例、断言依据与发布门禁；无 I/O 或框架依赖。

运行词汇表（档位 / 驱动 / 结论上限）的语义唯一来源。
合同层在 `aitest.contracts.prepared_run` 中声明对应枚举，值集合由
`tests/contracts/test_run_vocabulary.py` 锁定一致。

断言依据三态的派生依据架构文档《01-项目与计划》第 3 节：

    确认依据使用既有 `ConfirmationRecord` 绑定准确 Case/依据修订与摘要，
    **不改写冻结 Plan 或原 Case 字节**；应用读取该精确依据的有效确认记录派生
    `effective_assertion_basis_state`……**不能将文本已改变的旧确认带到新依据**。

因此本模块提供的是**派生函数**，不是派生字段：派生值不写回冻结对象。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from aitest.domain.planning.rules import RuleRevisionRef as RuleRevisionRef
from aitest.domain.planning.understanding import CoverageClaim as CoverageClaim


class RunTier(StrEnum):
    """档位：决定检查范围与结论上限（架构文档《01-项目与计划》第 4 节）。"""

    QUICK = "quick"
    ON_DEMAND = "on_demand"
    FULL = "full"


class RunDriver(StrEnum):
    """驱动：决定推进方式。运行中只允许 planned -> stepwise。"""

    PLANNED = "planned"
    STEPWISE = "stepwise"


class ConclusionCeiling(StrEnum):
    """结论上限：只由档位派生，不由覆盖率或驱动方式决定。"""

    PARTIAL = "partial"
    PASSABLE = "passable"


class AssertionBasisState(StrEnum):
    """断言依据三态。三者语义不同，不可合并（需求 P1-FR06）。

    `MISSING` 不得进入 full 必测；`PRESENT_UNCONFIRMED` 可执行、保留分母、
    不计入已验证；`CONFIRMED` 仍须实际证据或独立核验有效才计入已验证。
    """

    MISSING = "missing"
    PRESENT_UNCONFIRMED = "present_unconfirmed"
    CONFIRMED = "confirmed"


class CaseLayer(StrEnum):
    """用例层级。

    合同层以 `Literal["L1", "L2", "L3"]` 声明（`contracts/templates.py`、
    `contracts/prepared_run.py`），两者取值集合由
    `tests/contracts/test_project_vocabulary.py` 锁定一致。
    """

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class CaseImportance(StrEnum):
    """用例重要性。架构文档第 4 节以 P0／P1 条件判定可判通过。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class PlanPublicationStatus(StrEnum):
    """计划发布状态。草稿未发布不能执行。"""

    DRAFT = "draft"
    PUBLISHED = "published"


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_items(values: Sequence[str], name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must not contain empty values")


def _require_unique(values: Sequence[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


def conclusion_ceiling_for(tier: RunTier) -> ConclusionCeiling:
    """Return the only admissible conclusion ceiling for a run tier.

    `on_demand` 即使把冻结范围全部选上也仍为 `partial`；需要 `passable`
    时必须新建一次 `full` 运行。
    """
    if tier == RunTier.FULL:
        return ConclusionCeiling.PASSABLE
    if tier in (RunTier.QUICK, RunTier.ON_DEMAND):
        return ConclusionCeiling.PARTIAL
    raise ValueError(f"unmapped run tier: {tier}")


def narrow_driver(current: RunDriver, requested: RunDriver) -> RunDriver:
    if current == RunDriver.STEPWISE and requested == RunDriver.PLANNED:
        raise ValueError("driver cannot expand authorization")
    return requested


# ------------------------------------------------------------- 断言依据与确认


@dataclass(frozen=True, slots=True)
class AssertionBasis:
    """用例的断言依据。

    `state` 是**该 Case 修订保存时的冻结值**；当前有效状态用
    `effective_assertion_basis_state()` 派生，**不回写本对象**。
    """

    revision: int
    state: AssertionBasisState
    text: str = ""
    text_digest: str = ""

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("assertion basis revision must be >= 1")
        if self.state is AssertionBasisState.MISSING:
            if self.text or self.text_digest:
                raise ValueError("a missing assertion basis must not carry text")
            return
        _require_text(self.text, "assertion basis text")
        _require_text(self.text_digest, "assertion basis text_digest")


@dataclass(frozen=True, slots=True)
class ConfirmationRecord:
    """依据确认记录，**绑定准确的依据修订与文本摘要**。

    架构文档第 3 节称其为既有记录；本模块只定义其领域表示与匹配规则，
    持久化与编排属应用用例。
    """

    confirmation_id: str
    case_id: str
    basis_revision: int
    basis_text_digest: str
    confirmed_at_commit: str

    def __post_init__(self) -> None:
        _require_text(self.confirmation_id, "confirmation_id")
        _require_text(self.case_id, "case_id")
        _require_text(self.basis_text_digest, "basis_text_digest")
        _require_text(self.confirmed_at_commit, "confirmed_at_commit")
        if self.basis_revision < 1:
            raise ValueError("basis_revision must be >= 1")

    def matches(self, basis: AssertionBasis, *, case_id: str) -> bool:
        """确认必须**同时绑定用例、修订与摘要**；文本已改变的旧确认不适用。

        `case_id` 是**必填**的：确认记录自带 `case_id`，只比修订与摘要会让
        另一个用例的同修订同摘要确认把本用例判成已确认（B-CONFIRMATION-01）。
        强制调用方说出"这是哪个用例的确认"，这条路径才不可能被漏掉。
        """
        if self.case_id != case_id:
            return False
        return (
            self.basis_revision == basis.revision
            and self.basis_text_digest == basis.text_digest
        )


def effective_assertion_basis_state(
    basis: AssertionBasis,
    confirmations: Sequence[ConfirmationRecord],
    *,
    case_id: str,
) -> AssertionBasisState:
    """派生当前有效状态；**不修改** `basis`，也不返回任何计数。

    规则（架构文档第 3 节、需求 P1-FR06）：

    | 条件 | 结果 |
    | --- | --- |
    | 依据缺失 | `missing`（缺失无法被确认补齐） |
    | 有**本用例**的确认，且修订与摘要都匹配 | `confirmed` |
    | 有本用例的确认但修订或摘要不匹配（文本已变） | `present_unconfirmed` |
    | 无本用例的匹配确认 | `present_unconfirmed` |

    `case_id` **必填**：确认按用例分别绑定，"另一个用例的确认"不是本用例的依据有效性证据。
    **不返回"已验证数"**：补充确认不能直接把计数加一，聚合由判定侧按实际证据计算。
    """
    _require_text(case_id, "case_id")
    if basis.state is AssertionBasisState.MISSING:
        return AssertionBasisState.MISSING
    if any(record.matches(basis, case_id=case_id) for record in confirmations):
        return AssertionBasisState.CONFIRMED
    return AssertionBasisState.PRESENT_UNCONFIRMED


# ------------------------------------------------------------------ 用例与关联


@dataclass(frozen=True, slots=True)
class CaseLink:
    """用例的关联：验收项、模块、环境、关键链路。

    关键链路为空时必须给出不适用理由——"缺清单字段仍阻止发布"，
    "确无适用链路时保存明确空集合及理由"（需求 P1-FR06）。
    """

    acceptance_item_ids: frozenset[str]
    module_ids: frozenset[str] = frozenset()
    environment_ids: frozenset[str] = frozenset()
    critical_path_ids: frozenset[str] = frozenset()
    no_critical_path_reason: str | None = None
    #: AI/用户新增检查通过业务功能建立映射；模板检查仍用 acceptance_item_ids。
    function_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.acceptance_item_ids and not self.function_ids:
            raise ValueError(
                "a case must link to at least one acceptance item or business function"
            )
        for name in (
            "acceptance_item_ids",
            "function_ids",
            "module_ids",
            "environment_ids",
            "critical_path_ids",
        ):
            _require_items(sorted(getattr(self, name)), name)
        if self.critical_path_ids:
            if self.no_critical_path_reason is not None:
                raise ValueError(
                    "a reason for empty critical paths must not accompany a non-empty set"
                )
            return
        if self.no_critical_path_reason is None:
            raise ValueError(
                "empty critical paths require an explicit applicability reason"
            )
        _require_text(self.no_critical_path_reason, "no_critical_path_reason")


@dataclass(frozen=True, slots=True)
class Case:
    """用例。字段依据需求 P1-FR06 与架构文档第 3 节。

    `assertion_basis.state` 是**冻结值**；当前有效状态由
    `effective_assertion_basis_state()` 派生。
    """

    case_id: str
    revision: int
    layer: CaseLayer
    objective: str
    preconditions: tuple[str, ...]
    inputs: tuple[str, ...]
    steps: tuple[str, ...]
    expected: str
    verification_method: str
    links: CaseLink
    assertion_basis: AssertionBasis
    independent_verification: str | None = None
    mock_scope: tuple[str, ...] = field(default_factory=tuple)
    importance: CaseImportance = CaseImportance.P1
    #: 模板提醒项来源；AI/用户新增检查为空。只在技术详情与模板处置审计中使用。
    template_item_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.case_id, "case_id")
        if self.revision < 1:
            raise ValueError("case revision must be >= 1")
        _require_text(self.objective, "case objective")
        # 需求 P1-FR06：没有预期结果或前置条件不得标为可直接验收的完整用例。
        _require_text(self.expected, "case expected")
        _require_text(self.verification_method, "case verification_method")
        if not self.preconditions:
            raise ValueError("a case requires at least one precondition")
        if not self.steps:
            raise ValueError("a case requires at least one step")
        _require_items(self.preconditions, "preconditions")
        _require_items(self.inputs, "inputs")
        _require_items(self.steps, "steps")
        _require_items(self.mock_scope, "mock_scope")
        if self.independent_verification is not None:
            _require_text(self.independent_verification, "independent_verification")

    def effective_basis_state(
        self, confirmations: Sequence[ConfirmationRecord]
    ) -> AssertionBasisState:
        """本用例的当前依据状态；**只认本用例自己的确认**。"""
        return effective_assertion_basis_state(
            self.assertion_basis, confirmations, case_id=self.case_id
        )


@dataclass(frozen=True, slots=True)
class CaseRevisionRef:
    """按准确修订引用用例；与 `contracts.PreparedRun.CaseRevisionRef` 形状一致。"""

    case_id: str
    revision: int
    digest: str

    def __post_init__(self) -> None:
        _require_text(self.case_id, "case_id")
        _require_text(self.digest, "digest")
        if self.revision < 1:
            raise ValueError("case revision must be >= 1")


def validate_case_publication(case: Case) -> None:
    """用例发布门禁；不满足则抛 `ValueError` 并指出缺失字段。

    **注意**：`assertion_basis.state == missing` **不阻止发布**——
    "发布状态与依据确认状态分别校验"（架构文档第 3 节），依据缺失阻止的是
    **进入 full 必测**，由 `validate_plan_publication()` 施加。
    """
    if not case.expected.strip():
        raise ValueError("case publication requires an expected result")
    if not case.preconditions:
        raise ValueError("case publication requires preconditions")
    if not case.independent_verification:
        raise ValueError(
            "case publication requires an independent verification method"
        )
    if not case.links.acceptance_item_ids:
        raise ValueError("case publication requires an acceptance item link")
    if not case.links.critical_path_ids and not case.links.no_critical_path_reason:
        raise ValueError(
            "case publication requires critical paths or an explicit reason"
        )


# ------------------------------------------------------------------ 范围与计划


@dataclass(frozen=True, slots=True)
class AcceptanceScope:
    """验收范围，属于计划的不可变修订数据。

    既有字段保持不变；本次新增目标、排除、依赖闭包与适用性排除。
    """

    scope_id: str
    revision: int
    name: str
    required_case_ids: frozenset[str]
    template_case_ids: frozenset[str]
    objective: str = ""
    excluded_case_ids: frozenset[str] = frozenset()
    exclusion_reasons: tuple[tuple[str, str], ...] = ()
    dependency_closure_ids: frozenset[str] = frozenset()
    applicability_exclusions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.scope_id.strip() or not self.name.strip() or self.revision < 1:
            raise ValueError("scope requires identity, name and published revision")
        if not self.template_case_ids <= self.required_case_ids:
            raise ValueError("template requirements must be included in required cases")
        for name in (
            "required_case_ids",
            "template_case_ids",
            "excluded_case_ids",
            "dependency_closure_ids",
        ):
            _require_items(sorted(getattr(self, name)), name)
        # 排除项必须给出原因；不适用项同理，且不计入分母（分母由判定侧计算）。
        for entries, label in (
            (self.exclusion_reasons, "exclusion"),
            (self.applicability_exclusions, "applicability exclusion"),
        ):
            seen: set[str] = set()
            for case_id, reason in entries:
                _require_text(case_id, f"{label} case_id")
                _require_text(reason, f"{label} reason")
                if case_id in seen:
                    raise ValueError(f"duplicate {label} reason for {case_id}")
                seen.add(case_id)
        missing_reasons = self.excluded_case_ids - {
            case_id for case_id, _ in self.exclusion_reasons
        }
        if missing_reasons:
            raise ValueError(
                f"excluded cases require a reason: {sorted(missing_reasons)}"
            )

    def validate_selection(self, mode: RunTier, selected: frozenset[str]) -> None:
        if not selected:
            raise ValueError("empty selection is not not_applicable")
        if mode == RunTier.FULL and not self.required_case_ids <= selected:
            raise ValueError("full selection must include all required cases")


@dataclass(frozen=True, slots=True)
class TemplateVersionRef:
    """按准确版本引用模板；与 `contracts.PreparedRun.TemplateVersionRef` 形状一致。"""

    template_id: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        _require_text(self.template_id, "template_id")
        _require_text(self.version, "version")
        _require_text(self.digest, "digest")


# ------------------------------------------------------------------ 覆盖契约


class TemplateApplicability(StrEnum):
    """模板项适用性轴：项目是否需要这项检查（设计文档 03 第 5.2 节）。

    与覆盖轴正交：`not_applicable` 仍需理由与证据，`unknown` 表示尚未判断，
    不能与"本次未覆盖"折叠成同一个三态。
    """

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class TemplateCoverage(StrEnum):
    """模板项覆盖轴：本次计划是否已有用例、证据和独立核验。"""

    COVERED = "covered"
    UNCOVERED = "uncovered"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class FunctionRequirement:
    """一个业务功能的必要覆盖维度承诺。

    `required_dimensions` 是设计文档 03 第 4.5 节的维度名称集合；本对象只保存
    声明值，不在这里计算覆盖结果。空集合与重复维度由
    `validate_coverage_contract()` 拒绝。
    """

    function_id: str
    required_dimensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemplateDisposition:
    """模板项的适用性/覆盖两轴处置记录（设计文档 03 第 5.2 节）。

    两轴必须分别给出：缺任一轴都算处置不完整，不能用一个三态混住
    "项目是否需要"和"本次是否覆盖"。
    """

    template_item_id: str
    applicability: TemplateApplicability
    coverage: TemplateCoverage
    #: ``not_applicable`` 必须给出理由；证据引用可为空（模板项可能由源码扫描直接判定）。
    reason: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageContract:
    """一次计划冻结的覆盖承诺：功能 × 必要维度 + 模板项处置 + 关键链路。

    本对象是**被动记录**：构造时不丢弃原始输入，完整性由纯校验函数
    `validate_coverage_contract()` 判定。这样反序列化/草稿装配可以先保留
    未补齐的内容，再在计划门禁处统一拒绝，而不是在解析时就丢掉证据。
    """

    contract_id: str
    understanding_revision: int
    function_requirements: tuple[FunctionRequirement, ...] = ()
    template_dispositions: tuple[TemplateDisposition, ...] = ()
    critical_path_ids: tuple[str, ...] = ()
    #: 用例到“功能 × 维度”的映射；旧契约没有该字段时为空元组。
    coverage_claims: tuple[CoverageClaim, ...] = ()


def _require_enum_value(
    value: object,
    members: Sequence[StrEnum],
    name: str,
) -> None:
    """接受 StrEnum 成员或其字符串值（落盘形状），其余一律拒绝。"""
    if not any(value == member for member in members):
        allowed = ", ".join(member.value for member in members)
        raise ValueError(f"{name} must be one of: {allowed}")


def validate_coverage_contract(contract: CoverageContract) -> None:
    """纯校验覆盖契约；不修改输入，不写任何状态。

    逐条规则（设计文档 03 第 4.5、5.2、5.5 节）：

    1. `contract_id` 非空、`understanding_revision >= 1`；
    2. `function_id` 非空且唯一；
    3. 每个功能的 `required_dimensions` 非空、无重复、无空值；
    4. `claim_id` 唯一；claim 的 `function_id` 必须在 `function_requirements` 中，
       `dimension` 必须在该功能的 `required_dimensions` 中；
       `case_ids` 非空由 `CoverageClaim` 的构造校验保证；
    5. 模板项处置的适用性/覆盖枚举合法（两轴分别校验）；
    6. `critical_path_ids` 无空值且唯一。

    本函数**不**判断模板项是否漏项、`full` 是否可放行或功能×维度是否真正
    已覆盖；那些是需要用例/证据/核验输入的发布门禁，不能只看本对象。
    """
    _require_text(contract.contract_id, "coverage contract_id")
    if contract.understanding_revision < 1:
        raise ValueError("coverage understanding_revision must be >= 1")

    function_ids = [
        requirement.function_id for requirement in contract.function_requirements
    ]
    for function_id in function_ids:
        _require_text(function_id, "function_id")
    _require_unique(function_ids, "function_id")

    for requirement in contract.function_requirements:
        dimensions = requirement.required_dimensions
        if not dimensions:
            raise ValueError(
                "required_dimensions must not be empty: "
                f"{requirement.function_id}"
            )
        _require_items(dimensions, "required_dimensions")
        _require_unique(dimensions, "required_dimensions")

    claim_ids = [claim.claim_id for claim in contract.coverage_claims]
    _require_unique(claim_ids, "claim_id")
    requirements_by_function = {
        requirement.function_id: requirement
        for requirement in contract.function_requirements
    }
    for claim in contract.coverage_claims:
        if not claim.case_ids:
            raise ValueError("a coverage claim must map at least one case")
        matched_requirement = requirements_by_function.get(claim.function_id)
        if matched_requirement is None:
            raise ValueError(
                "coverage claim references an unknown function: "
                f"{claim.function_id}"
            )
        if claim.dimension not in matched_requirement.required_dimensions:
            raise ValueError(
                "coverage claim dimension is not required for "
                f"{claim.function_id}: {claim.dimension}"
            )

    for disposition in contract.template_dispositions:
        _require_text(disposition.template_item_id, "template_item_id")
        _require_enum_value(
            disposition.applicability,
            tuple(TemplateApplicability),
            "template disposition applicability",
        )
        _require_enum_value(
            disposition.coverage,
            tuple(TemplateCoverage),
            "template disposition coverage",
        )

    _require_items(contract.critical_path_ids, "critical_path_ids")
    _require_unique(contract.critical_path_ids, "critical_path_ids")


@dataclass(frozen=True, slots=True)
class Plan:
    """冻结的计划。

    **不包含结论上限字段**：结论上限只由档位派生（架构文档第 4 节），
    冻结一份会产生第二个真值来源。
    """

    plan_id: str
    revision: int
    scope: AcceptanceScope
    case_revisions: tuple[CaseRevisionRef, ...]
    rule_revisions: tuple[RuleRevisionRef, ...]
    template_versions: tuple[TemplateVersionRef, ...]
    run_tier: RunTier
    initial_driver: RunDriver
    status: PlanPublicationStatus = PlanPublicationStatus.DRAFT
    confirmation_id: str | None = None
    record_revision: int | None = None
    #: 可选覆盖契约。追加在末尾并带默认值，保持既有构造方式兼容；
    #: 其完整性由 `validate_coverage_contract()` 判定，本字段存在不代表可用。
    coverage_contract: CoverageContract | None = None

    def __post_init__(self) -> None:
        _require_text(self.plan_id, "plan_id")
        if self.revision < 1:
            raise ValueError("plan revision must be >= 1")
        if self.record_revision is not None and self.record_revision < 1:
            raise ValueError("plan record revision must be >= 1")
        if not self.case_revisions:
            raise ValueError("a plan must freeze at least one case revision")
        _require_unique(
            [ref.case_id for ref in self.case_revisions], "case_id"
        )
        _require_unique(
            [ref.rule_id for ref in self.rule_revisions], "rule_id"
        )
        _require_unique(
            [ref.template_id for ref in self.template_versions], "template_id"
        )
        if self.status is PlanPublicationStatus.PUBLISHED:
            if self.confirmation_id is None:
                raise ValueError("a published plan requires a confirmation id")
            _require_text(self.confirmation_id, "confirmation_id")
        elif self.confirmation_id is not None:
            _require_text(self.confirmation_id, "confirmation_id")


def validate_coverage_completeness(
    contract: CoverageContract,
    *,
    plan_revision: int,
    frozen_case_ids: Iterable[str],
) -> None:
    """校验覆盖契约的完整性与引用；发布、准备和运行修订共用同一规则。

    结构校验之后，要求每个功能×必要维度至少一条 claim，claim 指向本次计划
    修订，且 claim 引用的用例都在冻结集合内。本函数不判断执行证据或核验结果。
    """
    validate_coverage_contract(contract)
    frozen_ids = set(frozen_case_ids)
    for requirement in contract.function_requirements:
        for dimension in requirement.required_dimensions:
            if not any(
                claim.function_id == requirement.function_id
                and claim.dimension == dimension
                for claim in contract.coverage_claims
            ):
                raise ValueError(
                    "coverage claim missing for "
                    f"{requirement.function_id}×{dimension}"
                )
    for claim in contract.coverage_claims:
        if claim.plan_revision != plan_revision:
            raise ValueError(
                "coverage claim plan revision differs from the plan: "
                f"{claim.claim_id} ({claim.plan_revision} != {plan_revision})"
            )
        missing_cases = set(claim.case_ids) - frozen_ids
        if missing_cases:
            raise ValueError(
                f"coverage claim references unfrozen cases: {sorted(missing_cases)}"
            )


def validate_plan_publication(plan: Plan, cases: Sequence[Case]) -> None:
    """计划发布门禁；不满足则抛 `ValueError`。

    `cases` 是计划冻结的用例修订对应的实际用例，用于检查依据状态与独立核验方式。

    门禁逐条：

    0. 带 `coverage_contract` 时先校验其结构完整性，并校验每个功能×必要维度至少一条
       claim、claim 引用的用例都在冻结集合内；没有契约时保持旧行为；
    1. `T ⊄ M` 拒绝；
    2. 冻结的每个用例都必须提供实际用例（缺一即拒绝）；
    3. 提供的用例必须与冻结的**修订号**一致（B-PUBLICATION-01：不能拿 `@2` 顶 `@1`）；
    4. **必测项必须全部有冻结引用**——不得用集合交集把没有冻结引用的必测项静默滤掉；
    5. 冻结必测集合中包含依据缺失的用例 → 拒绝（"缺依据禁止 full 必测"）；
    6. 冻结用例缺独立核验方式 → 拒绝；
    7. 排除项或适用性排除缺理由 → 拒绝（构造 `AcceptanceScope` 时已强制）。
    """
    scope = plan.scope
    contract = plan.coverage_contract
    if contract is not None:
        validate_coverage_completeness(
            contract,
            plan_revision=plan.revision,
            frozen_case_ids=(ref.case_id for ref in plan.case_revisions),
        )
        if plan.run_tier is RunTier.FULL:
            for disposition in contract.template_dispositions:
                if disposition.applicability is TemplateApplicability.UNKNOWN:
                    raise ValueError(
                        "unknown template applicability blocks full validation: "
                        f"{disposition.template_item_id}"
                    )
                if (
                    disposition.applicability is TemplateApplicability.APPLICABLE
                    and disposition.coverage is not TemplateCoverage.COVERED
                ):
                    raise ValueError(
                        "applicable template item is not covered for full validation: "
                        f"{disposition.template_item_id} ({disposition.coverage.value})"
                    )
    if not scope.template_case_ids <= scope.required_case_ids:
        raise ValueError("template requirements must be included in required cases")

    frozen_by_id = {ref.case_id: ref for ref in plan.case_revisions}
    provided_by_id = {case.case_id: case for case in cases}

    missing_provided = sorted(set(frozen_by_id) - set(provided_by_id))
    if missing_provided:
        raise ValueError(
            f"frozen case revisions are not provided: {missing_provided}"
        )

    for case_id, ref in sorted(frozen_by_id.items()):
        provided = provided_by_id[case_id]
        if provided.revision != ref.revision:
            raise ValueError(
                "a provided case does not match the frozen revision: "
                f"{case_id} (frozen {ref.revision}, provided {provided.revision})"
            )

    missing_frozen = sorted(scope.required_case_ids - set(frozen_by_id))
    if missing_frozen:
        raise ValueError(
            f"required cases have no frozen case revision: {missing_frozen}"
        )

    for case_id in sorted(scope.required_case_ids):
        case = provided_by_id[case_id]
        if case.assertion_basis.state is AssertionBasisState.MISSING:
            raise ValueError(
                f"a case with a missing assertion basis must not be required: {case_id}"
            )
        if not case.independent_verification:
            raise ValueError(
                f"a required case needs an independent verification method: {case_id}"
            )
