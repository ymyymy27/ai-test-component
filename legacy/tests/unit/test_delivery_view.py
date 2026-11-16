"""交付说明视图（BD-001 §7.2、功能 §4 P1-FR02）：自述与验证分列、核验状态取冻结三态。

分册原文：

- BD-001 §7.2：「`Delivery` 已拆出 `self_report`（`completed`/`incomplete`）与
  `verified_in_scope`/`unverified_scope`」；「开发自述完成与测试验证完成**分列两处**，不得合并为
  一个"完成"字段」；「`verified_in_scope` 为空而 `self_report.completed` 非空时，必须显示为
  **待验证**，不得显示为已完成」；「AI 草稿不作为完成证据」；
- 功能 §4/§401：「开发自述完成、实际测试完成和未验证范围**分别展示**」；
- 功能 §14：`verification_state` ∈ {必要核验未完成, 材料无效, 核验有效}；
- AB-001 §1.27 / CORE-001 §1.40：「所有验收项**初始仍未验证**，不从自述推导结论」，
  「新正式提交拒绝历史自填 `verified_in_scope`」。

本文件锁定：①视图把自述块与验证块**分列**；②自述称完成而无验证范围 ⇒
`required_verification_incomplete`（待验证），绝不显示为已完成；③只读**准确修订**、跨项目拒绝、
缺记录拒绝；④附带的提测块只带验收项集合与内容身份，不猜"哪个提测"。
"""

from __future__ import annotations

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.domain.project.context import Delivery, SelfReport
from aitest.interfaces.dto import DeliveryViewDTO

PROJECT = "project-1"


def _write(core, kind: str, identity: str, payload: dict, *, project: str = PROJECT) -> int:
    unit = core.unit_of_work
    current = unit.repo.current_revision(kind, identity)
    expected = 0 if not isinstance(current, int) else current
    request = f"fixture-{kind}-{identity}-{expected}"
    unit.begin(request, project, intent_id=f"intent-{request}")
    unit.stage_record(
        aggregate_kind=kind, record_id=identity, expected_revision=expected, payload=payload
    )
    unit.commit(request)
    return expected + 1


def _delivery_payload(**changes: object) -> dict[str, object]:
    from aitest.application.project.serialization import delivery_to_payload

    delivery = Delivery(
        delivery_id="delivery-1",
        task_id="task-1",
        version="0.4.0",
        run_method="python main.py",
        self_report=SelfReport(completed=("developer says ticket is complete",)),
    )
    payload = delivery_to_payload(delivery, project_id=PROJECT) | {"task_revision": 1}
    payload.update(changes)
    return payload


def _core(tmp_path):
    return assemble_workspace_core(tmp_path, instance_id="delivery-view-core")


def test_self_reported_completion_without_verification_renders_as_pending(tmp_path) -> None:
    core = _core(tmp_path)
    try:
        _write(core, "delivery", "delivery-1", _delivery_payload())
        view = core.view_queries.delivery_view(project_id=PROJECT, delivery_id="delivery-1")
        assert isinstance(view, DeliveryViewDTO)
        # 自述与验证分列：自述内容不进入验证块，也不改变核验状态。
        assert view.self_report_completed == ("developer says ticket is complete",)
        assert view.self_report_incomplete == ()
        assert view.verified_in_scope == ()
        assert view.verification_state == "required_verification_incomplete"
        assert view.submitted is False and view.submission is None
        assert (view.delivery_id, view.revision, view.version) == ("delivery-1", 1, "0.4.0")
    finally:
        core.lifetime_lock.release()


def test_verified_scope_from_saved_record_is_rendered_as_valid(tmp_path) -> None:
    """记录里已有验证范围（由投影写入方落盘）⇒ 核验有效；视图只如实呈现。"""

    core = _core(tmp_path)
    try:
        payload = _delivery_payload()
        payload["verified_in_scope"] = ["ticket-created"]
        payload["unverified_scope"] = ["ticket-rejected"]
        _write(core, "delivery", "delivery-1", payload)
        view = core.view_queries.delivery_view(project_id=PROJECT, delivery_id="delivery-1")
        assert view.verified_in_scope == ("ticket-created",)
        assert view.unverified_scope == ("ticket-rejected",)
        assert view.verification_state == "verification_valid"
    finally:
        core.lifetime_lock.release()


def test_submission_block_carries_only_its_own_frozen_fields() -> None:
    """提测块只投影自己的冻结字段（验收项集合与内容身份）。

    记录级读取要过来源材料闭包（快照/绑定/项目），真实底座由交付提测套件覆盖；
    这里核对 DTO 投影本身，避免用合成记录伪造闭包。
    """

    from aitest.interfaces.dto import delivery_view_dto

    view = delivery_view_dto(
        Delivery(
            delivery_id="delivery-1",
            task_id="task-1",
            version="0.4.0",
            run_method="python main.py",
            self_report=SelfReport(completed=("developer says ticket is complete",)),
        ),
        project_id=PROJECT,
        submission={
            "submission_id": "submission-1",
            "revision": 1,
            "version": "0.4.0",
            "content_identity": "sha256:" + "b" * 64,
            "acceptance_item_ids": ["ticket-created", "ticket-rejected"],
        },
    )
    assert view.submitted is True
    assert view.submission is not None
    assert view.submission.acceptance_item_ids == ("ticket-created", "ticket-rejected")
    assert view.submission.content_identity == "sha256:" + "b" * 64
    # 提测记录本身不带验证结论：核验状态仍由交付说明派生（初始未验证）。
    assert view.verification_state == "required_verification_incomplete"
    assert view.self_report_completed == ("developer says ticket is complete",)


def test_submission_block_requires_its_exact_saved_fields() -> None:
    from aitest.interfaces.dto import delivery_view_dto

    delivery = Delivery(
        delivery_id="delivery-1", task_id="task-1", version="0.4.0", run_method="python main.py"
    )
    with pytest.raises(ValueError, match="submission_id"):
        delivery_view_dto(delivery, project_id=PROJECT, submission={"revision": 1})
    with pytest.raises(ValueError, match="acceptance_item_ids"):
        delivery_view_dto(
            delivery,
            project_id=PROJECT,
            submission={
                "submission_id": "submission-1",
                "revision": 1,
                "version": "0.4.0",
                "content_identity": "sha256:" + "b" * 64,
                "acceptance_item_ids": "ticket-created",
            },
        )


def test_delivery_view_refuses_missing_records_foreign_projects_and_bad_inputs(tmp_path) -> None:
    core = _core(tmp_path)
    try:
        _write(core, "delivery", "delivery-1", _delivery_payload(), project=PROJECT)
        reader = core.view_queries
        with pytest.raises(ValueError, match="unavailable"):
            reader.delivery_view(project_id=PROJECT, delivery_id="delivery-missing")
        with pytest.raises(ValueError, match="another project"):
            reader.delivery_view(project_id="other-project", delivery_id="delivery-1")
        with pytest.raises(ValueError, match="positive record revision"):
            reader.delivery_view(project_id=PROJECT, delivery_id="delivery-1", revision=0)
        with pytest.raises(ValueError, match="submission identity"):
            reader.delivery_view(
                project_id=PROJECT, delivery_id="delivery-1", submission_id="  "
            )
        with pytest.raises(ValueError, match="submission is unavailable"):
            reader.delivery_view(
                project_id=PROJECT, delivery_id="delivery-1", submission_id="submission-missing"
            )
        # 准确修订读取：请求不存在的修订时拒绝（不做"读最新"）。
        with pytest.raises((ValueError, KeyError, OSError)):
            reader.delivery_view(project_id=PROJECT, delivery_id="delivery-1", revision=9)
    finally:
        core.lifetime_lock.release()


def test_delivery_view_requires_saved_record_access() -> None:
    from aitest.interfaces.local.view_queries import CommittedViewReader

    reader = CommittedViewReader(decisions=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="saved record access"):
        reader.delivery_view(project_id=PROJECT, delivery_id="delivery-1")
