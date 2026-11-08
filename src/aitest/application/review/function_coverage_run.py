"""运行态覆盖投影：把本轮 Coverage 集合投影成 `FunctionCoverage`。

合同来源：设计文档 03 第 4.4—4.6、8.2—8.3 节（功能 × 必要维度状态、覆盖
收据），以及架构文档《03-报告与缺陷》第 2 节（`FunctionCoverage` 字段）。

本模块只做投影：

- 覆盖事实只取本轮 `Coverage` 的 S/E/R/V 集合；`M` 与业务结论、证据等级由
  其他领域规则计算，这里不判断；
- 不修改 `Coverage` 集合、不读文件、不写记录，也不生成报告 DTO；
- 每个“功能 × 必要维度”都出现在结果里，函数顺序与维度顺序保持契约顺序；
- 没有功能承诺时返回空元组，不伪造一条“已覆盖”。

状态规则（与计划预览分开）：

| 条件 | 维度状态 |
| --- | --- |
| 任一匹配 claim 为 ``unsupported`` | ``unsupported`` |
| 没有 claim | ``not_proven`` |
| claim 的用例并集为空 | ``not_proven`` |
| 全部 claim 用例都在 `Coverage.verified` | ``proven`` |
| 有已验证或已执行/复用用例 | ``partial`` |
| 其余 | ``not_proven`` |

`unsupported` 先于其他规则：已登记的能力缺口不会被同一维度的其他 claim 或
执行结果掩盖。claim 的用例不在本轮 `selected` 时，`Coverage` 的集合不变量
保证它不会进入 E/R/V，因此不能凭空算成已验证；缺口原因会点名这些用例。

``not_applicable`` 是计划/理解层的维度适用性处置，不是运行事实；运行态投影
只输出适用维度，不会把未执行或不适用混成一个状态。若上游把某个维度标为
不适用，应在本轮 `required_dimensions` 之外处理，而不是交给本函数猜测。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from aitest.domain.planning.plans import (
    CoverageContract,
    validate_coverage_contract,
)
from aitest.domain.planning.understanding import CoverageClaim, CoverageClaimStatus
from aitest.domain.review.function_coverage import CoverageStatus, FunctionCoverage
from aitest.domain.review.reports import Coverage

#: 函数内任一必要维度没有 claim 时的缺口原因；与计划预览保持同一原文，
#: 避免确认页与报告对同一缺口各说一套。
NO_CLAIM_GAP_REASON = "no coverage claim"
#: claim 存在但用例并集为空时的缺口原因；正常构造的 claim 不允许为空，
#: 这里只作为防御性分支，不能把空用例当成“已验证”。
NO_CASE_GAP_REASON = "no coverage case"


def _case_ids_text(case_ids: Iterable[str]) -> str:
    """按稳定顺序展示用例编号，保证同一输入得到同一缺口说明。"""

    return ", ".join(sorted(case_ids))


def _gap_fragments(
    dimension: str, case_ids: frozenset[str], coverage: Coverage
) -> list[str]:
    """列出该维度尚未证明的用例，并按“范围/执行/核验”归因。"""

    missing = case_ids - coverage.verified
    out_of_scope = missing - coverage.selected
    not_executed = (missing & coverage.selected) - (
        coverage.executed | coverage.reused
    )
    not_verified = missing & (coverage.executed | coverage.reused)

    fragments: list[str] = []
    if out_of_scope:
        fragments.append(
            f"{dimension}: cases outside selected scope: "
            f"{_case_ids_text(out_of_scope)}"
        )
    if not_executed:
        fragments.append(
            f"{dimension}: cases not executed: {_case_ids_text(not_executed)}"
        )
    if not_verified:
        fragments.append(
            f"{dimension}: cases not independently verified: "
            f"{_case_ids_text(not_verified)}"
        )
    return fragments


def build_run_function_coverage(
    contract: CoverageContract,
    *,
    coverage: Coverage,
    plan_revision: int,
    run_id: str,
) -> tuple[FunctionCoverage, ...]:
    """按冻结覆盖契约和本轮覆盖集合生成运行态功能覆盖投影。

    逐条规则（设计文档 03 第 4.5—4.6、8.2—8.3 节）：

    1. 先校验契约结构：claim 的 ``function_id`` 必须存在，``dimension`` 必须
       属于该功能的 ``required_dimensions``；否则无法把 claim 计入任何必要
       维度，直接拒绝而不是静默忽略；
    2. 对每个 ``FunctionRequirement`` 的每个 ``required_dimensions`` 项：

       - 没有匹配 claim → ``not_proven``，功能级缺口原文
         ``"no coverage claim"``；
       - 任一匹配 claim 为 ``unsupported`` → ``unsupported``；
       - 取匹配 claim 的 ``case_ids`` 并集；并集为空 → ``not_proven``；
       - ``verified = case_ids ∩ coverage.verified``；
       - ``executed_or_reused = case_ids ∩ (coverage.executed |
         coverage.reused)``；
       - ``case_ids`` 全部在 ``coverage.verified`` 中 → ``proven``；
       - 否则任有已验证或已执行/复用用例 → ``partial``；
       - 否则 → ``not_proven``。

    3. 每个功能生成一条 `FunctionCoverage`：``plan_revision`` / ``run_id``
       为入参，``case_ids`` 取该功能全部匹配 claim 的用例并集，
       ``evidence_refs`` / ``verification_refs`` 为空——本投影不携带具体证据
       引用；``overall_coverage`` 由维度状态派生，不能手填；
    4. 结果顺序保持 ``contract.function_requirements`` 顺序；没有功能时返回
       空元组。

    claim 用例不在本轮 ``selected`` 时，`Coverage` 的构造不变量保证它不会
    出现在 E/R/V 中；该维度只能得到 ``partial`` 或 ``not_proven``，缺口说明
    会点名落在选择范围外的用例，不能把它算成已验证。
    """

    if plan_revision < 1:
        raise ValueError("plan_revision must be >= 1")
    if not run_id.strip():
        raise ValueError("run_id must not be empty")
    validate_coverage_contract(contract)

    claims_by_dimension: dict[tuple[str, str], list[CoverageClaim]] = {}
    for claim in contract.coverage_claims:
        claims_by_dimension.setdefault(
            (claim.function_id, claim.dimension), []
        ).append(claim)

    projected_at = datetime.now(UTC)
    projections: list[FunctionCoverage] = []
    for requirement in contract.function_requirements:
        dimensions: dict[str, CoverageStatus] = {}
        function_case_ids: set[str] = set()
        gaps: list[str] = []
        for dimension in requirement.required_dimensions:
            claims = claims_by_dimension.get(
                (requirement.function_id, dimension), []
            )
            if not claims:
                dimensions[dimension] = CoverageStatus.NOT_PROVEN
                if NO_CLAIM_GAP_REASON not in gaps:
                    gaps.append(NO_CLAIM_GAP_REASON)
                continue

            dimension_case_ids: set[str] = set()
            for claim in claims:
                dimension_case_ids.update(claim.case_ids)
            function_case_ids.update(dimension_case_ids)

            if any(
                claim.status is CoverageClaimStatus.UNSUPPORTED
                for claim in claims
            ):
                dimensions[dimension] = CoverageStatus.UNSUPPORTED
                gaps.append(f"{dimension}: unsupported")
                continue

            case_ids = frozenset(dimension_case_ids)
            if not case_ids:
                dimensions[dimension] = CoverageStatus.NOT_PROVEN
                gaps.append(f"{dimension}: {NO_CASE_GAP_REASON}")
                continue

            verified = case_ids & coverage.verified
            executed_or_reused = case_ids & (
                coverage.executed | coverage.reused
            )
            if case_ids <= coverage.verified:
                dimensions[dimension] = CoverageStatus.PROVEN
            elif verified or executed_or_reused:
                dimensions[dimension] = CoverageStatus.PARTIAL
            else:
                dimensions[dimension] = CoverageStatus.NOT_PROVEN

            if dimensions[dimension] is not CoverageStatus.PROVEN:
                gaps.extend(_gap_fragments(dimension, case_ids, coverage))

        projections.append(
            FunctionCoverage(
                coverage_id=(
                    f"run-coverage:{run_id}:{requirement.function_id}"
                ),
                function_id=requirement.function_id,
                plan_revision=plan_revision,
                run_id=run_id,
                coverage_by_dimension=dimensions,
                updated_at=projected_at,
                case_ids=frozenset(function_case_ids),
                evidence_refs=(),
                verification_refs=(),
                gap_reason="; ".join(gaps) if gaps else None,
            )
        )
    return tuple(projections)
