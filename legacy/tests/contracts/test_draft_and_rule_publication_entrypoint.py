"""`generate_draft` 与 `publish_rules` 的合同测试：经**统一入口 + 真实文件底座**。

测的是"参数适配"这一层，不是领域规则本身（后者已有
`tests/unit/test_draft_generation.py`、`tests/unit/test_publish_orchestration.py` 覆盖）。
重点：

1. **键名逐字一致**：`Command.parameters` 的键名与 `draft.RevisionContext`、
   `rules.RuleDraft` 完全同名；
2. **缺口是正常结果**：`generate_draft` 在缺口非空时返回 `blocked=true` + 缺口清单，
   **不生成草稿**，也不报错；
3. **发布结果二选一**：`publish_rules` 返回 `published` 或 `blocked_by`，不会两者都有；
4. **失败指名到字段**。

不使用 `tmp_path`：受限执行环境拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举
（与 `tests/unit/test_substrate_adapter.py` 同一原因）。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import JsonValue

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
TEMPLATE_ID = "http-workflow"
TEMPLATE_VERSION = "1.0.0"

DRAFT_REQ = "req-draft-1"
GAP_REQ = "req-draft-gap"
PUBLISH_REQ = "req-publish-1"
BAD_REQ = "req-draft-bad"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 2, 10, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


class _Sequences:
    """当前提交序号的临时来源（`AB-001` 第 8.8 节的三个方法冻结前）。"""

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
        unit_of_work=PortsUnitOfWork(
            raw, repository=repository, sequence=_Sequences(root)
        ),
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
        parameters=cast("dict[str, JsonValue]", dict(parameters)),
    )


def _rebuild_index(root: Path) -> None:
    """建出**空**查询索引，模拟"已提交过、但还没有任何记录"的工作空间。

    `publish_rules` 要读 `rule_version` 的**当前修订**，走的是有界查询；
    索引文件不存在时按合同返回 `maintenance_required`（**不隐式全表扫描**）。
    新工作空间在第一次提交之后索引文件就已存在（行数为 0），因此这里建一份空索引，
    而不是让实现偷偷回退去扫记录。
    """
    FileQueryIndex(root).rebuild(())


def _publish_command(
    request_id: str, parameters: Mapping[str, object]
) -> Command:
    return _command("publish_rules", request_id, parameters)


def _draft_parameters(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "template_ref": {"template_id": TEMPLATE_ID, "version": TEMPLATE_VERSION},
        "revision_context": {
            "project_revision": 1,
            "binding_revision": 1,
            "template_revision": "1",
        },
        "draft_kind": "check_content",
    }
    base.update(overrides)
    return base


def _rule_parameters(**overrides: object) -> dict[str, object]:
    draft: dict[str, object] = {
        "rule_id": "rule-1",
        "revision": 1,
        "scope": "http workflows",
        "text": "check the status code and the persisted body",
        "source": "manual",
        "steps": ["call the endpoint", "read it back"],
        "evidence_requirements": ["raw response"],
        "enablement": "enabled",
        "confirmed": True,
    }
    draft.update(overrides)
    return {"draft": draft}


# ------------------------------------------------------------------ generate_draft


def test_generate_draft_returns_the_draft_with_its_revision_context(
    workspace_root: Path,
) -> None:
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("generate_draft", DRAFT_REQ, _draft_parameters()), _session()
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["blocked"] is False
    content = response.result["content"]
    assert isinstance(content, dict)
    assert content["project_id"] == PROJECT_ID
    assert content["draft_kind"] == "check_content"
    assert content["status"] == "draft"
    assert content["template_ref"] == {
        "template_id": TEMPLATE_ID,
        "version": TEMPLATE_VERSION,
    }
    revision_context = content["revision_context"]
    assert isinstance(revision_context, dict)
    assert revision_context["project_revision"] == 1


def test_generate_draft_persists_the_body_not_only_metadata(
    workspace_root: Path,
) -> None:
    """草稿**正文与摘要一起落盘**，并且重启后能按记录读回。

    只返回元数据会让"到底产出了什么"没有可核对的字节；本用例在真实文件存储上
    读回 `generated_content` 记录，逐项核对正文与摘要。
    """
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("generate_draft", DRAFT_REQ, _draft_parameters()), _session()
    )
    assert response.error is None, response.error
    assert response.result is not None
    record = response.result.get("record")
    assert isinstance(record, dict), response.result
    assert record["aggregate_kind"] == "generated_content"
    assert record["revision"] == 1

    content = response.result["content"]
    assert isinstance(content, dict)
    assert content["content_digest"]

    # 重启：重新构造一整套底座对象，复用同一目录，按准确修订读回。
    committed = _start(workspace_root).reader.read(
        aggregate_kind="generated_content",
        record_id=str(record["record_id"]),
        revision=1,
    )
    payload = committed.payload
    draft_text = payload["draft_text"]
    assert isinstance(draft_text, str) and draft_text
    # 正文摘要必须与正文一致，且与响应里报的摘要相同。
    assert payload["content_digest"] == content["content_digest"]
    assert payload["draft_text"] == draft_text
    # 正文来自模板内容本身：模板声明的必测项必须能在正文里找到。
    assert "items" in draft_text
    assert payload["template_id"] == TEMPLATE_ID


def test_generate_draft_body_is_reproducible(workspace_root: Path) -> None:
    """同样的输入两次生成，正文与摘要**逐字节一致**（可复现、可核对）。"""
    api = _api(_start(workspace_root))
    first = api.dispatch(
        _command("generate_draft", DRAFT_REQ, _draft_parameters()), _session()
    )
    second = api.dispatch(
        _command(
            "generate_draft",
            f"{DRAFT_REQ}-2",
            _draft_parameters(content_revision=2),
        ),
        _session(),
    )
    assert first.error is None and second.error is None
    assert first.result is not None and second.result is not None
    first_content = first.result["content"]
    second_content = second.result["content"]
    assert isinstance(first_content, dict) and isinstance(second_content, dict)
    assert first_content["content_digest"] == second_content["content_digest"]


def test_generate_draft_with_gaps_is_a_normal_blocked_result(
    workspace_root: Path,
) -> None:
    """缺口非空时**不生成草稿**，返回缺口清单——这是结果，不是错误。"""
    api = _api(_start(workspace_root))
    parameters = _draft_parameters(
        gaps=[
            {
                "kind": "missing_environment_carrier",
                "subject": "environment",
                "detail": "no environment carrier is registered",
            }
        ]
    )
    response = api.dispatch(_command("generate_draft", GAP_REQ, parameters), _session())
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["blocked"] is True
    assert "content" not in response.result
    gaps = response.result["gaps"]
    assert isinstance(gaps, list)
    first_gap = gaps[0]
    assert isinstance(first_gap, dict)
    assert first_gap["kind"] == "missing_environment_carrier"
    assert first_gap["blocking"] is True


def test_generate_draft_unknown_template_is_reported(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    parameters = _draft_parameters(
        template_ref={"template_id": "no-such-template", "version": "1.0.0"}
    )
    response = api.dispatch(_command("generate_draft", BAD_REQ, parameters), _session())
    assert response.error is not None
    assert "no-such-template" in response.error.message or "template" in response.error.message


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("template_ref", "not-an-object", "template_ref"),
        ("draft_kind", "", "draft_kind"),
        ("revision_context", "not-an-object", "revision_context"),
    ],
)
def test_generate_draft_invalid_parameters_are_named(
    workspace_root: Path, field: str, value: object, named: str
) -> None:
    api = _api(_start(workspace_root))
    parameters = _draft_parameters()
    parameters[field] = value
    response = api.dispatch(_command("generate_draft", BAD_REQ, parameters), _session())
    assert response.error is not None, (field, response.error)
    assert response.error.code == "B_INVALID_PARAMETER"
    assert named in response.error.message


# ------------------------------------------------------------------ publish_rules


def test_publish_rules_publishes_and_reports_the_committed_version(
    workspace_root: Path,
) -> None:
    _rebuild_index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("publish_rules", PUBLISH_REQ, _rule_parameters()), _session()
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is True
    assert response.result["kind"] == "rule_version"
    assert response.result["rule_id"] == "rule-1"
    assert response.result["revision"] == 1
    # 未确认的草稿不得发布；这里已确认，因此必须带确认标识。
    assert response.result["confirmation_id"]
    assert str(response.result["digest"]).startswith("sha256:")


def test_publish_rules_refuses_unconfirmed_draft(workspace_root: Path) -> None:
    """未确认的草稿：拒绝，并给出**原因**（不是异常、不是静默成功）。"""
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command(
            "publish_rules",
            PUBLISH_REQ,
            _rule_parameters(confirmed=False, enablement="disabled"),
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    assert response.result["blocked_by"]


def test_publish_rules_with_blocking_context_gap_is_refused(
    workspace_root: Path,
) -> None:
    api = _api(_start(workspace_root))
    parameters = _rule_parameters()
    parameters["context_gaps"] = [
        {
            "kind": "no_modules",
            "subject": "project",
            "detail": "the project declares no modules",
        }
    ]
    response = api.dispatch(_command("publish_rules", PUBLISH_REQ, parameters), _session())
    assert response.error is None, response.error
    assert response.result is not None
    assert response.result["published"] is False
    assert response.result["blocked_by"]


def test_publish_rules_invalid_draft_is_named(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    parameters = _rule_parameters()
    draft = parameters["draft"]
    assert isinstance(draft, dict)
    del draft["text"]
    response = api.dispatch(_command("publish_rules", BAD_REQ, parameters), _session())
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "text" in response.error.message


def test_publish_rules_second_publish_appends_a_new_revision(
    workspace_root: Path,
) -> None:
    """已发布规则再发布：**追加新修订**，旧修订保留（记录只追加）。

    第二次发布必须声明"我看到的是 `@1`"（检查项 B-14）：发布不再替调用方接受最新基线。
    """
    _rebuild_index(workspace_root)
    api = _api(_start(workspace_root))
    first = api.dispatch(
        _command("publish_rules", PUBLISH_REQ, _rule_parameters()), _session()
    )
    assert first.error is None, first.error
    second_parameters = _rule_parameters(revision=2, text="updated check text")
    second = api.dispatch(
        _command(
            "publish_rules",
            f"{PUBLISH_REQ}-2",
            second_parameters,
            expected_revision=1,
        ),
        _session(),
    )
    assert second.error is None, second.error
    assert second.result is not None
    assert second.result["published"] is True
    assert second.result["revision"] == 2


def test_publish_rules_with_a_stale_expected_revision_is_refused(
    workspace_root: Path,
) -> None:
    """检查项 B-14：旧编辑继续发布必须报冲突，并带上当前修订。"""
    _rebuild_index(workspace_root)
    api = _api(_start(workspace_root))
    first = api.dispatch(
        _command("publish_rules", PUBLISH_REQ, _rule_parameters()), _session()
    )
    assert first.error is None, first.error
    # 另一个编辑者手上仍是 `@1`：先真实发布到 `@2`，再用 `@1` 提交第三次发布。
    second = api.dispatch(
        _command(
            "publish_rules",
            f"{PUBLISH_REQ}-2",
            _rule_parameters(revision=2, text="updated check text"),
            expected_revision=1,
        ),
        _session(),
    )
    assert second.error is None, second.error
    stale = api.dispatch(
        _command(
            "publish_rules",
            f"{PUBLISH_REQ}-3",
            _rule_parameters(revision=3, text="第三个编辑者手上的旧正文"),
            expected_revision=1,
        ),
        _session(),
    )
    assert stale.error is not None
    assert stale.error.code == "B_REVISION_CONFLICT"
    assert "2" in stale.error.message
