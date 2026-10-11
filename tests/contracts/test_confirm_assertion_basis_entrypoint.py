"""受控依据确认动作 `confirm_assertion_basis` 的出口测试（检查项 B-01）。

重点：

1. **确认必须绑定真实存在的依据**：调用方只能声明"我核对了哪个用例的哪一版依据"，
   系统按**准确用例修订**读回记录并逐字比对依据摘要；对不上就拒绝，什么都不写；
2. **`case_revision` 必须是准确修订**：不存在的修订与不存在的用例都指名报错；
3. **两个字段由系统派生，调用方不得自称**：
   `confirmation_id`（同一事实同一标识）与 `confirmed_at_commit`（保存前的提交序号）；
4. **确认只追加**：同一事实再确认一次报 `B_REVISION_CONFLICT`，而不是静默覆盖。

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

from aitest.application.planning.persistence import (
    CONFIRMATION_AGGREGATE,
    load_confirmation,
)
from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.contracts.commands import Command
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.b_registration import register_b_use_cases

PROJECT_ID = "project-confirm"
WORKSPACE_ID = "ws-confirm"
BASIS_DIGEST = "sha256:basis-1"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


class _Sequences:
    def __init__(self, root: Path) -> None:
        self._orchestrator = RecoveryOrchestrator(root, instance_id="confirm-test")

    def current_commit_sequence(self) -> int:
        committed = self._orchestrator.inspect()["committed_sequences"]
        assert isinstance(committed, tuple)
        return max(committed) if committed else 0


@dataclass(frozen=True, slots=True)
class _Stack:
    unit_of_work: PortsUnitOfWork
    reader: PortsRecordReader


def _start(root: Path) -> _Stack:
    raw = FileUnitOfWork(root)
    repository = raw.repo
    return _Stack(
        unit_of_work=PortsUnitOfWork(
            raw, repository=repository, sequence=_Sequences(root)
        ),
        reader=PortsRecordReader(repository),
    )


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) / f"aitest-confirm-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _api(stack: _Stack) -> LocalAPI:
    api = LocalAPI(instance_id="core-confirm", workspace_id=WORKSPACE_ID, handlers={})
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
    return Session(session_id="cli-confirm", entry_kind=EntryKind.HUMAN_UI)


def _command(
    action: str,
    request_id: str,
    parameters: Mapping[str, object],
    *,
    expected_revision: int = 0,
) -> Command:
    return Command(
        request_id=request_id,
        action=action,
        project_id=PROJECT_ID,
        expected_revision=expected_revision,
        intent_id=f"intent-{request_id}",
        parameters=dict(parameters),
    )


def _case_payload(
    *, case_id: str = "case-1", revision: int = 1, text_digest: str = BASIS_DIGEST
) -> Mapping[str, object]:
    return {
        "case_id": case_id,
        "revision": revision,
        "layer": "L2",
        "objective": "create a ticket through the registered handler",
        "preconditions": ["the service is running"],
        "inputs": ["ticket body"],
        "steps": ["POST /tickets", "GET /tickets/{id}"],
        "expected": "the created ticket is read back unchanged",
        "verification_method": "read-only query against the same ticket id",
        "links": {
            "acceptance_item_ids": ["ai-1"],
            "module_ids": ["module-ticket"],
            "environment_ids": ["env-local"],
            "critical_path_ids": ["path-1"],
        },
        "assertion_basis": {
            "revision": 1,
            "state": "present_unconfirmed",
            "text": "check the persisted body",
            "text_digest": text_digest,
        },
        "importance": "P0",
    }


def _save_case(api: LocalAPI, *, request_id: str, payload: Mapping[str, object]) -> None:
    response = api.dispatch(
        _command("save_case", request_id, {"case": dict(payload)}), _session()
    )
    assert response.error is None, response.error


# ------------------------------------------------------------------ 正常路径


def test_a_confirmation_binds_the_existing_basis(workspace_root: Path) -> None:
    """确认登记成功，并且读回的记录与声明的依据一致。"""
    stack = _start(workspace_root)
    api = _api(stack)
    _save_case(api, request_id="req-case-1", payload=_case_payload())

    response = api.dispatch(
        _command(
            "confirm_assertion_basis",
            "req-confirm-1",
            {
                "case_id": "case-1",
                "case_revision": 1,
                "basis_text_digest": BASIS_DIGEST,
            },
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    result = response.result
    # 系统派生的三项都在，且与声明的事实一致。
    assert result["basis_revision"] == 1
    assert result["confirmation_id"] == "confirmation-case-1-r1"
    assert str(result["confirmed_at_commit"]).strip()
    assert result["record_id"] == result["confirmation_id"]

    # 重启后按记录读回：确认绑定的是真实存在的依据。
    restarted = _start(workspace_root)
    stored = load_confirmation(
        restarted.reader,
        project_id=PROJECT_ID,
        confirmation_id="confirmation-case-1-r1",
    )
    assert stored.case_id == "case-1"
    assert stored.basis_revision == 1
    assert stored.basis_text_digest == BASIS_DIGEST


def test_the_confirmation_commit_marker_matches_the_commit_that_stored_it(
    workspace_root: Path,
) -> None:
    """`confirmed_at_commit` 是**登记这次确认的那个提交**，不是"最新提交"。

    比对对象是**落盘记录里的取值**：`save_*` 自带事务、返回后提交序号已经前进，
    所以"事后补读当前序号"必然对不上；这里比的是记录自身携带的事实。
    """
    stack = _start(workspace_root)
    api = _api(stack)
    _save_case(api, request_id="req-case-1", payload=_case_payload())

    response = api.dispatch(
        _command(
            "confirm_assertion_basis",
            "req-confirm-1",
            {"case_id": "case-1", "case_revision": 1, "basis_text_digest": BASIS_DIGEST},
        ),
        _session(),
    )
    assert response.error is None and response.result is not None
    stored = _start(workspace_root).reader.read(
        aggregate_kind=CONFIRMATION_AGGREGATE,
        record_id="confirmation-case-1-r1",
        revision=1,
    )
    assert response.result["confirmed_at_commit"] == stored.payload["confirmed_at_commit"]


# ------------------------------------------------------------------ 拒绝路径


def test_confirming_a_case_revision_that_does_not_exist_is_refused(
    workspace_root: Path,
) -> None:
    stack = _start(workspace_root)
    api = _api(stack)
    _save_case(api, request_id="req-case-1", payload=_case_payload())

    response = api.dispatch(
        _command(
            "confirm_assertion_basis",
            "req-confirm-bad",
            {"case_id": "case-1", "case_revision": 9, "basis_text_digest": BASIS_DIGEST},
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "case-1@9" in response.error.message


def test_confirming_a_digest_that_the_case_does_not_carry_is_refused(
    workspace_root: Path,
) -> None:
    """这是关键一条：**不能确认一个依据摘要对不上的修订**。

    `ConfirmationRecord` 构造时并不校验依据是否存在，只凭声明就会记下
    "确认了某个不存在的依据"的记录——那种记录将来永远匹配不上任何依据。
    """
    stack = _start(workspace_root)
    api = _api(stack)
    _save_case(api, request_id="req-case-1", payload=_case_payload())

    response = api.dispatch(
        _command(
            "confirm_assertion_basis",
            "req-confirm-mismatch",
            {
                "case_id": "case-1",
                "case_revision": 1,
                "basis_text_digest": "sha256:something-else",
            },
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "does not match" in response.error.message
    # 被拒时什么都没写。
    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="unknown revision"):
        restarted.reader.read(
            aggregate_kind=CONFIRMATION_AGGREGATE,
            record_id="confirmation-case-1-r1",
            revision=1,
        )


def test_the_caller_cannot_supply_the_confirmation_identity(
    workspace_root: Path,
) -> None:
    """`confirmation_id` 由系统派生：调用方传了也不被采信。"""
    stack = _start(workspace_root)
    api = _api(stack)
    _save_case(api, request_id="req-case-1", payload=_case_payload())

    response = api.dispatch(
        _command(
            "confirm_assertion_basis",
            "req-confirm-forge",
            {
                "case_id": "case-1",
                "case_revision": 1,
                "basis_text_digest": BASIS_DIGEST,
                "confirmation_id": "confirmation-i-made-this-up",
                "confirmed_at_commit": "commit-999",
            },
        ),
        _session(),
    )
    assert response.error is None and response.result is not None
    # 仍然是系统派生的标识与提交序号，不是调用方给的那两个。
    assert response.result["confirmation_id"] == "confirmation-case-1-r1"
    assert response.result["confirmed_at_commit"] != "commit-999"


def test_confirming_the_same_basis_twice_is_reported_as_a_conflict(
    workspace_root: Path,
) -> None:
    """确认只追加：同一事实再确认一次是**修订冲突**，不是静默覆盖。"""
    stack = _start(workspace_root)
    api = _api(stack)
    _save_case(api, request_id="req-case-1", payload=_case_payload())
    parameters = {
        "case_id": "case-1",
        "case_revision": 1,
        "basis_text_digest": BASIS_DIGEST,
    }
    first = api.dispatch(
        _command("confirm_assertion_basis", "req-confirm-1", parameters), _session()
    )
    assert first.error is None

    second = api.dispatch(
        _command("confirm_assertion_basis", "req-confirm-2", parameters), _session()
    )
    assert second.error is not None
    assert second.error.code == "B_REVISION_CONFLICT"


def test_a_new_basis_revision_becomes_a_new_confirmation(
    workspace_root: Path,
) -> None:
    """依据修订变化时产生**新**确认，旧确认按原样读得到。"""
    stack = _start(workspace_root)
    api = _api(stack)
    _save_case(api, request_id="req-case-1", payload=_case_payload())
    first = api.dispatch(
        _command(
            "confirm_assertion_basis",
            "req-confirm-1",
            {"case_id": "case-1", "case_revision": 1, "basis_text_digest": BASIS_DIGEST},
        ),
        _session(),
    )
    assert first.error is None

    # 用例修订 2：依据文本改动，摘要随之变化。
    payload = dict(_case_payload(revision=2, text_digest="sha256:basis-2"))
    payload["assertion_basis"] = {
        "revision": 2,
        "state": "present_unconfirmed",
        "text": "check the persisted body twice",
        "text_digest": "sha256:basis-2",
    }
    payload["importance"] = "P0"
    response = api.dispatch(
        _command(
            "save_case",
            "req-case-2",
            {"case": payload},
            # B-11：用例已有 `@1`，新增修订必须声明"我看到的是 @1"。
            expected_revision=1,
        ),
        _session(),
    )
    assert response.error is None, response.error
    # B-11：正文修订必须等于这次分配的仓储修订，因此这条是 @2。
    api.dispatch(
        _command(
            "confirm_assertion_basis",
            "req-confirm-2",
            {"case_id": "case-1", "case_revision": 2, "basis_text_digest": "sha256:basis-2"},
        ),
        _session(),
    )
    restarted = _start(workspace_root)
    older = load_confirmation(
        restarted.reader,
        project_id=PROJECT_ID,
        confirmation_id="confirmation-case-1-r1",
    )
    newer = load_confirmation(
        restarted.reader,
        project_id=PROJECT_ID,
        confirmation_id="confirmation-case-1-r2",
    )
    # 两条都在，历史没有被改写。
    assert older.basis_revision == 1
    assert newer.basis_revision == 2
    assert newer.basis_text_digest == "sha256:basis-2"


def test_missing_parameters_are_named(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    for missing in ("case_id", "case_revision", "basis_text_digest"):
        parameters: dict[str, object] = {
            "case_id": "case-1",
            "case_revision": 1,
            "basis_text_digest": BASIS_DIGEST,
        }
        del parameters[missing]
        response = api.dispatch(
            _command("confirm_assertion_basis", f"req-missing-{missing}", parameters),
            _session(),
        )
        assert response.error is not None, missing
        assert response.error.code == "B_INVALID_PARAMETER"
