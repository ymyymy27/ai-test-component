"""运行态覆盖投影：把本轮 Coverage 集合投影成 `FunctionCoverage`。

维度状态规则（与计划预览不同）只有在有运行事实时才成立：
``unsupported`` 优先；无 claim / 无用例为 ``not_proven``；全部用例已验证为
``proven``；有已验证或已执行/复用为 ``partial``。遍历与用例合并由
`domain.review.function_coverage.project_coverage` 完成。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from aitest.domain.planning.plans import CoverageContract, validate_coverage_contract
from aitest.domain.planning.understanding import CoverageClaim, CoverageClaimStatus
from aitest.domain.review.function_coverage import (
    NO_CLAIM_GAP_REASON,
    CoverageStatus,
    FunctionCoverage,
    project_coverage,
)
from aitest.domain.review.reports import Coverage

#: claim 存在但用例并集为空时的缺口原因；只作防御性分支。
NO_CASE_GAP_REASON = "no coverage case"

__all__ = [
    "NO_CLAIM_GAP_REASON",
    "NO_CASE_GAP_REASON",
    "build_run_function_coverage",
]


def _case_ids_text(case_ids: Iterable[str]) -> str:
    return ", ".join(sorted(case_ids))


def _gap_fragments(dimension: str, case_ids: frozenset[str], coverage: Coverage) -> list[str]:
    missing = case_ids - coverage.verified
    out_of_scope = missing - coverage.selected
    not_executed = (missing & coverage.selected) - (coverage.executed | coverage.reused)
    not_verified = missing & (coverage.executed | coverage.reused)
    fragments: list[str] = []
    if out_of_scope:
        fragments.append(
            f"{dimension}: cases outside selected scope: {_case_ids_text(out_of_scope)}"
        )
    if not_executed:
        fragments.append(f"{dimension}: cases not executed: {_case_ids_text(not_executed)}")
    if not_verified:
        fragments.append(
            f"{dimension}: cases not independently verified: {_case_ids_text(not_verified)}"
        )
    return fragments


def _resolver(coverage: Coverage):
    def resolve(
        _requirement: object,
        dimension: str,
        claims: list[CoverageClaim],
        case_ids: frozenset[str],
    ) -> tuple[CoverageStatus, tuple[str, ...]]:
        if not claims:
            return CoverageStatus.NOT_PROVEN, (NO_CLAIM_GAP_REASON,)
        if any(claim.status is CoverageClaimStatus.UNSUPPORTED for claim in claims):
            return CoverageStatus.UNSUPPORTED, (f"{dimension}: unsupported",)
        if not case_ids:
            return CoverageStatus.NOT_PROVEN, (f"{dimension}: {NO_CASE_GAP_REASON}",)
        verified = case_ids & coverage.verified
        executed_or_reused = case_ids & (coverage.executed | coverage.reused)
        if case_ids <= coverage.verified:
            status = CoverageStatus.PROVEN
        elif verified or executed_or_reused:
            status = CoverageStatus.PARTIAL
        else:
            status = CoverageStatus.NOT_PROVEN
        if status is CoverageStatus.PROVEN:
            return status, ()
        return status, tuple(_gap_fragments(dimension, case_ids, coverage))

    return resolve


def build_run_function_coverage(
    contract: CoverageContract,
    *,
    coverage: Coverage,
    plan_revision: int,
    run_id: str,
    projected_at: datetime | None = None,
) -> tuple[FunctionCoverage, ...]:
    """按冻结覆盖契约和本轮覆盖集合生成运行态功能覆盖投影。"""

    if plan_revision < 1:
        raise ValueError("plan_revision must be >= 1")
    if not run_id.strip():
        raise ValueError("run_id must not be empty")
    validate_coverage_contract(contract)
    moment = projected_at if projected_at is not None else datetime.now(UTC)
    return project_coverage(
        contract,
        projected_at=moment,
        plan_revision=plan_revision,
        run_id=run_id,
        coverage_id=lambda function_id: f"run-coverage:{run_id}:{function_id}",
        resolve_dimension=_resolver(coverage),
    )
