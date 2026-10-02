"""`task` 与 `delivery` 两类记录的落盘回归（真实文件存储）与出口动作。

测什么：

1. **往返**：保存后按准确修订读回，对象与源对象相等（含验收项与自述块）；
2. **重启读回**：重新构造一整套底座对象、复用同一目录，仍读得到；
3. **不自动覆盖**：同修订再存一次抛 `ConcurrentEditError`；
4. **修订只追加**：`@1` 与 `@2` 同时读得到，历史不被改写；
5. **自述与验证事实分列**：读回的 `self_report` 不因自述非空而让 `verified_in_scope` 非空；
6. **缺字段读回抛错**：读不懂的记录不得被当成"没有记录"；
7. **验收项顺序保留**：`Task.acceptance_items` 的声明顺序是语义的一部分，不排序；
8. **出口动作**：`save_task` / `save_delivery` 经统一入口落盘，`expected_revision=0`
   按"新建"处理，冲突出独立错误码。

不使用 `tmp_path`：受限执行环境拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aitest.application.planning.substrate import ConcurrentEditError
from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.project.persistence import (
    load_delivery,
    load_task,
    save_delivery,
    save_task,
)
from aitest.application.project.serialization import (
    acceptance_item_to_payload,
    delivery_to_payload,
    task_to_payload,
)
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.contracts.commands import Command
from aitest.domain.project.context import (
    AcceptanceItem,
    Delivery,
    SelfReport,
    Task,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.b_registration import register_b_use_cases

PROJECT_ID = "project-task"
WORKSPACE_ID = "ws-task"
OTHER_PROJECT = "project-other"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class _Stack:
    unit_of_work: PortsUnitOfWork
    reader: PortsRecordReader


def _start(root: Path) -> _Stack:
    raw = FileUnitOfWork(root)
    repository = raw.repo
    return _Stack(
        unit_of_work=PortsUnitOfWork(raw, repository=repository),
        reader=PortsRecordReader(repository),
    )


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) / f"aitest-task-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _api(stack: _Stack) -> LocalAPI:
    api = LocalAPI(instance_id="core-task", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(
        api,
        BUseCaseDependencies(
            unit_of_work=stack.unit_of_work,
            reader=stack.reader,
            clock=_FixedClock(),
        ),
    )
    return api


def _session() -> Session:
    return Session(session_id="cli-task", entry_kind=EntryKind.HUMAN_UI)


def _command(action: str, request_id: str, parameters: Mapping[str, object]) -> Command:
    return Command(
        request_id=request_id,
        action=action,
        project_id=PROJECT_ID,
        expected_revision=0,
        intent_id=f"intent-{request_id}",
        parameters=dict(parameters),
    )


# ------------------------------------------------------------------ 夹具对象


def _items() -> tuple[AcceptanceItem, ...]:
    """验收项**按重要性声明**，顺序是语义的一部分。"""
    return (
        AcceptanceItem(
            acceptance_item_id="ai-1",
            observable_result="a created ticket is read back unchanged",
        ),
        AcceptanceItem(
            acceptance_item_id="ai-2",
            observable_result="a rejected ticket leaves no record",
            required=False,
        ),
    )


def _task(*, revision: int = 1, task_id: str = "task-1") -> Task:
    return Task(
        task_id=task_id,
        project_id=PROJECT_ID,
        goal="accept the ticket flow",
        scope="ticket creation and rejection",
        acceptance_items=_items(),
        inputs=("ticket title", "ticket body"),
        outputs=("ticket id",),
        preconditions=("the service is running",),
        owner="feix-a",
        acceptor="reviewer",
        revision=revision,
    )


def _delivery(*, revision: int = 1, delivery_id: str = "delivery-1") -> Delivery:
    return Delivery(
        delivery_id=delivery_id,
        task_id="task-1",
        version="0.4.0",
        run_method="uv run pytest",
        self_report=SelfReport(
            completed=("ai-1",),
            incomplete=("ai-2",),
        ),
        # 验证事实**不来自自述**：这里显式给空，读回也必须为空。
        verified_in_scope=(),
        unverified_scope=("ai-2",),
        changed_modules=("module-ticket",),
        api_changes=("POST /tickets",),
        test_data=("tests/fixtures/tickets.json",),
        dependencies=("httpx==0.27",),
        mock_declarations=("upstream billing is mocked",),
        known_issues=("rate limiting is not covered",),
        self_test_evidence=("raw pytest output",),
        submitted_by="feix-a",
        revision=revision,
    )


# ------------------------------------------------------------------ 往返与不变性


def test_task_survives_a_restart_on_real_storage(workspace_root: Path) -> None:
    first = _start(workspace_root)
    save_task(_task(), unit_of_work=first.unit_of_work)

    restarted = _start(workspace_root)
    assert (
        load_task(restarted.reader, project_id=PROJECT_ID, task_id="task-1", revision=1)
        == _task()
    )


def test_delivery_survives_a_restart_on_real_storage(workspace_root: Path) -> None:
    first = _start(workspace_root)
    save_delivery(_delivery(), project_id=PROJECT_ID, unit_of_work=first.unit_of_work)

    restarted = _start(workspace_root)
    loaded = load_delivery(
        restarted.reader, project_id=PROJECT_ID, delivery_id="delivery-1", revision=1
    )
    assert loaded == _delivery()
    # 自述非空**不得**让验证事实跟着非空（自述不是证据）。
    assert loaded.has_self_reported_completion is True
    assert loaded.verified_in_scope == ()


def test_task_and_delivery_revisions_are_append_only(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    save_task(_task(revision=1), unit_of_work=stack.unit_of_work)
    save_task(_task(revision=2), unit_of_work=stack.unit_of_work, expected_revision=1)
    save_delivery(_delivery(revision=1), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work)
    save_delivery(
        _delivery(revision=2),
        project_id=PROJECT_ID,
        unit_of_work=stack.unit_of_work,
        expected_revision=1,
    )

    restarted = _start(workspace_root)
    assert (
        load_task(restarted.reader, project_id=PROJECT_ID, task_id="task-1", revision=1)
        .revision
        == 1
    )
    assert (
        load_task(restarted.reader, project_id=PROJECT_ID, task_id="task-1", revision=2)
        .revision
        == 2
    )
    assert (
        load_delivery(
            restarted.reader,
            project_id=PROJECT_ID,
            delivery_id="delivery-1",
            revision=1,
        ).revision
        == 1
    )


def test_task_is_not_overwritten_by_accident(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    save_task(_task(), unit_of_work=stack.unit_of_work)
    with pytest.raises(ConcurrentEditError):
        save_task(_task(), unit_of_work=stack.unit_of_work)


def test_cross_project_read_is_rejected(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    save_task(_task(), unit_of_work=stack.unit_of_work)
    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="another project"):
        load_task(
            restarted.reader,
            project_id=OTHER_PROJECT,
            task_id="task-1",
            revision=1,
        )


def test_missing_revision_is_an_error(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    save_task(_task(), unit_of_work=stack.unit_of_work)
    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="unknown revision"):
        load_task(
            restarted.reader, project_id=PROJECT_ID, task_id="task-1", revision=9
        )


def test_acceptance_item_order_is_preserved() -> None:
    """验收项顺序是语义的一部分：落盘与读回都不得重排。"""
    from aitest.application.project.serialization import task_from_payload

    payload = task_to_payload(_task())
    ids = [item["acceptance_item_id"] for item in payload["acceptance_items"]]
    assert ids == ["ai-1", "ai-2"]
    assert task_from_payload(payload) == _task()


def test_unreadable_payload_is_rejected_not_defaulted() -> None:
    from aitest.application.project.serialization import (
        delivery_from_payload,
        task_from_payload,
    )

    task_payload = task_to_payload(_task())
    del task_payload["goal"]
    with pytest.raises(ValueError, match="goal"):
        task_from_payload(task_payload)

    delivery_payload = delivery_to_payload(_delivery())
    del delivery_payload["self_report"]
    with pytest.raises(ValueError, match="self_report"):
        delivery_from_payload(delivery_payload)


def test_delivery_payload_keeps_self_report_and_facts_separate() -> None:
    payload = delivery_to_payload(_delivery())
    assert payload["self_report"] == {"completed": ["ai-1"], "incomplete": ["ai-2"]}
    assert payload["verified_in_scope"] == []
    assert payload["unverified_scope"] == ["ai-2"]


def test_required_flag_round_trips() -> None:
    payload = acceptance_item_to_payload(_items()[1])
    assert payload["required"] is False


# ------------------------------------------------------------------ 出口动作


def test_save_task_and_delivery_reach_the_entry(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    api = _api(stack)

    for action, request_id, parameters in (
        ("save_task", "req-task-1", {"task": task_to_payload(_task())}),
        ("save_delivery", "req-delivery-1", {"delivery": delivery_to_payload(_delivery())}),
    ):
        response = api.dispatch(
            _command(action, request_id, parameters), _session()
        )
        assert response.error is None, (action, response.error)
        assert response.result is not None
        assert response.result["revision"] == 1

    restarted = _start(workspace_root)
    assert (
        load_task(restarted.reader, project_id=PROJECT_ID, task_id="task-1", revision=1)
        == _task()
    )
    assert (
        load_delivery(
            restarted.reader,
            project_id=PROJECT_ID,
            delivery_id="delivery-1",
            revision=1,
        )
        == _delivery()
    )


def test_saving_a_task_twice_reports_a_revision_conflict(
    workspace_root: Path,
) -> None:
    """同一个"新建"意图存两次：第二次是修订冲突，不是静默覆盖。"""
    api = _api(_start(workspace_root))
    first = api.dispatch(
        _command("save_task", "req-task-1", {"task": task_to_payload(_task())}),
        _session(),
    )
    assert first.error is None, first.error
    second = api.dispatch(
        _command("save_task", "req-task-2", {"task": task_to_payload(_task())}),
        _session(),
    )
    assert second.error is not None
    assert second.error.code == "B_REVISION_CONFLICT"


def test_task_action_appends_a_new_revision_when_asked(
    workspace_root: Path,
) -> None:
    """`expected_revision=1` 时写入修订 2：修订递增追加，历史仍在。"""
    api = _api(_start(workspace_root))
    api.dispatch(
        _command("save_task", "req-task-1", {"task": task_to_payload(_task())}),
        _session(),
    )
    command = Command(
        request_id="req-task-2",
        action="save_task",
        project_id=PROJECT_ID,
        expected_revision=1,
        intent_id="intent-req-task-2",
        parameters={"task": task_to_payload(_task(revision=2))},
    )
    response = api.dispatch(command, _session())
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["revision"] == 2

    restarted = _start(workspace_root)
    assert (
        load_task(restarted.reader, project_id=PROJECT_ID, task_id="task-1", revision=1)
        .revision
        == 1
    )


def test_invalid_task_payload_is_named(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    payload = task_to_payload(_task())
    del payload["scope"]
    response = api.dispatch(
        _command("save_task", "req-task-bad", {"task": payload}), _session()
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "scope" in response.error.message


def test_task_without_acceptance_items_is_rejected(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    payload = task_to_payload(_task())
    payload["acceptance_items"] = []
    response = api.dispatch(
        _command("save_task", "req-task-bad", {"task": payload}), _session()
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
