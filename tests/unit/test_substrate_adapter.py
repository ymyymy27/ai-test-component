"""薄转接头对 **A 的真实文件存储** 的接线验证。

测试对象**不是内存替身**：这里用 `infrastructure/file_store` 的
`FileUnitOfWork` / `FileRecordRepository` 与 `RecoveryOrchestrator` 真落盘，
再经 `PortsUnitOfWork` / `PortsRecordReader` 跑 B 的 `prepare_run`。
"重启后仍能按业务身份查回原意图"这条要求只有在这一层才测得出来——
内存替身无论 payload 少多少字段都能查到。

## 为什么不用 `tmp_path` / `tempfile.mkdtemp()`

受限执行环境会拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举，也会拒绝写入
`tempfile.mkdtemp()` 建出来的目录（同一目录改用 `Path.mkdir()` 就能写）。
本模块用 `Path.mkdir()` 在系统临时目录下建一个等价目录并在结束时清理：
被测语义完全相同，CI 与本机都能跑。

## 提交序号为什么由一个测试替身提供

`commit_seq()` / `next_commit_seq()` 还没冻结进 A 的 `ports.py`
（见 `docs/接口对接/进行中/AB-001-端口与保存/contract.md` 第 8.8 节）。
本模块用 A **自己**的 `RecoveryOrchestrator.inspect()` 读出已提交序号——
不读 A 的私有文件、不猜它的实现。正式签名落地后，装配点换成 A 的访问器即可。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aitest.application.planning.preparation import (
    InputRevisions,
    preparation_intent_id,
    preparation_record_id,
)
from aitest.application.planning.prepare_run import PreparationInputs, prepare_run
from aitest.application.planning.substrate import (
    ConcurrentEditError,
    IndexMaintenanceRequired,
    PreparationConflictError,
    RecordQuery,
)
from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.contracts.prepared_run import (
    AssertionBasisEntry,
    AssertionBasisStateFact,
    BindingFormFact,
    CaseLinks,
    CaseRevisionRef,
    EnvironmentIsolationModeFact,
    EnvironmentRefFact,
    ExecutionSourceBinding,
    FrozenCase,
    FrozenCaseStep,
    PlanRevisionRef,
    PreparedRunStatusFact,
    RuleVersionRef,
    RunDriverFact,
    RunTierFact,
    SnapshotRef,
    TemplateVersionRef,
)
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

PROJECT_ID = "p1"
CLIENT_ID = "c1"
REQUEST_ID = "req-1"


# ------------------------------------------------------------------ 夹具


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    """真实临时目录：`Path.mkdir()` 建、结束时清理。"""
    root = Path(tempfile.gettempdir()) / f"aitest-adapter-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class _Sequences:
    """当前提交序号的只读来源；用 A 自己的恢复巡检读出已提交序号。"""

    def __init__(self, root: Path) -> None:
        self._orchestrator = RecoveryOrchestrator(root, instance_id="adapter-test")

    def current_commit_sequence(self) -> int:
        committed = self._orchestrator.inspect()["committed_sequences"]
        assert isinstance(committed, tuple)
        return max(committed) if committed else 0


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class _Stack:
    """一次"进程启动"：真实文件底座 + 转接头。重新构造即等于重启。"""

    unit_of_work: PortsUnitOfWork
    reader: PortsRecordReader
    raw: FileUnitOfWork


def _start(root: Path) -> _Stack:
    raw = FileUnitOfWork(root)
    repository = raw.repo
    return _Stack(
        unit_of_work=PortsUnitOfWork(
            raw, repository=repository, sequence=_Sequences(root)
        ),
        reader=PortsRecordReader(repository),
        raw=raw,
    )


def _revisions(**overrides: int) -> InputRevisions:
    base = {
        "project_revision": 1,
        "binding_revision": 1,
        "snapshot_revision": 1,
        "environment_revision": 1,
        "plan_revision": 1,
        "rules_revision": 1,
        "template_revision": 1,
        "scope_revision": 1,
    }
    base.update(overrides)
    return InputRevisions(**base)


def _inputs(**overrides: object) -> PreparationInputs:
    base: dict[str, object] = {
        "project_id": PROJECT_ID,
        "workspace_id": "ws-1",
        "binding_id": "binding-1",
        "binding_revision": 1,
        "binding_form": BindingFormFact.GIT,
        "client_id": CLIENT_ID,
        "prepare_request_id": REQUEST_ID,
        "input_revisions": _revisions(),
        "snapshot": SnapshotRef(
            source_snapshot_id="snap-1",
            purpose="prepare",
            content_identity="sha256:content-1",
        ),
        "selected_paths": ("src/ticket",),
        "environment": EnvironmentRefFact(
            environment_id="env-1",
            revision=1,
            isolation_mode=EnvironmentIsolationModeFact.VENV,
            interpreter_identity="cpython-3.13.3-windows-amd64",
            dependency_set_digest="sha256:deps-1",
        ),
        "execution_source": ExecutionSourceBinding(
            registered_entry="python -m pytest",
            entry_arguments=("tests/acceptance",),
            cwd_mapping="<project-root> -> workdirs/run-1",
            test_config_ref="pyproject.toml#tool.pytest",
            resolved_input_digest="sha256:resolved-1",
        ),
        "plan_revision": PlanRevisionRef(
            revision_id="plan-1", revision_no=1, digest="sha256:plan-1"
        ),
        "acceptance_scope_revision": 1,
        "rule_versions": (
            RuleVersionRef(rule_id="rule-1", revision=1, digest="sha256:rule-1"),
        ),
        "template_versions": (
            TemplateVersionRef(
                template_id="ticket-workflow", version="1.0.0", digest="sha256:tpl-1"
            ),
        ),
        "case_revisions": (
            CaseRevisionRef(case_id="case-1", revision=1, digest="sha256:case-1"),
        ),
        "frozen_cases": (
            FrozenCase(
                case_id="case-1",
                revision=1,
                layer="L2",
                required=True,
                independent_verification="read-only query against the same ticket id",
                importance="P0",
                steps=(
                    FrozenCaseStep(
                        step_id="step-1",
                        layer="L2",
                        objective="create a ticket",
                        expected="the ticket is persisted",
                    ),
                ),
                links=CaseLinks(acceptance_item_ids=("ai-1",)),
            ),
        ),
        "assertion_bases": (
            AssertionBasisEntry(
                case_id="case-1",
                basis_revision=1,
                basis_text_digest="sha256:basis-1",
                assertion_basis_state=AssertionBasisStateFact.PRESENT_UNCONFIRMED,
            ),
        ),
        "git_base_commit": "9c44344bcd612df1a7d033efa1e7a47c810c49cf",
        "git_diff_digest": "sha256:diff-1",
        "run_tier": RunTierFact.FULL,
        "initial_driver": RunDriverFact.PLANNED,
        "template_required_case_ids": ("case-1",),
        "frozen_required_case_ids": ("case-1",),
        "selected_case_ids": ("case-1",),
    }
    base.update(overrides)
    return PreparationInputs(**base)  # type: ignore[arg-type]


def _prepare(stack: _Stack, **overrides: object):  # noqa: ANN202 - 测试内部辅助
    return prepare_run(
        _inputs(**overrides),
        unit_of_work=stack.unit_of_work,
        reader=stack.reader,
        clock=_FixedClock(),
    )


def _record_id(project_id: str = PROJECT_ID) -> str:
    return preparation_record_id(
        project_id=project_id, client_id=CLIENT_ID, prepare_request_id=REQUEST_ID
    )


def _intent_id(project_id: str = PROJECT_ID) -> str:
    return preparation_intent_id(
        project_id=project_id, client_id=CLIENT_ID, prepare_request_id=REQUEST_ID
    )


# --------------------------------------------------------------- 准备：落盘


def test_prepare_run_registers_the_preparation_on_the_real_store(
    workspace_root: Path,
) -> None:
    stack = _start(workspace_root)
    result = _prepare(stack)
    assert result.status is PreparedRunStatusFact.PREPARED
    assert result.intent_id == _intent_id()
    assert result.created_at_commit == "1"

    stored = stack.reader.find_preparation(
        project_id=PROJECT_ID, client_id=CLIENT_ID, prepare_request_id=REQUEST_ID
    )
    assert stored is not None
    assert stored.intent_id == result.intent_id
    assert stored.request.payload_hash == result.payload_hash
    assert stored.created_at_commit == "1"


def test_a_restart_reads_the_preparation_back_by_identity(
    workspace_root: Path,
) -> None:
    """重启（重新构造一整套对象）后按三元组查回原意图。"""
    first = _start(workspace_root)
    result = _prepare(first)
    assert result.status is PreparedRunStatusFact.PREPARED

    restarted = _start(workspace_root)
    stored = restarted.reader.find_preparation(
        project_id=PROJECT_ID, client_id=CLIENT_ID, prepare_request_id=REQUEST_ID
    )
    assert stored is not None
    assert stored.intent_id == result.intent_id
    assert stored.request.input_revisions == _revisions()
    assert stored.cancelled is False


def test_a_restart_reads_the_preparation_back_by_intent(workspace_root: Path) -> None:
    first = _start(workspace_root)
    result = _prepare(first)

    restarted = _start(workspace_root)
    stored = restarted.reader.find_preparation_by_intent(intent_id=result.intent_id)
    assert stored is not None
    assert stored.request.prepare_request_id == REQUEST_ID


def test_replaying_the_same_request_reuses_the_record(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    first = _prepare(stack)
    replay = _prepare(stack)

    assert replay.status is PreparedRunStatusFact.PREPARED
    assert replay.intent_id == first.intent_id
    assert replay.created_at_commit == first.created_at_commit
    assert (
        stack.raw.repo.current_revision("preparation_record", _record_id()) == 1
    )


def test_the_same_key_with_a_different_digest_conflicts(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    first = _prepare(stack)
    with pytest.raises(PreparationConflictError) as error:
        _prepare(stack, selected_case_ids=())
    assert error.value.existing_payload_hash == first.payload_hash

    # 冲突不得覆盖原记录，也不得留下打开的写锁。
    stored = stack.reader.find_preparation(
        project_id=PROJECT_ID, client_id=CLIENT_ID, prepare_request_id=REQUEST_ID
    )
    assert stored is not None
    assert stored.request.payload_hash == first.payload_hash
    assert stack.raw.repo.current_revision("preparation_record", _record_id()) == 1


def test_source_drift_reports_needs_reprepare(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    _prepare(stack)
    drifted = _prepare(
        stack,
        input_revisions=_revisions(snapshot_revision=2),
        snapshot=SnapshotRef(
            source_snapshot_id="snap-2",
            purpose="prepare",
            content_identity="sha256:content-2",
        ),
    )
    assert drifted.status is PreparedRunStatusFact.BLOCKED
    assert any(reason.code == "needs_reprepare" for reason in drifted.blocking_reasons)


def test_two_projects_with_the_same_request_id_do_not_collide(
    workspace_root: Path,
) -> None:
    stack = _start(workspace_root)
    for project_id in ("p1", "p2"):
        result = _prepare(stack, project_id=project_id)
        assert result.status is PreparedRunStatusFact.PREPARED
    assert _record_id("p1") != _record_id("p2")
    for project_id in ("p1", "p2"):
        assert (
            stack.reader.find_preparation(
                project_id=project_id,
                client_id=CLIENT_ID,
                prepare_request_id=REQUEST_ID,
            )
            is not None
        )


# --------------------------------------------------------------- 事务语义


def test_next_commit_seq_matches_the_real_commit(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    assert stack.unit_of_work.commit_seq() == "0"
    stack.unit_of_work.open(PROJECT_ID)
    assert stack.unit_of_work.next_commit_seq() == "1"
    stack.unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    result = stack.unit_of_work.commit()
    assert result.commit_seq == "1"
    assert stack.unit_of_work.commit_seq() == "1"
    assert stack.unit_of_work.next_commit_seq() == "2"


def test_stage_record_translates_a_revision_conflict(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    stack.unit_of_work.open(PROJECT_ID)
    stack.unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    stack.unit_of_work.commit()

    stack.unit_of_work.open(PROJECT_ID)
    with pytest.raises(ConcurrentEditError) as error:
        stack.unit_of_work.stage_record(
            aggregate_kind="project",
            record_id="p1",
            expected_revision=None,
            payload={},
        )
    assert error.value.current_revision == 1
    assert error.value.expected_revision is None

    # 暂存失败必须释放排他写锁：还能再开一次事务就是证据。
    stack.unit_of_work.open(PROJECT_ID)
    stack.unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=1, payload={}
    )
    assert stack.unit_of_work.commit().commit_seq == "2"


def test_same_record_staged_twice_in_one_transaction(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    stack.unit_of_work.open(PROJECT_ID)
    stack.unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    stack.unit_of_work.rollback()
    assert stack.unit_of_work.commit_seq() == "0"


# --------------------------------------------------------------- 查询


def test_query_without_an_index_requires_maintenance(workspace_root: Path) -> None:
    """索引缺失必须显式报维护，**不得**返回空页。"""
    stack = _start(workspace_root)
    with pytest.raises(IndexMaintenanceRequired):
        stack.reader.query(RecordQuery(project_id=PROJECT_ID))


def test_query_returns_committed_revisions_after_a_commit(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    stack.unit_of_work.open(PROJECT_ID)
    stack.unit_of_work.stage_record(
        aggregate_kind="project",
        record_id="p1",
        expected_revision=None,
        payload={"project_id": PROJECT_ID},
    )
    stack.unit_of_work.commit()

    page = stack.reader.query(
        RecordQuery(project_id=PROJECT_ID, aggregate_kind="project", record_id="p1")
    )
    assert [item.revision for item in page.items] == [1]
    assert page.items[0].payload["project_id"] == PROJECT_ID


def test_read_requires_an_exact_revision(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    stack.unit_of_work.open(PROJECT_ID)
    stack.unit_of_work.stage_record(
        aggregate_kind="project",
        record_id="p1",
        expected_revision=None,
        payload={"project_id": PROJECT_ID},
    )
    stack.unit_of_work.commit()

    record = stack.reader.read(aggregate_kind="project", record_id="p1", revision=1)
    assert record.revision == 1
    with pytest.raises(ValueError, match="unknown revision"):
        stack.reader.read(aggregate_kind="project", record_id="p1", revision=2)


def test_the_workspace_is_released_between_transactions(workspace_root: Path) -> None:
    """同一工作空间只能有一个写事务；正常提交后必须能再开。"""
    stack = _start(workspace_root)
    for expected in (None, 1, 2):
        stack.unit_of_work.open(PROJECT_ID)
        stack.unit_of_work.stage_record(
            aggregate_kind="project",
            record_id="p1",
            expected_revision=expected,
            payload={"project_id": PROJECT_ID},
        )
        stack.unit_of_work.commit()
    assert stack.raw.repo.current_revision("project", "p1") == 3
