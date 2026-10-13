"""`save_plan` / `save_rules` 出口动作的合同测试：经**统一入口 + 真实文件底座**。

报告点名的两个"名称缺口"（FR06 `save_plan` 名称未接、FR05 `save_rules` 默认名称未接）
在这里锁定为可派发动作，重点：

1. **草稿与发布独立**：保存草稿**不跑发布门禁**，草稿可以尚未补齐；发布仍走
   `publish_plan`/`publish_rules` 的逐条门禁并产生下一修订；
2. **未发布不生效**：`save_rules` 只写 `rule_draft`，不产生 `rule_version`；
3. **同一处乐观锁**：保存使用调用方所见修订（`0` 表示新建），冲突报
   `B_REVISION_CONFLICT`（与发布同一条 `_revision_to_stage` 语义）；
4. **失败指名到字段**，且请求里自报 `status`/`confirmation_id` 不算数。

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

from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.contracts.commands import Command
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.b_registration import register_b_use_cases

PROJECT_ID = "project-draft"
WORKSPACE_ID = "ws-draft"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 13, 9, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


class _Sequences:
    def __init__(self, root: Path) -> None:
        self._orchestrator = RecoveryOrchestrator(root, instance_id="draft-entry-test")

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
        unit_of_work=PortsUnitOfWork(raw, repository=repository, sequence=_Sequences(root)),
        reader=PortsRecordReader(repository),
    )


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) / f"aitest-draft-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _api(stack: _Stack) -> LocalAPI:
    api = LocalAPI(instance_id="core-draft", workspace_id=WORKSPACE_ID, handlers={})
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
    return Session(session_id="cli-draft", entry_kind=EntryKind.HUMAN_UI)


def _command(
    action: str, request_id: str, parameters: Mapping[str, object], *, expected: int = 0
) -> Command:
    return Command(
        request_id=request_id,
        action=action,
        project_id=PROJECT_ID,
        expected_revision=expected,
        intent_id=f"intent-{request_id}",
        parameters=dict(parameters),
    )


def _index(workspace_root: Path) -> None:
    FileQueryIndex(workspace_root).rebuild(())


# ------------------------------------------------------------------ 请求内容


def _case(case_id: str, *, basis_state: str = "confirmed", **overrides: object):
    payload: dict[str, object] = {
        "case_id": case_id,
        "revision": 1,
        "layer": "L2",
        "objective": f"check {case_id} through the registered handler",
        "preconditions": ["the service is running"],
        "inputs": ["ticket body"],
        "steps": ["POST /tickets", "GET /tickets/{id}"],
        "expected": "the created ticket is read back unchanged",
        "verification_method": "read-only query against the same ticket id",
        "links": {
            "acceptance_item_ids": ["ai-1"],
            "module_ids": ["module-ticket"],
            "environment_ids": ["env-1"],
            "critical_path_ids": ["path-1"],
        },
        "assertion_basis": {
            "revision": 1,
            "state": basis_state,
            "text": "check the persisted body",
            "text_digest": "sha256:basis-1",
        },
        "independent_verification": "read-only query against the same ticket id",
        "mock_scope": [],
        "importance": "P0",
    }
    payload.update(overrides)
    if basis_state == "missing":
        payload["assertion_basis"] = {
            "revision": 1,
            "state": "missing",
            "text": "",
            "text_digest": "",
        }
    return payload


def _scope(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "scope_id": "scope-1",
        "revision": 1,
        "name": "ticket acceptance",
        "required_case_ids": ["case-1"],
        "template_case_ids": ["case-1"],
        "objective": "accept the ticket flow",
        "excluded_case_ids": [],
        "exclusion_reasons": [],
        "dependency_closure_ids": [],
        "applicability_exclusions": [],
    }
    payload.update(overrides)
    return payload


def _plan_parameters(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "plan_id": "plan-1",
        "revision": 1,
        "scope": _scope(),
        "cases": [_case("case-1")],
        "run_tier": "full",
        "initial_driver": "planned",
    }
    payload.update(overrides)
    return payload


def _rule_draft(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "rule_id": "rule-1",
        "revision": 1,
        "scope": "http workflows",
        "text": "check the status code and the persisted body",
        "source": "manual",
        "steps": ["call the endpoint", "read it back"],
        "evidence_requirements": ["raw response"],
        "enablement": "disabled",
        "confirmed": False,
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ save_plan


def test_save_plan_writes_a_draft_without_running_publication_gates(
    workspace_root: Path,
) -> None:
    """草稿可以尚未补齐：缺独立核验/依据的用例照样**保存成功**，但不算发布。"""

    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "save_plan",
            "req-draft-1",
            _plan_parameters(
                cases=[
                    _case("case-1", independent_verification=None),
                    _case("case-2", basis_state="missing"),
                ],
                scope=_scope(
                    required_case_ids=["case-1"],
                    template_case_ids=["case-1"],
                ),
            ),
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    saved = response.result["saved"]
    assert saved["aggregate_kind"] == "plan"
    assert saved["record_id"] == "plan-1"
    assert saved["revision"] == 1
    assert saved["status"] == "draft"
    # 草稿落盘内容可读，且 payload 里没有任何"已发布"标识。
    committed = _start(workspace_root).reader.read(
        aggregate_kind="plan", record_id="plan-1", revision=1
    )
    assert committed.payload["plan_id"] == "plan-1"
    assert "confirmation_id" not in committed.payload
    assert "status" not in committed.payload or committed.payload["status"] != "published"


def test_draft_then_publish_is_two_revisions_and_gates_still_apply(
    workspace_root: Path,
) -> None:
    """草稿与发布独立：草稿@1 成功；同一内容发布仍要过门禁，过了才产生@2。"""

    _index(workspace_root)
    api = _api(_start(workspace_root))
    draft = api.dispatch(
        _command("save_plan", "req-draft-1", _plan_parameters()), _session()
    )
    assert draft.error is None and draft.result is not None
    assert draft.result["published"] is False

    blocked = api.dispatch(
        _command(
            "publish_plan",
            "req-publish-bad",
            _plan_parameters(cases=[_case("case-1", independent_verification=None)]),
            expected=1,
        ),
        _session(),
    )
    assert blocked.error is None, blocked.error
    assert blocked.result is not None
    assert blocked.result["published"] is False
    assert blocked.result["blocked_by"]

    published = api.dispatch(
        _command("publish_plan", "req-publish-good", _plan_parameters(), expected=1),
        _session(),
    )
    assert published.error is None, published.error
    assert published.result is not None
    assert published.result["published"] is True
    reader = _start(workspace_root).reader
    assert reader.read(aggregate_kind="plan", record_id="plan-1", revision=1).revision == 1
    assert reader.read(aggregate_kind="plan", record_id="plan-1", revision=2).revision == 2


def test_save_plan_uses_the_same_optimistic_lock_as_publication(
    workspace_root: Path,
) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    first = api.dispatch(_command("save_plan", "req-draft-1", _plan_parameters()), _session())
    assert first.error is None and first.result is not None

    # 同一份"我认定这是新记录"再存一次：必须冲突，不自动覆盖。
    stale = api.dispatch(_command("save_plan", "req-draft-2", _plan_parameters()), _session())
    assert stale.error is not None
    assert stale.error.code == "B_REVISION_CONFLICT"

    next_revision = api.dispatch(
        _command("save_plan", "req-draft-3", _plan_parameters(), expected=1), _session()
    )
    assert next_revision.error is None, next_revision.error
    assert next_revision.result is not None
    assert next_revision.result["saved"]["revision"] == 2


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("plan_id", "", "plan_id"),
        ("revision", 0, "revision"),
        ("cases", [], "cases"),
        ("scope", "not-an-object", "scope"),
        ("run_tier", "turbo", "run_tier"),
        ("initial_driver", "sideways", "initial_driver"),
    ],
)
def test_invalid_save_plan_request_is_named(
    workspace_root: Path, field: str, value: object, named: str
) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    parameters = _plan_parameters()
    parameters[field] = value
    response = api.dispatch(_command("save_plan", "req-draft-bad", parameters), _session())
    assert response.error is not None, (field, response.error)
    assert response.error.code == "B_INVALID_PARAMETER"
    assert named in response.error.message


def test_save_plan_ignores_self_declared_status(workspace_root: Path) -> None:
    """请求里自报 `status=published` 不算数：草稿永远只是草稿。"""

    _index(workspace_root)
    api = _api(_start(workspace_root))
    parameters = _plan_parameters()
    parameters["status"] = "published"
    parameters["confirmation_id"] = "self-declared"
    response = api.dispatch(_command("save_plan", "req-draft-1", parameters), _session())
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    assert response.result["saved"]["status"] == "draft"


# ------------------------------------------------------------------ save_rules


def test_save_rules_writes_only_a_draft_and_takes_no_effect(
    workspace_root: Path,
) -> None:
    """未发布不生效：`save_rules` 只写 `rule_draft`，不产生 `rule_version`。"""

    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("save_rules", "req-rule-1", {"draft": _rule_draft()}), _session()
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    saved = response.result["saved"]
    assert saved["aggregate_kind"] == "rule_draft"
    assert saved["record_id"] == "rule-1"
    assert saved["revision"] == 1
    assert saved["confirmed"] is False
    assert saved["enablement"] == "disabled"
    # 没有发布版本：规则尚未生效。
    reader = _start(workspace_root).reader
    assert reader.current_revision(aggregate_kind="rule_version", record_id="rule-1") == 0
    assert reader.current_revision(aggregate_kind="rule_draft", record_id="rule-1") == 1


def test_saved_rule_draft_can_then_be_published(workspace_root: Path) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    saved = api.dispatch(
        _command("save_rules", "req-rule-1", {"draft": _rule_draft()}), _session()
    )
    assert saved.error is None and saved.result is not None

    published = api.dispatch(
        _command(
            "publish_rules",
            "req-rule-publish",
            {"draft": _rule_draft(confirmed=True, enablement="enabled")},
        ),
        _session(),
    )
    assert published.error is None, published.error
    assert published.result is not None
    assert published.result["published"] is True
    assert (
        _start(workspace_root).reader.current_revision(
            aggregate_kind="rule_version", record_id="rule-1"
        )
        == 1
    )


def test_save_rules_conflicts_and_appends_by_seen_revision(workspace_root: Path) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    first = api.dispatch(
        _command("save_rules", "req-rule-1", {"draft": _rule_draft()}), _session()
    )
    assert first.error is None, first.error
    stale = api.dispatch(
        _command("save_rules", "req-rule-2", {"draft": _rule_draft()}), _session()
    )
    assert stale.error is not None and stale.error.code == "B_REVISION_CONFLICT"
    appended = api.dispatch(
        _command(
            "save_rules",
            "req-rule-3",
            {"draft": _rule_draft(revision=2, text="second revision of the rule")},
            expected=1,
        ),
        _session(),
    )
    assert appended.error is None, appended.error
    assert appended.result is not None
    assert appended.result["saved"]["revision"] == 2


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("rule_id", "", "rule_id"),
        ("revision", 0, "revision"),
        ("text", "", "text"),
        ("enablement", "sometimes", "enablement"),
        ("confirmed", "yes", "confirmed"),
    ],
)
def test_invalid_save_rules_request_is_named(
    workspace_root: Path, field: str, value: object, named: str
) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    draft = _rule_draft()
    draft[field] = value
    response = api.dispatch(
        _command("save_rules", "req-rule-bad", {"draft": draft}), _session()
    )
    assert response.error is not None, (field, response.error)
    assert response.error.code == "B_INVALID_PARAMETER"
    assert named in response.error.message


def test_save_rules_requires_the_draft_object(workspace_root: Path) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(_command("save_rules", "req-rule-bad", {}), _session())
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "draft" in response.error.message
