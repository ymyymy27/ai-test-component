"""公共结果投影与缺口解析

原先在 `dispatch/actions/handlers.py`；按动作族拆出，registry 直接绑定。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from aitest.application.dispatch.errors import BUseCaseError as BUseCaseError
from aitest.application.dispatch.params import (
    _as_mapping,
    _as_text,
    _bool_of,
    _required,
    _revision_of,
)
from aitest.application.planning.publish import (
    PublicationResult,
)
from aitest.application.project.context import ContextGap

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
    pass
def _publication_result(result: PublicationResult, *, published_kind: str) -> Mapping[str, object]:
    """发布结果：**要么得到已发布版本，要么得到阻塞原因**（`PublicationResult` 的不变量）。"""
    if result.value is None:
        return {"published": False, "blocked_by": list(result.blocked_by)}
    value = result.value
    header: dict[str, object] = {
        "published": True,
        "kind": published_kind,
        "revision": _revision_of(getattr(value, "revision", None), "revision"),
        "record_revision": _revision_of(value.record_revision, "record_revision"),
        "confirmation_id": _as_text(getattr(value, "confirmation_id", None), "confirmation_id"),
    }
    if published_kind == "plan":
        scope = getattr(value, "scope", None)
        header["plan_id"] = _as_text(getattr(value, "plan_id", None), "plan_id")
        header["scope_id"] = _as_text(getattr(scope, "scope_id", None), "scope_id")
        header["scope_revision"] = _revision_of(getattr(scope, "revision", None), "scope_revision")
        header["case_revisions"] = [
            {"case_id": ref.case_id, "revision": ref.revision, "digest": ref.digest}
            for ref in getattr(value, "case_revisions", ())
        ]
        header["rule_revisions"] = [
            {"rule_id": ref.rule_id, "revision": ref.revision, "digest": ref.digest}
            for ref in getattr(value, "rule_revisions", ())
        ]
        header["template_versions"] = [
            {
                "template_id": ref.template_id,
                "version": ref.version,
                "digest": ref.digest,
            }
            for ref in getattr(value, "template_versions", ())
        ]
        return header
    header["rule_id"] = _as_text(getattr(value, "rule_id", None), "rule_id")
    header["digest"] = _as_text(getattr(value, "digest", None), "digest")
    return header

def _gaps_of(value: object, name: str) -> tuple[ContextGap, ...]:
    """上下文缺口列表；每一项的字段名与 `ContextGap` 逐字一致。"""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a list")
    gaps: list[ContextGap] = []
    for index, item in enumerate(value):
        raw = _as_mapping(item, f"{name}[{index}]")
        prefix = f"{name}[{index}]"
        gaps.append(
            ContextGap(
                kind=_as_text(_required(raw, "kind"), f"{prefix}.kind"),
                subject=_as_text(_required(raw, "subject"), f"{prefix}.subject"),
                detail=_as_text(_required(raw, "detail"), f"{prefix}.detail"),
                blocking=_bool_of(raw.get("blocking"), f"{prefix}.blocking", default=True),
            )
        )
    return tuple(gaps)
