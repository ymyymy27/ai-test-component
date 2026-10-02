"""`case` / `acceptance_scope` / `case_link` 三类记录的落盘回归（真实文件存储）。

测什么：

1. **往返**：保存后用**准确修订**读回，对象与源对象相等；
2. **重启读回**：重新构造一整套底座对象、复用同一目录，仍读得到；
3. **不自动覆盖**：同修订再存一次抛 `ConcurrentEditError`（带当前修订）；
4. **只追加**：同一 `Case` 的修订 2 与修订 1 同时读得到，历史不被改写；
5. **确认只追加**：`save_confirmation()` 没有 `expected_revision`；
   同一 `confirmation_id` 再登记一次会冲突，而不是静默覆盖；
6. **缺字段读回抛错**：读不懂的记录不得被当成"没有记录"（否则同键异输入会覆盖材料）；
7. **集合的书写顺序不影响字节**：同一集合的不同顺序得到同一形状。

不使用 `tmp_path`：受限执行环境拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举
（与 `tests/unit/test_substrate_adapter.py` 同一原因）。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from aitest.application.planning.persistence import (
    load_acceptance_scope,
    load_case,
    load_confirmation,
    save_acceptance_scope,
    save_case,
    save_confirmation,
)
from aitest.application.planning.serialization import (
    acceptance_scope_to_payload,
    case_to_payload,
    confirmation_to_payload,
)
from aitest.application.planning.substrate import ConcurrentEditError
from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.domain.planning.plans import (
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseImportance,
    CaseLayer,
    CaseLink,
    ConfirmationRecord,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

PROJECT_ID = "project-planning"
OTHER_PROJECT = "project-other"


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
    root = Path(tempfile.gettempdir()) / f"aitest-planning-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------ 夹具对象


def _links(*, modules: tuple[str, ...] = ("module-b", "module-a")) -> CaseLink:
    return CaseLink(
        acceptance_item_ids=frozenset({"ai-2", "ai-1"}),
        module_ids=frozenset(modules),
        environment_ids=frozenset({"env-1"}),
        critical_path_ids=frozenset({"path-1"}),
    )


def _basis(
    *, state: AssertionBasisState = AssertionBasisState.CONFIRMED
) -> AssertionBasis:
    if state is AssertionBasisState.MISSING:
        return AssertionBasis(revision=1, state=state)
    return AssertionBasis(
        revision=1,
        state=state,
        text="check the persisted body, not only the status code",
        text_digest="sha256:basis-1",
    )


def _case(*, revision: int = 1, case_id: str = "case-1") -> Case:
    return Case(
        case_id=case_id,
        revision=revision,
        layer=CaseLayer.L2,
        objective="create a ticket through the registered HTTP handler",
        preconditions=("the service is running",),
        inputs=("ticket title", "ticket body"),
        steps=("POST /tickets", "GET /tickets/{id}"),
        expected="the created ticket is read back unchanged",
        verification_method="read-only query against the same ticket id",
        links=_links(),
        assertion_basis=_basis(),
        independent_verification="read-only query against the same ticket id",
        mock_scope=("upstream billing",),
        importance=CaseImportance.P0,
    )


def _scope():  # noqa: ANN202 - 测试内部构造
    from aitest.domain.planning.plans import AcceptanceScope

    return AcceptanceScope(
        scope_id="scope-1",
        revision=1,
        name="ticket acceptance",
        required_case_ids=frozenset({"case-2", "case-1"}),
        template_case_ids=frozenset({"case-1"}),
        objective="accept the ticket flow",
        excluded_case_ids=frozenset({"case-9"}),
        exclusion_reasons=(("case-9", "not part of this release"),),
        dependency_closure_ids=frozenset({"case-3"}),
        applicability_exclusions=(("case-4", "no billing module"),),
    )


def _confirmation(*, confirmation_id: str = "confirm-1") -> ConfirmationRecord:
    return ConfirmationRecord(
        confirmation_id=confirmation_id,
        case_id="case-1",
        basis_revision=1,
        basis_text_digest="sha256:basis-1",
        confirmed_at_commit="commit-7",
    )


# ------------------------------------------------------------------ 用例


def test_case_survives_a_restart_on_real_storage(workspace_root: Path) -> None:
    first = _start(workspace_root)
    save_case(_case(), project_id=PROJECT_ID, unit_of_work=first.unit_of_work)

    restarted = _start(workspace_root)
    assert (
        load_case(
            restarted.reader, project_id=PROJECT_ID, case_id="case-1", revision=1
        )
        == _case()
    )


def test_case_revisions_are_append_only(workspace_root: Path) -> None:
    """修订 2 与修订 1 同时读得到：历史用例不被改写。"""
    stack = _start(workspace_root)
    save_case(_case(revision=1), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work)
    save_case(
        _case(revision=2),
        project_id=PROJECT_ID,
        unit_of_work=stack.unit_of_work,
        expected_revision=1,
    )
    restarted = _start(workspace_root)
    assert (
        load_case(
            restarted.reader, project_id=PROJECT_ID, case_id="case-1", revision=1
        ).revision
        == 1
    )
    assert (
        load_case(
            restarted.reader, project_id=PROJECT_ID, case_id="case-1", revision=2
        ).revision
        == 2
    )


def test_case_is_not_overwritten_by_accident(workspace_root: Path) -> None:
    """同一个"新建"意图存两次：第二次是修订冲突，不是静默覆盖。"""
    stack = _start(workspace_root)
    save_case(_case(), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work)
    with pytest.raises(ConcurrentEditError):
        save_case(_case(), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work)


def test_scope_and_confirmation_survive_a_restart(workspace_root: Path) -> None:
    first = _start(workspace_root)
    save_acceptance_scope(
        _scope(), project_id=PROJECT_ID, unit_of_work=first.unit_of_work
    )
    save_confirmation(
        _confirmation(), project_id=PROJECT_ID, unit_of_work=first.unit_of_work
    )

    restarted = _start(workspace_root)
    assert (
        load_acceptance_scope(
            restarted.reader, project_id=PROJECT_ID, scope_id="scope-1", revision=1
        )
        == _scope()
    )
    assert (
        load_confirmation(
            restarted.reader, project_id=PROJECT_ID, confirmation_id="confirm-1"
        )
        == _confirmation()
    )


def test_confirmation_is_append_only(workspace_root: Path) -> None:
    """同一 `confirmation_id` 再登记一次是冲突——确认记录不得被覆盖。"""
    stack = _start(workspace_root)
    save_confirmation(
        _confirmation(), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work
    )
    with pytest.raises(ConcurrentEditError):
        save_confirmation(
            _confirmation(), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work
        )
    # 换一个 id 登记新确认是允许的：更正走"追加新确认"。
    save_confirmation(
        _confirmation(confirmation_id="confirm-2"),
        project_id=PROJECT_ID,
        unit_of_work=stack.unit_of_work,
    )
    restarted = _start(workspace_root)
    assert (
        load_confirmation(
            restarted.reader, project_id=PROJECT_ID, confirmation_id="confirm-2"
        ).confirmation_id
        == "confirm-2"
    )


def test_reading_another_projects_record_is_rejected(workspace_root: Path) -> None:
    """跨项目读同一条记录必须被拒，不能当成同一条读到。"""
    stack = _start(workspace_root)
    save_case(_case(), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work)
    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="another project"):
        load_case(
            restarted.reader,
            project_id=OTHER_PROJECT,
            case_id="case-1",
            revision=1,
        )


def test_missing_revision_is_an_error_not_an_empty_record(
    workspace_root: Path,
) -> None:
    """读不存在的修订要报错，不得返回空对象或"没有记录"。"""
    stack = _start(workspace_root)
    save_case(_case(), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work)
    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="unknown revision"):
        load_case(
            restarted.reader, project_id=PROJECT_ID, case_id="case-1", revision=9
        )


def test_unreadable_payload_is_rejected_not_defaulted() -> None:
    """缺字段的 payload 读回必须抛错，不填默认值。"""
    from aitest.application.planning.serialization import case_from_payload

    payload = case_to_payload(_case(), project_id=PROJECT_ID)
    del payload["objective"]
    with pytest.raises(ValueError, match="objective"):
        case_from_payload(payload)


def test_collection_order_does_not_change_the_payload() -> None:
    """同一集合的不同书写顺序得到同一形状（否则字节不可复现）。"""
    reordered = Case(
        case_id="case-1",
        revision=1,
        layer=CaseLayer.L2,
        objective="create a ticket through the registered HTTP handler",
        preconditions=("the service is running",),
        inputs=("ticket title", "ticket body"),
        steps=("POST /tickets", "GET /tickets/{id}"),
        expected="the created ticket is read back unchanged",
        verification_method="read-only query against the same ticket id",
        links=_links(modules=("module-a", "module-b")),
        assertion_basis=_basis(),
        independent_verification="read-only query against the same ticket id",
        mock_scope=("upstream billing",),
        importance=CaseImportance.P0,
    )
    assert case_to_payload(reordered, project_id=PROJECT_ID) == case_to_payload(
        _case(), project_id=PROJECT_ID
    )


def test_missing_assertion_basis_round_trips_without_text() -> None:
    """缺失态依据：读回不得凭空长出正文或摘要。"""
    from aitest.application.planning.serialization import (
        assertion_basis_from_payload,
        assertion_basis_to_payload,
    )

    missing = _basis(state=AssertionBasisState.MISSING)
    payload = assertion_basis_to_payload(missing)
    assert payload["text"] == ""
    assert assertion_basis_from_payload(payload) == missing


def test_payload_carries_the_project_scope() -> None:
    """三类形状都带项目范围：记录需要项目维度才可查询。"""
    assert case_to_payload(_case(), project_id=PROJECT_ID)["project_id"] == PROJECT_ID
    assert (
        acceptance_scope_to_payload(_scope(), project_id=PROJECT_ID)["project_id"]
        == PROJECT_ID
    )
    assert (
        confirmation_to_payload(_confirmation(), project_id=PROJECT_ID)["project_id"]
        == PROJECT_ID
    )
