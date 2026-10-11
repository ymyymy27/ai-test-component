"""薄转接头对 **A 的真实文件存储** 的接线验证。

测试对象**不是内存替身**：这里用 `infrastructure/file_store` 的
`FileUnitOfWork` / `FileRecordRepository` 与 `RecoveryOrchestrator` 真落盘，
再经 `PortsUnitOfWork` / `PortsRecordReader` 跑 B 的 `prepare_run`。
"重启后仍能按业务身份查回原意图"这条要求只有在这一层才测得出来——
内存替身无论 payload 少多少字段都能查到。

## 两处非默认写法及其原因

**一、不用 `tmp_path` / `tempfile.mkdtemp()`。** 受限执行环境拒绝 pytest 临时目录工厂
在 basetemp 上的目录枚举，也拒绝写入 `tempfile.mkdtemp()` 建出来的目录
（同一目录改用 `Path.mkdir()` 就能写）。本模块用 `Path.mkdir()` 在系统临时目录下建
等价目录并在结束时清理，被测语义不变，CI 与受限环境都能跑。

**二、提交序号由 `_Sequences` 提供。** `commit_seq()` / `next_commit_seq()` 尚未冻结进
A 的 `application/ports.py`（见 `docs/接口对接/进行中/AB-001-端口与保存/contract.md`
第 8.8 节）。`_Sequences` 取 A 的 `RecoveryOrchestrator.inspect()` 公开返回的
已提交序号，不访问存储内部文件。A 冻结签名后，装配点换成正式访问器即可。

## 事务作用域（B-Q10）

文件末尾一组测试专门证明**排他写锁不再泄漏**。它们不看提交序号，而是直接取锁：
`writer_lock` 是**非阻塞**排他锁（`timeout=0` + `LOCK_NB`），被占时再取会抛
`WorkspaceInUse`（同进程换一个句柄也一样），因此"还能不能取到锁"就是最直接的证据。
覆盖：作用域内抛异常、提前 `return`、对象被回收、转接头被回收、真实冲突路径、
`prepare_run` 的全部返回分支，以及"应用用例不得绕过作用域直接 `open()`"的源码守卫。
"""

from __future__ import annotations

import ast
import gc
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aitest.application.errors import WorkspaceInUse
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
    transaction,
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
    GapEntry,
    PlanRevisionRef,
    PreparedRun,
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
    """当前提交序号的只读来源；取 A 的恢复巡检公开返回的已提交序号。"""

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
        "scope_id": "scope-1",
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


def _prepare(stack: _Stack, **overrides: object) -> PreparedRun:
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


# --------------------------------------------------- 事务作用域（B-Q10）

_APPLICATION_ROOT = Path(__file__).resolve().parents[2] / "src" / "aitest" / "application"
#: 端口层自己就是事务作用域的实现处，`Transaction` 必须调 `open` / `rollback`。
_PORT_LAYER_MODULE = _APPLICATION_ROOT / "planning" / "substrate.py"

#: 会写记录的 B 应用用例；守卫测试要求它们经 `transaction()` 而不是裸 `open()`。
_WRITE_ORCHESTRATIONS = (
    "planning/prepare_run.py",
    "planning/publish.py",
    "planning/model_orchestration.py",
    "project/persistence.py",
)


def _lock_is_free(raw: FileUnitOfWork) -> bool:
    """排他写锁现在能不能取到——能取到就证明上一个事务真的放锁了。

    `writer_lock` 是**非阻塞**排他锁（`timeout=0` + `LOCK_NB`）：被占时
    `Workspace.acquire()` 抛 `WorkspaceInUse`，同进程换一个句柄再取同样失败。
    因此这是"锁有没有泄漏"的**直接**证据，比看提交序号更贴近问题本身。
    """
    acquired = raw.workspace.acquire()
    try:
        acquired.__enter__()
    except WorkspaceInUse:
        return False
    acquired.__exit__(None, None, None)
    return True


def _stage_one_project(stack: _Stack) -> None:
    with transaction(stack.unit_of_work, PROJECT_ID) as tx:
        tx.stage_record(
            aggregate_kind="project",
            record_id="p1",
            expected_revision=None,
            payload={"project_id": PROJECT_ID},
        )
        tx.commit()


def _stage_then_return_early(stack: _Stack) -> str:
    """在作用域内暂存后**提前 return**：既不提交，也不显式回滚。"""
    with transaction(stack.unit_of_work, PROJECT_ID) as tx:
        tx.stage_record(
            aggregate_kind="project",
            record_id="p1",
            expected_revision=None,
            payload={"project_id": PROJECT_ID},
        )
        return "returned early"


def test_the_lock_probe_detects_a_held_lock(workspace_root: Path) -> None:
    """先证明探针本身有效：裸 `open()` 握着锁时探针必须报"取不到"。

    没有这条，下面所有"锁是自由的"断言都可能只是探针失灵。
    """
    stack = _start(workspace_root)
    stack.unit_of_work.open(PROJECT_ID)
    assert not _lock_is_free(stack.raw)
    stack.unit_of_work.rollback()
    assert _lock_is_free(stack.raw)


def test_a_raise_inside_the_scope_releases_the_lock(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    with (
        pytest.raises(RuntimeError, match="boom"),
        transaction(stack.unit_of_work, PROJECT_ID) as tx,
    ):
        tx.stage_record(
            aggregate_kind="project",
            record_id="p1",
            expected_revision=None,
            payload={"project_id": PROJECT_ID},
        )
        raise RuntimeError("boom")

    assert _lock_is_free(stack.raw)
    # 未提交的暂存不得留下任何可见修订。
    assert stack.raw.repo.current_revision("project", "p1") == 0


def test_an_early_return_inside_the_scope_releases_the_lock(
    workspace_root: Path,
) -> None:
    stack = _start(workspace_root)
    assert _stage_then_return_early(stack) == "returned early"

    assert _lock_is_free(stack.raw)
    assert stack.raw.repo.current_revision("project", "p1") == 0


def test_a_committed_scope_keeps_its_records_and_releases_the_lock(
    workspace_root: Path,
) -> None:
    stack = _start(workspace_root)
    _stage_one_project(stack)

    assert _lock_is_free(stack.raw)
    assert stack.raw.repo.current_revision("project", "p1") == 1

    # 提交过的记录在退出作用域后**不得**被收尾回滚掉。
    again = _start(workspace_root)
    assert again.raw.repo.current_revision("project", "p1") == 1


def test_the_scope_rolls_back_when_the_body_forgets_to_commit(
    workspace_root: Path,
) -> None:
    """`with` 体内没提交就退出：宁可不写，也不留锁。"""
    stack = _start(workspace_root)
    with transaction(stack.unit_of_work, PROJECT_ID) as tx:
        tx.stage_record(
            aggregate_kind="project",
            record_id="p1",
            expected_revision=None,
            payload={"project_id": PROJECT_ID},
        )

    assert _lock_is_free(stack.raw)
    assert stack.raw.repo.current_revision("project", "p1") == 0


def test_staging_without_entering_the_scope_fails_without_taking_the_lock(
    workspace_root: Path,
) -> None:
    """只创建、不进入（忘写 `with`）：**根本没有取锁**，而且是响亮报错而不是静默写入。"""
    stack = _start(workspace_root)
    scope = transaction(stack.unit_of_work, PROJECT_ID)
    with pytest.raises(RuntimeError, match="no open transaction"):
        scope.stage_record(
            aggregate_kind="project",
            record_id="p1",
            expected_revision=None,
            payload={"project_id": PROJECT_ID},
        )
    assert _lock_is_free(stack.raw)
    # 未进入即"已收尾"：显式收尾是幂等的，不得碰别人的事务。
    scope.settle()
    assert _lock_is_free(stack.raw)

    _stage_one_project(stack)
    assert stack.raw.repo.current_revision("project", "p1") == 1


def test_a_dropped_scope_releases_the_lock(workspace_root: Path) -> None:
    """兜底路径：手工进入作用域后把对象丢掉，回收时也要把锁还回去。"""
    stack = _start(workspace_root)
    scope = transaction(stack.unit_of_work, PROJECT_ID)
    scope.__enter__()
    scope.stage_record(
        aggregate_kind="project",
        record_id="p1",
        expected_revision=None,
        payload={"project_id": PROJECT_ID},
    )
    assert not _lock_is_free(stack.raw)

    del scope
    gc.collect()
    assert _lock_is_free(stack.raw)
    assert stack.raw.repo.current_revision("project", "p1") == 0


def test_a_dropped_adapter_releases_the_lock(workspace_root: Path) -> None:
    """最后一道网：转接头自己（含裸 `open()` 的事务）被回收时必须放锁。"""
    stack = _start(workspace_root)
    raw = stack.raw
    stack.unit_of_work.open(PROJECT_ID)  # 故意既不提交也不回滚
    assert not _lock_is_free(raw)

    del stack
    gc.collect()
    assert _lock_is_free(raw)


def test_a_preparation_conflict_releases_the_lock(workspace_root: Path) -> None:
    """真实失败路径：同键异摘要的冲突也必须把锁还回去。"""
    stack = _start(workspace_root)
    first = _prepare(stack)
    with pytest.raises(PreparationConflictError):
        _prepare(stack, selected_case_ids=())

    assert _lock_is_free(stack.raw)
    assert stack.raw.repo.current_revision("preparation_record", _record_id()) == 1
    assert first.status is PreparedRunStatusFact.PREPARED


def test_every_prepare_run_branch_releases_the_lock(workspace_root: Path) -> None:
    """`prepare_run` 的每个返回分支都不得把锁留着（真写、复用、阻塞、需重新准备）。"""
    stack = _start(workspace_root)

    assert _prepare(stack).status is PreparedRunStatusFact.PREPARED
    assert _lock_is_free(stack.raw)

    # 幂等复用：不写记录，也不得开事务后不关。
    assert _prepare(stack).status is PreparedRunStatusFact.PREPARED
    assert _lock_is_free(stack.raw)

    blocked = _prepare(
        stack,
        context_gaps=(
            GapEntry(
                gap_id="gap-1",
                kind="missing_environment",
                subject="env-1",
                blocking=True,
                detail="no environment carrier",
            ),
        ),
    )
    assert blocked.status is PreparedRunStatusFact.BLOCKED
    assert _lock_is_free(stack.raw)

    drifted = _prepare(stack, input_revisions=_revisions(snapshot_revision=2))
    assert drifted.status is PreparedRunStatusFact.BLOCKED
    assert any(
        reason.code == "needs_reprepare" for reason in drifted.blocking_reasons
    )
    assert _lock_is_free(stack.raw)


def test_application_use_cases_never_open_transactions_directly() -> None:
    """源码守卫：`application/**` 除端口层外，不得直接 `open()` / `rollback()` 事务。

    裸 `open()` 之后的收尾靠调用方的记性，而真实底座的 `begin()` 会取工作空间级
    排他写锁（B-Q10）。用例统一走 `substrate.transaction()`；这条测试是为了防止
    后来者在某个新用例里又写回裸 `open()`——那样锁的保证会重新出现缺口。
    """
    offenders: list[str] = []
    for path in sorted(_APPLICATION_ROOT.rglob("*.py")):
        if path == _PORT_LAYER_MODULE:
            continue
        # `utf-8-sig`：仓库里有带 BOM 的源文件，带 BOM 的文本 `ast.parse` 会直接报错。
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"open", "rollback"}:
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and (
                receiver.id == "uow" or "unit_of_work" in receiver.id
            ):
                offenders.append(
                    f"{path.relative_to(_APPLICATION_ROOT)}:{node.lineno} "
                    f"{receiver.id}.{node.func.attr}()"
                )
    assert offenders == []

    # 反向核对：真正写记录的用例确实用了事务上下文（防止守卫变成空转）。
    for relative in _WRITE_ORCHESTRATIONS:
        source = (_APPLICATION_ROOT / relative).read_text(encoding="utf-8")
        assert "transaction(" in source, relative
