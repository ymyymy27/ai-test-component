"""Finite query contracts; callers cannot request an implicit full scan."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .identity import RequestId
from .versions import PROTOCOL_VERSION, ProtocolVersion

QueryName = Literal[
    "record.get",
    "records.list",
    "events.list",
    "workspace.status",
    "integrity.check",
]
SortKey = Literal[
    "aggregate_kind",
    "record_id",
    "revision",
    "commit_sequence",
    "published_sequence",
    "updated_sequence",
]

#: 报告列表允许的业务结果筛选（reports.latest，存储与恢复第 13 节）。
#: 报告“状态”明确指 business_outcome，不与 execution_state/有效性混用。
BusinessOutcomeFilter = Literal["passed", "failed", "incomplete", "not_applicable"]

#: 问题列表视图：OPEN 仅含尚需处理项，ALL 为显式全量分支。
IssueView = Literal["OPEN", "ALL"]

#: 问题列表单主筛选维度；可再叠加 severity，固定 14 种掩码。
IssueFacet = Literal[
    "NONE",
    "module",
    "layer",
    "review_state",
    "workflow_state",
    "disposition",
    "blocking",
]


class QueryUnsupportedFilter(ValueError):
    code = "QUERY_UNSUPPORTED_FILTER"

    def __init__(self, reason: str) -> None:
        super().__init__(f"{self.code}: {reason}")


class QuerySpec(BaseModel):
    """有限查询定义：固定允许筛选组合、排序键与有界页大小。

    不接收任意表达式；报告业务结果与问题 facet 各走独立索引目录
    （存储与恢复第 13 节）。所有筛选字段参与游标 qid 绑定，改变任一
    条件都必须刷新游标，不能复用旧快照。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    project_id: str = Field(min_length=1, max_length=128)
    aggregate_kind: str | None = Field(default=None, min_length=1, max_length=128)
    record_id: str | None = Field(default=None, min_length=1, max_length=128)
    revision: int | None = Field(default=None, ge=1)
    sort: SortKey = "aggregate_kind"
    descending: bool = False
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = Field(default=None, min_length=1, max_length=256)

    # ----- reports.latest / reports.by_id 固定目录 ---------------------
    business_outcome: BusinessOutcomeFilter | None = None
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    report_id: str | None = Field(default=None, min_length=1, max_length=128)

    # ----- issues.list 固定 Schema（14 种掩码 × OPEN/ALL） -------------
    view: IssueView | None = None
    facet: IssueFacet = "NONE"
    facet_value: str | None = Field(default=None, min_length=1, max_length=128)
    severity: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def _validate_finite_combinations(self) -> QuerySpec:
        # facet 非 NONE 必须给类型匹配的 facet_value；NONE 禁止带值。
        if self.facet == "NONE":
            if self.facet_value is not None:
                raise ValueError("facet_value is forbidden when facet=NONE")
        elif self.facet_value is None:
            raise ValueError(f"facet_value is required for facet={self.facet}")
        # 问题目录字段只在 issues.list 组合内出现；facet_value 不得脱离
        # facet 单独给出。facet 非 NONE 或仅按 severity 筛选都进入问题
        # 目录，view 缺省按 OPEN（OPEN 是问题列表默认范围）。
        if self.view is None and (self.facet != "NONE" or self.severity is not None):
            object.__setattr__(self, "view", "OPEN")
        self.ensure_finite_route()
        return self

    def ensure_finite_route(self) -> None:
        """Every supplied selector must belong to the chosen fixed directory."""
        if self.view is not None:
            if self.aggregate_kind not in (None, "issue") or any(
                value is not None
                for value in (
                    self.record_id,
                    self.revision,
                    self.report_id,
                    self.run_id,
                    self.business_outcome,
                )
            ):
                raise QueryUnsupportedFilter("issues.list cannot mix record/report selectors")
            return
        if self.report_id is not None or self.run_id is not None:
            if (
                self.aggregate_kind not in (None, "report")
                or self.record_id is not None
                or self.revision is not None
                or self.report_id is not None
                and self.run_id is not None
            ):
                raise QueryUnsupportedFilter("reports.by_id requires one typed report/run identity")
            return
        if self.record_id is not None and self.aggregate_kind is None:
            raise QueryUnsupportedFilter("record identity requires aggregate_kind")
        if self.revision is not None and (self.record_id is None or self.aggregate_kind is None):
            raise QueryUnsupportedFilter("revision requires an exact typed record identity")
        if self.business_outcome is not None and (
            self.aggregate_kind not in (None, "report") or self.record_id is not None
        ):
            raise QueryUnsupportedFilter("business_outcome belongs to reports.latest/by_id")


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    request_id: RequestId
    name: QueryName
    workspace_id: str = Field(min_length=1, max_length=128)
    spec: QuerySpec | None = None
