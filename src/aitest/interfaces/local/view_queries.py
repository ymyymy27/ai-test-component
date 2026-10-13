"""宿主只读视图查询（架构 06 §1/§2/§4/§5、§54 `editor_host` 的"只读查询与事件续读"）。

面板读的是 **DTO 视图**（RunDTO/DecisionDTO/…），不是原始记录载荷：架构 06 §1 把 DTO 的生产方
写成"应用查询与 interfaces/dto.py"，§9 又要求"视图不直接读文件、调用适配器或重算判定"。
本模块就是那层只读查询接缝：

- 判定**只算一次**：调用 D 侧 `SavedReportService.live_decision`（报告与面板共用同一次领域判定），
  再经 `interfaces/dto.py` 的纯投影函数生成 DTO；
- **不写任何记录**：只读已提交事实与冻结准备，协商式 `read_view` 暴露有限选择器；
- 外层返回读取期间未变化的全视图提交/事件边界，供宿主续读；DTO 内的执行事实快照
  标识保留原义，不把它当成整个视图的事件游标。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aitest.application.ports import RecordQuery
from aitest.application.project.serialization import delivery_from_payload
from aitest.application.review.saved_reports import SavedReportService
from aitest.contracts.commands import Command
from aitest.contracts.read_views import ReadViewQuery
from aitest.interfaces.dto import (
    DeliveryViewDTO,
    PanelViewDTO,
    RunViewDTO,
    delivery_view_dto,
    panel_view_dto,
    run_view_dto,
)

__all__ = ["CommittedViewReader"]


class CommittedViewReader:
    """从**已提交记录**读视图 DTO；判定复用 D 侧唯一装配点。"""

    def __init__(
        self,
        *,
        decisions: SavedReportService,
        reader: Any | None = None,
        records: Any | None = None,
    ) -> None:
        if decisions is None:
            raise ValueError("the view reader requires the live decision capability")
        self._decisions = decisions
        self._reader = reader
        self._records = records

    def dispatch(self, command: Command) -> Mapping[str, object]:
        """Finite DTO query with a single verified outer view/event boundary."""
        project = command.project_id
        if not isinstance(project, str) or not project.strip() or len(project) > 128:
            raise ValueError("read_view requires an explicit project")
        selector = ReadViewQuery.model_validate(command.parameters)
        view = selector.view
        identity = selector.run_id if view in {"run", "panel"} else selector.delivery_id
        assert identity is not None
        boundary = self._boundary(project)
        result: RunViewDTO | PanelViewDTO | DeliveryViewDTO
        if view == "run":
            result = self.run_view(project_id=project, run_id=identity)
        elif view == "panel":
            result = self.panel_view(project_id=project, run_id=identity)
        else:
            result = self.delivery_view(
                project_id=project,
                delivery_id=identity,
                revision=selector.revision,
                submission_id=selector.submission_id,
            )
        if self._boundary(project) != boundary:
            raise ValueError("the committed view boundary changed while reading")
        return {"view_type": view, "view": result.model_dump(mode="json"), **boundary}

    def _boundary(self, project_id: str) -> dict[str, object]:
        query = getattr(self._reader, "query", None)
        if not callable(query):
            raise ValueError("committed view/event pairing is unavailable")
        page = query(RecordQuery(project_id=project_id, limit=1))
        values = {
            name: getattr(page, name, None)
            for name in ("commit_id", "event_cursor", "generation", "index_version")
        }
        for name in ("commit_id", "generation", "index_version"):
            value = values[name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("the exact committed view/event boundary is unverified")
        cursor = values["event_cursor"]
        if not isinstance(cursor, str) or not cursor.strip():
            raise ValueError("the exact committed view/event boundary is unverified")
        return values

    def run_view(self, *, project_id: str, run_id: str) -> RunViewDTO:
        """运行视图：事实 + 同一次领域判定 → `RunViewDTO`（含快照边界与配对游标）。"""

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("the run view requires an explicit project identity")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("the run view requires an explicit run identity")
        facts, decision = self._decisions.live_decision(project_id, run_id)
        return run_view_dto(facts, decision)

    def panel_view(self, *, project_id: str, run_id: str) -> PanelViewDTO:
        """面板主屏视图（架构 06 §1 的「当前段 + 信息区」）：同一次判定 + 运行视图。

        只读已提交事实与**同一次**领域判定；`primary_action` 的动作标识未冻结，故不给候选
        （见待裁定第 8 项）。
        """

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("the panel view requires an explicit project identity")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("the panel view requires an explicit run identity")
        facts, decision = self._decisions.live_decision(project_id, run_id)
        return panel_view_dto(facts, decision, run_view_dto(facts, decision))

    def delivery_view(
        self,
        *,
        project_id: str,
        delivery_id: str,
        revision: int | None = None,
        submission_id: str | None = None,
    ) -> DeliveryViewDTO:
        """交付说明视图（BD-001 §7.2）：自述与验证**分列**，核验状态取冻结三态。

        - 只读**准确修订**：给出 `revision` 时按该修订读，否则读当前修订指针（不读"最新正文"）；
        - 记录正文里的 `project_id` 必须与请求项目一致（跨项目一律拒绝）；
        - 传入 `submission_id` 时附带该正式提测记录的验收项集合与内容身份；**不猜**"哪个提测"。
        """

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("the delivery view requires an explicit project identity")
        if not isinstance(delivery_id, str) or not delivery_id.strip():
            raise ValueError("the delivery view requires an explicit delivery identity")
        if self._reader is None or self._records is None:
            raise ValueError("the delivery view requires saved record access")
        exact = revision
        if exact is None:
            current = self._records.current_revision("delivery", delivery_id)
            if type(current) is not int or current < 1:
                raise ValueError("saved delivery is unavailable")
            exact = current
        if type(exact) is not int or exact < 1:
            raise ValueError("the delivery view requires a positive record revision")
        record = self._reader.read(aggregate_kind="delivery", record_id=delivery_id, revision=exact)
        payload = record.payload
        delivery = delivery_from_payload(payload)
        if payload.get("project_id") != project_id:
            raise ValueError("saved delivery belongs to another project")
        if (
            delivery.delivery_id != delivery_id
            or record.record_id != delivery_id
            or record.revision != exact
            or record.aggregate_kind != "delivery"
        ):
            raise ValueError("saved delivery does not match its exact record identity")
        submission: dict[str, object] | None = None
        if submission_id is not None:
            if not isinstance(submission_id, str) or not submission_id.strip():
                raise ValueError("the delivery view requires an explicit submission identity")
            submission_revision = self._records.current_revision(
                "delivery_submission", submission_id
            )
            if type(submission_revision) is not int or submission_revision < 1:
                raise ValueError("saved delivery submission is unavailable")
            submission_record = self._reader.read(
                aggregate_kind="delivery_submission",
                record_id=submission_id,
                revision=submission_revision,
            )
            submission = dict(submission_record.payload)
            if submission.get("project_id") != project_id:
                raise ValueError("saved delivery submission belongs to another project")
            if (
                submission.get("submission_id") != submission_id
                or submission.get("delivery_id") != delivery_id
                or submission.get("delivery_record_revision") != exact
            ):
                raise ValueError("saved submission does not bind the exact delivery revision")
        return delivery_view_dto(delivery, project_id=project_id, submission=submission)
