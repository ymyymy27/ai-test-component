"""只读查询动作

原先在 `dispatch/actions/handlers.py`；按动作族拆出，registry 直接绑定。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from aitest.application.dispatch.errors import BUseCaseError as BUseCaseError
from aitest.application.dispatch.params import (
    _as_limit,
    _as_text,
    _bool_of,
    _command_parameters,
    _command_project_id,
    _optional_text,
    _revision_of,
)
from aitest.application.planning.substrate import (
    AggregateKind,
    RecordQuery,
)

_AGGREGATE_KINDS: frozenset[str] = frozenset(
    {
        "project",
        "binding",
        "module",
        "dependency_set",
        "task",
        "delivery",
        "delivery_submission",
        "acceptance_item",
        "environment",
        "source_snapshot",
        "source_pin_intent",
        "source_binding_current",
        "template_ref",
        "generated_content",
        "rule_draft",
        "rule_version",
        "case",
        "case_link",
        "plan",
        "acceptance_scope",
        "preparation_record",
        "model_outbound_policy",
        "model_outbound_request",
        "prepared_run",
        "project_view_preference",
        # 人工补证记录（只含人工描述与引用，不含证据正文）：列表可读，正文按引用取。
        "manual_evidence",
        # 本地复核记录：按报告修订独立保存，供复核追溯与导出引用。
        "local_review",
        # 复用选择记录：面板读取当前目标运行的已选复用修订后再提交新选择。
        "reuse_decision",
        # 报告修订：面板「问题与报告」页按项目列报告；索引层已有 reports 分区。
        "report",
    }
)

if TYPE_CHECKING:
    from aitest.application.usecase_registry import BUseCaseDependencies
_AGGREGATE_KINDS: frozenset[str] = frozenset(
    {
        "project",
        "binding",
        "module",
        "dependency_set",
        "task",
        "delivery",
        "delivery_submission",
        "acceptance_item",
        "environment",
        "source_snapshot",
        "source_pin_intent",
        "source_binding_current",
        "template_ref",
        "generated_content",
        "rule_draft",
        "rule_version",
        "case",
        "case_link",
        "plan",
        "acceptance_scope",
        "preparation_record",
        "model_outbound_policy",
        "model_outbound_request",
        "prepared_run",
        "project_view_preference",
        # 人工补证记录（只含人工描述与引用，不含证据正文）：列表可读，正文按引用取。
        "manual_evidence",
        # 本地复核记录：按报告修订独立保存，供复核追溯与导出引用。
        "local_review",
        # 复用选择记录：面板读取当前目标运行的已选复用修订后再提交新选择。
        "reuse_decision",
        # 报告修订：面板「问题与报告」页按项目列报告；索引层已有 reports 分区。
        "report",
    }
)


def handle_query(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    raw_kind = parameters.get("aggregate_kind")
    aggregate_kind: AggregateKind | None = None
    if raw_kind is not None:
        kind = _as_text(raw_kind, "aggregate_kind")
        if kind not in _AGGREGATE_KINDS:
            raise BUseCaseError("B_INVALID_PARAMETER", f"unknown aggregate_kind: {kind}")
        aggregate_kind = kind  # type: ignore[assignment]
    revision = parameters.get("revision")
    try:
        record_query = RecordQuery(
            project_id=project_id,
            aggregate_kind=aggregate_kind,
            record_id=_optional_text(parameters.get("record_id"), "record_id"),
            limit=_as_limit(parameters.get("limit")),
            cursor=_optional_text(parameters.get("cursor"), "cursor"),
            revision=None if revision is None else _revision_of(revision, "revision"),
            sort=_as_text(parameters.get("sort", "aggregate_kind"), "sort"),
            descending=_bool_of(parameters.get("descending"), "descending"),
            business_outcome=_optional_text(
                parameters.get("business_outcome"), "business_outcome"
            ),
            run_id=_optional_text(parameters.get("run_id"), "run_id"),
            report_id=_optional_text(parameters.get("report_id"), "report_id"),
            view=_optional_text(parameters.get("view"), "view"),
            facet=_as_text(parameters.get("facet", "NONE"), "facet"),
            facet_value=_optional_text(parameters.get("facet_value"), "facet_value"),
            severity=_optional_text(parameters.get("severity"), "severity"),
        )
    except ValueError as error:
        # 已登记错误码（如 `QUERY_UNSUPPORTED_FILTER`）原样抛出；其余是参数问题。
        if getattr(error, "code", None):
            raise
        raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error
    try:
        page = deps.reader.query(record_query)
    except ValueError as error:
        # 固定目录组合的合法性由 `QuerySpec.ensure_finite_route()` 唯一判定；
        # 它自带的 `QUERY_UNSUPPORTED_FILTER` 必须保留，其余落到参数问题，
        # 不得变成 INTERNAL_ERROR。
        if getattr(error, "code", None):
            raise
        raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error
    return {
        "items": [
            {
                "aggregate_kind": record.aggregate_kind,
                "record_id": record.record_id,
                "revision": record.revision,
            }
            for record in page.items
        ],
        "next_cursor": page.next_cursor,
        # 同一已提交边界的配对信息（架构 06 §4/§8/§136：「列表返回 commit 配对摘要」
        # 「读取 SnapshotEnvelope 及配对游标」）。取不到时如实给 None（未知），
        # 不改成"此刻最新"水位。
        "commit_id": getattr(page, "commit_id", None),
        "event_cursor": getattr(page, "event_cursor", None),
    }
