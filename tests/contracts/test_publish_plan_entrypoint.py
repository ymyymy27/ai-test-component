"""`publish_plan` 出口动作的合同测试：经**统一入口 + 真实文件底座**发布计划。

测的是"请求形状与门禁接线"，不是门禁规则本身（后者由
`tests/unit/test_publish_orchestration.py` 覆盖）。重点：

1. **请求形状**：`plan_id` / `revision` / `scope` / `cases` 为人工给定；
   `status`、`confirmation_id`、用例 `digest` **由服务端产生**，请求里给不给都不算数；
2. **门禁仍在**：必测缺冻结引用、依据缺失、缺独立核验、模板必测不在必测集合内，
   都要按 `PublicationResult` 报"被拒并给出原因"，而不是静默通过；
3. **身份摘要来自记录**：`rule_revisions` 的 `digest` 取自已发布的 `rule_version` 记录，
   记录不存在即拒绝，不填占位值；
4. **失败指名到字段**。

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

PROJECT_ID = "project-plan"
WORKSPACE_ID = "ws-plan"
TEMPLATE_ID = "http-workflow"
TEMPLATE_VERSION = "1.0.0"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 2, 11, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


class _Sequences:
    def __init__(self, root: Path) -> None:
        self._orchestrator = RecoveryOrchestrator(root, instance_id="plan-entry-test")

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
    root = Path(tempfile.gettempdir()) / f"aitest-plan-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _api(stack: _Stack) -> LocalAPI:
    api = LocalAPI(instance_id="core-plan", workspace_id=WORKSPACE_ID, handlers={})
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
    return Session(session_id="cli-plan", entry_kind=EntryKind.HUMAN_UI)


def _command(action: str, request_id: str, parameters: Mapping[str, object]) -> Command:
    return Command(
        request_id=request_id,
        action=action,
        project_id=PROJECT_ID,
        expected_revision=0,
        intent_id=f"intent-{request_id}",
        parameters=dict(parameters),
    )


def _index(workspace_root: Path) -> None:
    """建空索引：`publish_*` 要读当前修订，走有界查询而不隐式全扫。"""
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
        payload["assertion_basis"] = {"revision": 1, "state": "missing",
                                      "text": "", "text_digest": ""}
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
        "template_versions": [
            {"template_id": TEMPLATE_ID, "version": TEMPLATE_VERSION}
        ],
        "run_tier": "full",
        "initial_driver": "planned",
    }
    payload.update(overrides)
    return payload


def _publish_rule(api: LocalAPI, *, rule_id: str = "rule-1", revision: int = 1) -> None:
    """先发布一条规则，供计划引用其已发布修订。"""
    response = api.dispatch(
        _command(
            "publish_rules",
            f"req-rule-{rule_id}-{revision}",
            {
                "draft": {
                    "rule_id": rule_id,
                    "revision": revision,
                    "scope": "http workflows",
                    "text": "check the status code and the persisted body",
                    "source": "manual",
                    "steps": ["call the endpoint", "read it back"],
                    "evidence_requirements": ["raw response"],
                    "enablement": "enabled",
                    "confirmed": True,
                }
            },
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None and response.result["published"] is True


# ------------------------------------------------------------------ 成功路径


def test_publish_plan_reaches_the_entry_and_freezes_the_scope(
    workspace_root: Path,
) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("publish_plan", "req-plan-1", _plan_parameters()), _session()
    )
    assert response.error is None, response.error
    assert response.result is not None
    result = response.result
    assert result["published"] is True
    assert result["kind"] == "plan"
    assert result["plan_id"] == "plan-1"
    assert result["revision"] == 1
    assert result["scope_id"] == "scope-1"
    assert result["confirmation_id"]
    # 用例摘要是**服务端按内容**算出来的。
    revisions = result["case_revisions"]
    assert isinstance(revisions, list) and len(revisions) == 1
    assert revisions[0]["case_id"] == "case-1"
    assert str(revisions[0]["digest"]).startswith("sha256:")
    # 模板版本摘要同样由服务端算出。
    templates = result["template_versions"]
    assert isinstance(templates, list) and len(templates) == 1
    assert str(templates[0]["digest"]).startswith("sha256:")


def test_published_plan_is_readable_by_commit_revision(workspace_root: Path) -> None:
    """发布结果落盘后按准确修订读回：`plan` 记录可核对。"""
    _index(workspace_root)
    api = _api(_start(workspace_root))
    api.dispatch(_command("publish_plan", "req-plan-1", _plan_parameters()), _session())
    committed = _start(workspace_root).reader.read(
        aggregate_kind="plan", record_id="plan-1", revision=1
    )
    assert committed.payload["project_id"] == PROJECT_ID
    assert committed.payload["plan_id"] == "plan-1"
    assert committed.payload["scope_id"] == "scope-1"
    assert committed.payload["scope_revision"] == 1
    assert committed.payload["run_tier"] == "full"


def test_rule_digest_comes_from_the_published_record(workspace_root: Path) -> None:
    """`rule_revisions` 只给标识与修订，摘要取自已发布记录。"""
    _index(workspace_root)
    api = _api(_start(workspace_root))
    _publish_rule(api)
    response = api.dispatch(
        _command(
            "publish_plan",
            "req-plan-1",
            _plan_parameters(rule_revisions=[{"rule_id": "rule-1", "revision": 1}]),
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    rules = response.result["rule_revisions"]
    assert isinstance(rules, list) and len(rules) == 1
    assert rules[0]["rule_id"] == "rule-1"
    assert str(rules[0]["digest"]).startswith("sha256:")
    # 该摘要必须与规则发布时落盘的那一份一致（不是另算一套）。
    committed = _start(workspace_root).reader.read(
        aggregate_kind="plan", record_id="plan-1", revision=1
    )
    assert committed.payload["rule_revisions"] == rules


# ------------------------------------------------------------------ 门禁与失败路径


def test_unpublished_rule_version_is_rejected(workspace_root: Path) -> None:
    """引用没有发布过的规则版本：拒绝，且不填占位摘要。"""
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "publish_plan",
            "req-plan-1",
            _plan_parameters(rule_revisions=[{"rule_id": "rule-1", "revision": 1}]),
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "rule-1@1" in response.error.message


def test_case_without_independent_verification_is_refused(
    workspace_root: Path,
) -> None:
    """门禁：冻结用例缺独立核验方式 → 被拒并给出原因。"""
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "publish_plan",
            "req-plan-1",
            _plan_parameters(
                cases=[_case("case-1", independent_verification=None)]
            ),
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    assert response.result["blocked_by"]


def test_case_with_missing_assertion_basis_is_refused(workspace_root: Path) -> None:
    """门禁：冻结必测含依据缺失用例 → 被拒（"缺依据禁止 full 必测"）。"""
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "publish_plan",
            "req-plan-1",
            _plan_parameters(cases=[_case("case-1", basis_state="missing")]),
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    assert response.result["blocked_by"]


def test_required_case_without_a_frozen_reference_is_refused(
    workspace_root: Path,
) -> None:
    """门禁：必测项没有冻结引用 → 拒绝，不得用集合交集静默滤掉。"""
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "publish_plan",
            "req-plan-1",
            _plan_parameters(
                scope=_scope(
                    required_case_ids=["case-1", "case-missing"],
                    template_case_ids=["case-1"],
                )
            ),
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    assert response.result["blocked_by"]


def test_case_in_scope_without_content_is_refused(workspace_root: Path) -> None:
    """必测里有某用例，但请求没给它的内容：拒绝，不能用集合交集静默滤掉。

    （"冻结 `@1` 却提供 `@2`"那条门禁由域级用例覆盖：出口的冻结引用由请求内容派生，
    因此出口层构造不出二者不一致的场景。）
    """
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "publish_plan",
            "req-plan-1",
            _plan_parameters(
                scope=_scope(
                    required_case_ids=["case-1", "case-2"],
                    template_case_ids=["case-1"],
                ),
                cases=[_case("case-1")],
            ),
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    assert response.result["blocked_by"]


def test_unknown_template_is_reported(workspace_root: Path) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "publish_plan",
            "req-plan-1",
            _plan_parameters(
                template_versions=[
                    {"template_id": "no-such-template", "version": "1.0.0"}
                ]
            ),
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER" or "template" in (
        response.error.message
    )


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
def test_invalid_plan_request_is_named(
    workspace_root: Path, field: str, value: object, named: str
) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    parameters = _plan_parameters()
    parameters[field] = value
    response = api.dispatch(
        _command("publish_plan", "req-plan-bad", parameters), _session()
    )
    assert response.error is not None, (field, response.error)
    assert response.error.code == "B_INVALID_PARAMETER"
    assert named in response.error.message


def test_status_and_confirmation_in_the_request_are_ignored(
    workspace_root: Path,
) -> None:
    """请求里自报 `status` / `confirmation_id` 不算数：发布事实由动作与提交序号产生。"""
    _index(workspace_root)
    api = _api(_start(workspace_root))
    parameters = _plan_parameters()
    parameters["status"] = "published"
    parameters["confirmation_id"] = "self-declared"
    response = api.dispatch(
        _command("publish_plan", "req-plan-1", parameters), _session()
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is True
    assert response.result["confirmation_id"] != "self-declared"
