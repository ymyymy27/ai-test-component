"""把 D 的问题生命周期用例并进统一入口（P1-FR15）。

与 `b_registration.py` 同一做法：`application` 不得 import 入口层，因此这里做一次**显式
cast**，由 `tests/unit/test_saved_defect_management.py` 用真实 `Command` 驱动把形状锁死。

`request_regression`（**安排回归**）也已登记：其语义由架构 03 §66/§117/§149 与功能 §542
冻结——只登记回归关联（修复版本/回归方式/原关闭条件/原复现尝试与证据），**不创建 Attempt、
不执行、不授权、不关闭**；参数只接受 `issue_id` 与可选 `note`，目标与标准一律取自问题记录。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from aitest.application.review.repair_brief import SavedRepairBrief
from aitest.application.review.saved_defects import SavedDefectManagement
from aitest.application.review.saved_exports import SavedExportService
from aitest.application.review.saved_reports import SavedReportService
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import Handler

__all__ = [
    "brief_registration_for",
    "d_registration_for",
    "defect_registration_for",
    "export_registration_for",
    "report_registration_for",
    "register_d_use_cases",
]


def _entry(handler: Callable[[object], Mapping[str, object]]) -> Handler:
    def entry(command: Command) -> Mapping[str, object]:
        return handler(cast("object", command))

    return entry


def defect_registration_for(service: SavedDefectManagement) -> dict[str, Handler]:
    """构造可直接并入 `LocalAPI(handlers=...)` 的问题动作表（FR15）。"""

    return {
        "update_issue": _entry(service.update_issue),
        "record_fix": _entry(service.record_fix),
        "request_regression": _entry(service.request_regression),
        "close_issue": _entry(service.close_issue),
    }


def report_registration_for(service: SavedReportService) -> dict[str, Handler]:
    """构造可直接并入 `LocalAPI(handlers=...)` 的报告/复核动作表（FR14）。"""

    return {
        "create_report": _entry(service.create_report),
        "record_local_review": _entry(service.record_local_review),
    }


def export_registration_for(service: SavedExportService) -> dict[str, Handler]:
    """构造可直接并入 `LocalAPI(handlers=...)` 的导出动作表（FR14）。"""

    return {"export_report": _entry(service.export_report)}


def brief_registration_for(service: SavedRepairBrief) -> dict[str, Handler]:
    """只读的复测简报动作（一期动作表里的 READ_ACTIONS 成员）。"""

    return {"copy_repair_brief": _entry(service.copy_repair_brief)}


def d_registration_for(
    service: SavedDefectManagement,
    *,
    reports: SavedReportService | None = None,
    exports: SavedExportService | None = None,
    briefs: SavedRepairBrief | None = None,
) -> dict[str, Handler]:
    """构造可直接并入 `LocalAPI(handlers=...)` 的 D 问题/报告/导出/简报动作表。"""

    registration = defect_registration_for(service)
    for extra in (
        report_registration_for(reports) if reports is not None else {},
        export_registration_for(exports) if exports is not None else {},
        brief_registration_for(briefs) if briefs is not None else {},
    ):
        conflicts = sorted(set(registration) & set(extra))
        if conflicts:
            raise ValueError(f"D use case already registered: {conflicts}")
        registration.update(extra)
    return registration


def register_d_use_cases(
    api: object,
    service: SavedDefectManagement,
    *,
    reports: SavedReportService | None = None,
    exports: SavedExportService | None = None,
    briefs: SavedRepairBrief | None = None,
) -> tuple[str, ...]:
    """把 D 的动作并进一个已有 `LocalAPI` 的 handler 表；同名冲突即拒绝。"""

    handlers = getattr(api, "handlers", None)
    if not isinstance(handlers, dict):
        raise TypeError("api must expose a mutable 'handlers' dict")
    registration = d_registration_for(
        service, reports=reports, exports=exports, briefs=briefs
    )
    conflicts = sorted(set(registration) & set(handlers))
    if conflicts:
        raise ValueError(f"D use case already registered: {conflicts}")
    handlers.update(registration)
    return tuple(sorted(registration))
