"""C-12：冻结环境步骤截止的准入守卫（不依赖工作空间装配的快速回归）。

真实文件事务版正/反例见 test_execution_authorization_origin.py；本文件用假仓库
直接固定守卫的判定分支，使它在任何环境（含本机受限沙箱）都能运行。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.domain.approvals import ApprovalRequired

ENVIRONMENT = SimpleNamespace(environment_id="env-1", revision=1)


class _Record(BaseModel):
    aggregate_kind: str
    record_id: str
    revision: int
    payload: dict[str, object]


class _FakeRecords:
    def __init__(self, record: object) -> None:
        self._record = record
        self.calls: list[tuple[str, str, int]] = []

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> object:
        self.calls.append((aggregate_kind, record_id, revision))
        if isinstance(self._record, Exception):
            raise self._record
        return self._record


def _service(record: object) -> ExecutionAuthorizationService:
    service = object.__new__(ExecutionAuthorizationService)
    service.records = _FakeRecords(record)
    service.workspace_id = "ws-1"
    return service


def _record(payload: dict[str, object]) -> _Record:
    return _Record(
        aggregate_kind="environment", record_id="env-1", revision=1, payload=payload
    )


def _prepared() -> object:
    return SimpleNamespace(environment=ENVIRONMENT)


def _resolved(timeout_ms: int | None) -> object:
    return SimpleNamespace(request=SimpleNamespace(timeout_ms=timeout_ms))


def test_declared_deadline_is_converted_to_milliseconds() -> None:
    service = _service(_record({"step_timeout_seconds": 120}))
    assert service._frozen_step_deadline_ms(_prepared()) == 120_000


def test_absent_declaration_means_no_frozen_deadline() -> None:
    service = _service(_record({}))
    assert service._frozen_step_deadline_ms(_prepared()) is None
    service._require_frozen_step_deadline(_prepared(), _resolved(None))


def test_declared_deadline_must_be_used_exactly() -> None:
    service = _service(_record({"step_timeout_seconds": 120}))
    service._require_frozen_step_deadline(_prepared(), _resolved(120_000))
    for unfrozen in (None, 1, 119_999, 120_001, 999_000):
        with pytest.raises(ApprovalRequired, match="frozen step timeout"):
            service._require_frozen_step_deadline(_prepared(), _resolved(unfrozen))


def test_absent_declaration_rejects_an_invented_deadline() -> None:
    service = _service(_record({}))
    with pytest.raises(ApprovalRequired, match="frozen step timeout"):
        service._require_frozen_step_deadline(_prepared(), _resolved(1_000))


@pytest.mark.parametrize("declared", [True, False, 0, -1, 1.5, "120", [], {}])
def test_invalid_declaration_is_never_a_deadline(declared: object) -> None:
    service = _service(_record({"step_timeout_seconds": declared}))
    with pytest.raises(ApprovalRequired, match="cannot be verified"):
        service._frozen_step_deadline_ms(_prepared())


def test_unreadable_or_foreign_environment_record_is_rejected() -> None:
    unreadable = _service(FileNotFoundError("synthetic missing environment record"))
    with pytest.raises(ApprovalRequired, match="unreadable"):
        unreadable._frozen_step_deadline_ms(_prepared())

    foreign = _service(_Record(
        aggregate_kind="environment", record_id="other-env", revision=1, payload={}
    ))
    with pytest.raises(ApprovalRequired, match="envelope differs"):
        foreign._frozen_step_deadline_ms(_prepared())

    wrong_revision = _service(_Record(
        aggregate_kind="environment", record_id="env-1", revision=2, payload={}
    ))
    with pytest.raises(ApprovalRequired, match="envelope differs"):
        wrong_revision._frozen_step_deadline_ms(_prepared())


def test_guard_reads_the_exact_frozen_environment_revision() -> None:
    service = _service(_record({"step_timeout_seconds": 30}))
    service._require_frozen_step_deadline(_prepared(), _resolved(30_000))
    assert service.records.calls == [("environment", "env-1", 1)]
