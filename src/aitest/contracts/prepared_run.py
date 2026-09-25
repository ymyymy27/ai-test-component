"""Versioned PreparedRun contract: the single business deliverable of package B.

Frozen at prepare time; read by C and displayed by D. Design rationale and field
semantics: `docs/文档-feix-a/B包/04-PreparedRun设计说明.md`.

The run vocabulary (tier / driver / conclusion ceiling) is declared here at the
contract boundary and in `aitest.domain.planning.plans` for domain rules. The two
value sets are locked together by `tests/contracts/test_run_vocabulary.py`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------- 词汇表


class RunTierFact(StrEnum):
    QUICK = "quick"
    ON_DEMAND = "on_demand"
    FULL = "full"


class RunDriverFact(StrEnum):
    PLANNED = "planned"
    STEPWISE = "stepwise"


class ConclusionCeilingFact(StrEnum):
    PARTIAL = "partial"
    PASSABLE = "passable"


class AssertionBasisStateFact(StrEnum):
    MISSING = "missing"
    PRESENT_UNCONFIRMED = "present_unconfirmed"
    CONFIRMED = "confirmed"


class BindingFormFact(StrEnum):
    GIT = "git"
    PLAIN = "plain"


class PreparedRunStatusFact(StrEnum):
    """快照自身状态。

    "依据需重新准备" 不是快照状态：快照不可变，该结论由应用用例按
    `invalidation_rules` 比较当前来源修订后派生（架构文档第 11 节）。
    """

    PREPARED = "prepared"
    BLOCKED = "blocked"


def conclusion_ceiling_for(tier: RunTierFact) -> ConclusionCeilingFact:
    """结论上限只由档位派生（架构文档《01-项目与计划》第 4 节）。"""
    if tier == RunTierFact.FULL:
        return ConclusionCeilingFact.PASSABLE
    return ConclusionCeilingFact.PARTIAL


# ----------------------------------------------------------------- 引用结构


class SnapshotRef(ContractModel):
    source_snapshot_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    content_identity: str = Field(min_length=1)


class EnvironmentRefFact(ContractModel):
    environment_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    isolation_mode: str = Field(min_length=1)
    interpreter_identity: str = Field(min_length=1)
    dependency_set_digest: str = Field(min_length=1)


class ExecutionSourceBinding(ContractModel):
    """prepare 冻结的预期执行来源；实际来源由 C 在 start 时核对。"""

    registered_entry: str = Field(min_length=1)
    entry_arguments: tuple[str, ...] = Field(default_factory=tuple)
    cwd_mapping: str = Field(min_length=1)
    allowed_env_keys: tuple[str, ...] = Field(default_factory=tuple)
    secret_refs: tuple[str, ...] = Field(default_factory=tuple)
    test_config_ref: str = Field(min_length=1)
    adapter_versions: dict[str, str] = Field(default_factory=dict)
    resolved_input_digest: str = Field(min_length=1)


class PlanRevisionRef(ContractModel):
    revision_id: str = Field(min_length=1)
    revision_no: int = Field(ge=1)
    digest: str = Field(min_length=1)


class RuleVersionRef(ContractModel):
    rule_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    digest: str = Field(min_length=1)


class TemplateVersionRef(ContractModel):
    template_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    digest: str = Field(min_length=1)


class CaseRevisionRef(ContractModel):
    case_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    digest: str = Field(min_length=1)


# ------------------------------------------------------------- 范围与依据


class SkippedScopeEntry(ContractModel):
    case_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ExclusionEntry(ContractModel):
    case_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ConfirmationRef(ContractModel):
    confirmation_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    basis_revision: int = Field(ge=1)
    confirmed_at_commit: str = Field(min_length=1)


class AssertionBasisEntry(ContractModel):
    case_id: str = Field(min_length=1)
    basis_revision: int = Field(ge=1)
    basis_text_digest: str = Field(min_length=1)
    assertion_basis_state: AssertionBasisStateFact
    confirmation_refs: tuple[ConfirmationRef, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _confirmation_matches_state(self) -> Self:
        has_confirmation = bool(self.confirmation_refs)
        if self.assertion_basis_state is AssertionBasisStateFact.CONFIRMED and not has_confirmation:
            raise ValueError("confirmed basis requires at least one confirmation reference")
        if self.assertion_basis_state is not AssertionBasisStateFact.CONFIRMED and has_confirmation:
            raise ValueError("only a confirmed basis may carry confirmation references")
        for reference in self.confirmation_refs:
            if reference.case_id != self.case_id:
                raise ValueError("confirmation reference must target the same case")
            if reference.basis_revision != self.basis_revision:
                raise ValueError("confirmation reference must target the same basis revision")
        return self


class FrozenCaseStep(ContractModel):
    step_id: str = Field(min_length=1)
    layer: Literal["L1", "L2", "L3"]
    objective: str = Field(min_length=1)
    expected: str = Field(min_length=1)


class CaseLinks(ContractModel):
    acceptance_item_ids: tuple[str, ...] = Field(default_factory=tuple)
    module_ids: tuple[str, ...] = Field(default_factory=tuple)
    environment_ids: tuple[str, ...] = Field(default_factory=tuple)
    critical_path_ids: tuple[str, ...] = Field(default_factory=tuple)


class FrozenCase(ContractModel):
    case_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    layer: Literal["L1", "L2", "L3"]
    required: bool
    independent_verification: str = Field(min_length=1)
    mock_scope: tuple[str, ...] = Field(default_factory=tuple)
    importance: str = Field(min_length=1)
    steps: tuple[FrozenCaseStep, ...] = Field(min_length=1)
    links: CaseLinks


# ------------------------------------------------------- 授权、策略与缺口


class AuthorizationRequirement(ContractModel):
    """声明需要授权的动作；授权记录本身由 C 执行并持久化。"""

    action_id: str = Field(min_length=1)
    requires_side_effect: bool
    credential_scope: str = Field(min_length=1)
    resolved_input_digest: str | None = None
    pending_reason: str | None = None

    @model_validator(mode="after")
    def _resolution_is_explicit(self) -> Self:
        if self.resolved_input_digest is None:
            if not self.pending_reason:
                raise ValueError("unresolved authorization requires a pending reason")
        elif self.pending_reason is not None:
            raise ValueError("resolved authorization must not carry a pending reason")
        return self


class InvalidationRule(ContractModel):
    source_kind: str = Field(min_length=1)
    description: str = Field(min_length=1)


class BlockingReason(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class GapEntry(ContractModel):
    gap_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    blocking: bool
    detail: str = Field(min_length=1)


# ------------------------------------------------------------------ 顶层


class PreparedRun(ContractModel):
    schema_version: Literal["aitest.prepared-run/1.0"] = "aitest.prepared-run/1.0"

    # 身份与幂等
    prepared_run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    binding_id: str = Field(min_length=1)
    binding_revision: int = Field(ge=1)
    binding_form: BindingFormFact
    client_id: str = Field(min_length=1)
    prepare_request_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    payload_hash: str = Field(min_length=1)
    status: PreparedRunStatusFact
    created_at: datetime
    created_at_commit: str = Field(min_length=1)

    # 来源
    snapshot: SnapshotRef
    git_base_commit: str | None = None
    git_diff_digest: str | None = None
    plain_manifest_digest: str | None = None
    selected_paths: tuple[str, ...] = Field(min_length=1)
    exclusion_rules: tuple[str, ...] = Field(default_factory=tuple)
    refetch_dependencies: tuple[str, ...] = Field(default_factory=tuple)

    # 环境与执行来源
    environment: EnvironmentRefFact
    execution_source: ExecutionSourceBinding

    # 计划与修订引用
    plan_revision: PlanRevisionRef
    acceptance_scope_revision: int = Field(ge=1)
    rule_versions: tuple[RuleVersionRef, ...] = Field(default_factory=tuple)
    template_versions: tuple[TemplateVersionRef, ...] = Field(default_factory=tuple)
    case_revisions: tuple[CaseRevisionRef, ...] = Field(default_factory=tuple)

    # 档位、范围与门禁
    run_tier: RunTierFact
    initial_driver: RunDriverFact
    conclusion_ceiling: ConclusionCeilingFact
    template_required_case_ids: tuple[str, ...] = Field(default_factory=tuple)
    frozen_required_case_ids: tuple[str, ...] = Field(default_factory=tuple)
    selected_case_ids: tuple[str, ...] = Field(default_factory=tuple)
    skipped_scope: tuple[SkippedScopeEntry, ...] = Field(default_factory=tuple)
    applicability_exclusions: tuple[ExclusionEntry, ...] = Field(default_factory=tuple)

    # 依据与用例
    assertion_bases: tuple[AssertionBasisEntry, ...] = Field(default_factory=tuple)
    frozen_cases: tuple[FrozenCase, ...] = Field(default_factory=tuple)

    # 授权前置、出站策略与失效规则
    authorization_requirements: tuple[AuthorizationRequirement, ...] = Field(
        default_factory=tuple
    )
    model_outbound_policy_revision: int | None = None
    source_snippets_enabled: bool = False
    invalidation_rules: tuple[InvalidationRule, ...] = Field(default_factory=tuple)

    # 缺口与阻塞
    blocking_reasons: tuple[BlockingReason, ...] = Field(default_factory=tuple)
    gaps: tuple[GapEntry, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        self._check_source_identity()
        self._check_ceiling_is_derived()
        self._check_scope_references()
        if self.status is not PreparedRunStatusFact.BLOCKED:
            self._check_admission_gates()
        elif not self.blocking_reasons:
            raise ValueError("blocked status requires at least one blocking reason")
        return self

    def _check_source_identity(self) -> None:
        if self.binding_form is BindingFormFact.GIT:
            if not self.git_base_commit or not self.git_diff_digest:
                raise ValueError("git binding requires a base commit and a diff digest")
            if self.plain_manifest_digest is not None:
                raise ValueError("git binding must not carry a plain manifest digest")
            return
        if self.git_base_commit is not None or self.git_diff_digest is not None:
            raise ValueError("plain binding must omit git fields rather than null them")
        if not self.plain_manifest_digest:
            raise ValueError("plain binding requires a manifest digest")

    def _check_ceiling_is_derived(self) -> None:
        expected = conclusion_ceiling_for(self.run_tier)
        if self.conclusion_ceiling is not expected:
            raise ValueError(
                "conclusion_ceiling must be derived from run_tier: "
                f"{self.run_tier.value} -> {expected.value}"
            )

    def _check_scope_references(self) -> None:
        known = {case.case_id for case in self.frozen_cases}
        referenced = (
            set(self.template_required_case_ids)
            | set(self.frozen_required_case_ids)
            | set(self.selected_case_ids)
        )
        unknown = referenced - known
        if unknown:
            raise ValueError(f"scope references unknown cases: {sorted(unknown)}")

        revision_ids = [case.case_id for case in self.frozen_cases]
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("frozen case IDs must be unique")

        basis_ids = [entry.case_id for entry in self.assertion_bases]
        if len(basis_ids) != len(set(basis_ids)):
            raise ValueError("assertion basis entries must be unique per case")

    def _check_admission_gates(self) -> None:
        template = set(self.template_required_case_ids)
        required = set(self.frozen_required_case_ids)
        selected = set(self.selected_case_ids)

        if not template <= required:
            raise ValueError("template requirements must be included in frozen required cases")

        if self.run_tier is RunTierFact.FULL:
            if not required <= selected:
                raise ValueError("full tier selection must include every frozen required case")
            if self.skipped_scope:
                raise ValueError("full tier must not skip any scope")

        missing = {
            entry.case_id
            for entry in self.assertion_bases
            if entry.assertion_basis_state is AssertionBasisStateFact.MISSING
        }
        inadmissible = missing & required
        if inadmissible:
            raise ValueError(
                "cases with a missing assertion basis cannot be frozen required: "
                f"{sorted(inadmissible)}"
            )

        cases_without_basis = required - {entry.case_id for entry in self.assertion_bases}
        if cases_without_basis:
            raise ValueError(
                f"frozen required cases need an assertion basis entry: {sorted(cases_without_basis)}"
            )


__all__ = [
    "AssertionBasisEntry",
    "AssertionBasisStateFact",
    "AuthorizationRequirement",
    "BindingFormFact",
    "BlockingReason",
    "CaseLinks",
    "CaseRevisionRef",
    "ConclusionCeilingFact",
    "ConfirmationRef",
    "ContractModel",
    "EnvironmentRefFact",
    "ExclusionEntry",
    "ExecutionSourceBinding",
    "FrozenCase",
    "FrozenCaseStep",
    "GapEntry",
    "InvalidationRule",
    "PlanRevisionRef",
    "PreparedRun",
    "PreparedRunStatusFact",
    "RuleVersionRef",
    "RunDriverFact",
    "RunTierFact",
    "SkippedScopeEntry",
    "SnapshotRef",
    "TemplateVersionRef",
    "conclusion_ceiling_for",
]
