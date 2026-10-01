"""`prepare_run` 参数适配层的合同测试：经**统一入口 + 真实文件底座**跑准备链路。

测的是"参数适配"这一层，不是 `prepare_run` 本身（后者已有
`tests/unit/test_prepare_run.py` 覆盖）。因此这里的重点是：

1. **键名逐字一致**：`Command.parameters` 的键名与 `prepare_run.PreparationInputs`
   完全同名，不做别名、不补默认值；
2. **输入取自真实工厂**：用 `tests/support/prepared_run_factory.build_scenario()`
   造出的一份**真准备输入**（`blocked` / `needs_reprepare` 两个场景），
   把它改走统一入口，结果与工厂直接调用的结果**在关键字段上一致**；
3. **失败要指名**：缺字段或非法字段回报 `B_INVALID_PARAMETER`，并说出是**哪一项**；
4. **幂等不重复实现**：同键异输入由 `prepare_run()` 判 `CONFLICTED`，
   经 `_guard` 变成 `B_PREPARATION_CONFLICT`。

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
from pydantic import BaseModel, JsonValue

from aitest.application.planning.prepare_run import PreparationInputs
from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.project.context import ContextGap
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.contracts.commands import Command
from aitest.contracts.prepared_run import PreparedRunStatusFact
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.b_registration import register_b_use_cases
from tests.support.prepared_run_factory import build_scenario

PREPARE_REQUEST = "req-prepare-1"
PREPARE_REQUEST_2 = "req-prepare-2"
PREPARE_BAD = "req-prepare-bad"
PREPARE_MISSING = "req-prepare-missing"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 2, 9, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class _Stack:
    unit_of_work: PortsUnitOfWork
    reader: PortsRecordReader


class _Sequences:
    """当前提交序号的**临时**来源（`AB-001` 第 8.8 节的三个方法冻结前）。

    与 `tests/unit/test_substrate_adapter.py` 同一做法：取 A 的恢复巡检公开返回的
    已提交序号，不访问 A 的存储内部文件。A 冻结 `commit_seq` / `next_commit_seq`
    之后，这里换成正式访问器即可，**用例与动作表都不用改**。
    """

    def __init__(self, root: Path) -> None:
        self._orchestrator = RecoveryOrchestrator(root, instance_id="prepare-entry-test")

    def current_commit_sequence(self) -> int:
        committed = self._orchestrator.inspect()["committed_sequences"]
        assert isinstance(committed, tuple)
        return max(committed) if committed else 0


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
    root = Path(tempfile.gettempdir()) / f"aitest-prepare-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _api(stack: _Stack) -> LocalAPI:
    api = LocalAPI(instance_id="core-prepare", workspace_id="ws-prepare", handlers={})
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
    return Session(session_id="cli-prepare", entry_kind=EntryKind.HUMAN_UI)


def _plain(value: object) -> object:
    """把准备输入里的领域/合同对象化成 JSON 可表示的值。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, ContextGap):
        # 领域缺口对象 → 合同 `GapEntry`：`kind` 进 `gap_id` 与 `kind`，
        # `subject` / `detail` / `blocking` 同名对应。
        return {
            "gap_id": value.kind,
            "kind": value.kind,
            "subject": value.subject,
            "blocking": value.blocking,
            "detail": value.detail,
        }
    if hasattr(value, "value"):  # StrEnum
        return value.value
    return value


def _parameters(inputs: PreparationInputs) -> dict[str, object]:
    """按**同名字段**把 `PreparationInputs` 摊成命令参数。

    这里刻意逐字列出字段名（而不是 `dataclasses.asdict()` + 变换），
    好让"参数形状与 `PreparationInputs` 一致"这件事在测试里**看得见**：
    少写一个必填字段，下面的用例会立刻报缺参数。
    """
    return {
        "workspace_id": inputs.workspace_id,
        "binding_id": inputs.binding_id,
        "binding_revision": inputs.binding_revision,
        "binding_form": inputs.binding_form.value,
        "client_id": inputs.client_id,
        "prepare_request_id": inputs.prepare_request_id,
        "input_revisions": {
            "project_revision": inputs.input_revisions.project_revision,
            "binding_revision": inputs.input_revisions.binding_revision,
            "snapshot_revision": inputs.input_revisions.snapshot_revision,
            "environment_revision": inputs.input_revisions.environment_revision,
            "plan_revision": inputs.input_revisions.plan_revision,
            "rules_revision": inputs.input_revisions.rules_revision,
            "template_revision": inputs.input_revisions.template_revision,
            "scope_revision": inputs.input_revisions.scope_revision,
        },
        "snapshot": _plain(inputs.snapshot),
        "selected_paths": list(inputs.selected_paths),
        "environment": _plain(inputs.environment),
        "execution_source": _plain(inputs.execution_source),
        "plan_revision": _plain(inputs.plan_revision),
        "acceptance_scope_revision": inputs.acceptance_scope_revision,
        "rule_versions": _plain(inputs.rule_versions),
        "template_versions": _plain(inputs.template_versions),
        "case_revisions": _plain(inputs.case_revisions),
        "frozen_cases": _plain(inputs.frozen_cases),
        "assertion_bases": _plain(inputs.assertion_bases),
        "context_gaps": _plain(inputs.context_gaps),
        "authorization_requirements": _plain(inputs.authorization_requirements),
        "model_outbound_policy_revision": inputs.model_outbound_policy_revision,
        "source_snippets_enabled": inputs.source_snippets_enabled,
        "exclusion_rules": list(inputs.exclusion_rules),
        "refetch_dependencies": list(inputs.refetch_dependencies),
        "git_base_commit": inputs.git_base_commit,
        "git_diff_digest": inputs.git_diff_digest,
        "plain_manifest_digest": inputs.plain_manifest_digest,
        "run_tier": inputs.run_tier.value,
        "initial_driver": inputs.initial_driver.value,
        "template_required_case_ids": list(inputs.template_required_case_ids),
        "frozen_required_case_ids": list(inputs.frozen_required_case_ids),
        "selected_case_ids": list(inputs.selected_case_ids),
        "skipped_scope": _plain(inputs.skipped_scope),
        "applicability_exclusions": _plain(inputs.applicability_exclusions),
    }


def _prepare_command(
    *,
    request_id: str,
    parameters: Mapping[str, object],
    project_id: str,
) -> Command:
    return Command(
        request_id=request_id,
        action="prepare_run",
        project_id=project_id,
        expected_revision=0,
        intent_id=f"intent-{request_id}",
        parameters=cast("dict[str, JsonValue]", dict(parameters)),
    )


def _scenario_inputs(name: str) -> PreparationInputs:
    scenario = build_scenario(name)
    assert scenario.inputs is not None, "工厂未暴露 inputs"
    return scenario.inputs


# ------------------------------------------------------------------ 成功路径


def test_prepare_run_reaches_the_entry_and_reports_the_contract_dto(
    workspace_root: Path,
) -> None:
    """一份真准备输入经统一入口跑通，返回的 DTO 用 `PreparedRun` 的合同字段。"""
    inputs = _scenario_inputs("blocked")  # blocked 场景不需要真实存储写入
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _prepare_command(
            request_id=PREPARE_REQUEST,
            parameters=_parameters(inputs),
            project_id=inputs.project_id,
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    result = response.result
    # 与 BC-001 冻结的 PreparedRun 同字段：抽查关键项与逐字一致。
    assert result["project_id"] == inputs.project_id
    assert result["binding_id"] == inputs.binding_id
    assert result["prepare_request_id"] == inputs.prepare_request_id
    assert result["status"] == PreparedRunStatusFact.BLOCKED.value
    assert result["payload_hash"]
    assert result["created_at_commit"]
    # 阻塞原因必须来自上下文缺口，不能是空列表。
    assert result["blocking_reasons"]


def test_blocked_and_unblocked_inputs_are_distinguishable(
    workspace_root: Path,
) -> None:
    """`blocked` 与 `needs_reprepare` 两个场景的输入产生**不同**的结论。"""
    api = _api(_start(workspace_root))
    blocked = _scenario_inputs("blocked")
    other = _scenario_inputs("needs_reprepare")

    first = api.dispatch(
        _prepare_command(
            request_id=PREPARE_REQUEST,
            parameters=_parameters(blocked),
            project_id=blocked.project_id,
        ),
        _session(),
    )
    assert first.error is None, first.error
    assert first.result is not None
    assert first.result["status"] == PreparedRunStatusFact.BLOCKED.value

    second = api.dispatch(
        _prepare_command(
            request_id=PREPARE_REQUEST_2,
            parameters=_parameters(other),
            project_id=other.project_id,
        ),
        _session(),
    )
    assert second.error is None, second.error
    assert second.result is not None
    assert second.result["status"] != PreparedRunStatusFact.BLOCKED.value


# ------------------------------------------------------------------ 失败路径


def test_missing_parameter_names_the_field(workspace_root: Path) -> None:
    """缺参数时报 `B_INVALID_PARAMETER`，并且**指出是哪一项**。"""
    inputs = _scenario_inputs("blocked")
    parameters = _parameters(inputs)
    del parameters["snapshot"]
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _prepare_command(
            request_id=PREPARE_MISSING,
            parameters=parameters,
            project_id=inputs.project_id,
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "snapshot" in response.error.message


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("binding_form", "not-a-form"),
        ("binding_revision", "one"),
        ("run_tier", "turbo"),
        ("selected_paths", "src/ticket"),
        ("input_revisions", "not-an-object"),
    ],
)
def test_invalid_parameters_are_rejected_by_name(
    workspace_root: Path, field: str, value: object
) -> None:
    inputs = _scenario_inputs("blocked")
    parameters = _parameters(inputs)
    parameters[field] = value
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _prepare_command(
            request_id=PREPARE_BAD,
            parameters=parameters,
            project_id=inputs.project_id,
        ),
        _session(),
    )
    assert response.error is not None, (field, response.error)
    assert response.error.code == "B_INVALID_PARAMETER"
    assert field in response.error.message


def test_nested_invalid_field_names_the_nested_path(workspace_root: Path) -> None:
    """嵌套模型的错误要能定位到**子字段**。"""
    inputs = _scenario_inputs("blocked")
    parameters = _parameters(inputs)
    revisions = parameters["input_revisions"]
    assert isinstance(revisions, dict)
    revisions["plan_revision"] = 0  # 修订必须 >= 1
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _prepare_command(
            request_id=PREPARE_BAD,
            parameters=parameters,
            project_id=inputs.project_id,
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert "input_revisions.plan_revision" in response.error.message


def test_prepare_run_is_a_write_action_so_identity_is_required() -> None:
    """`prepare_run` 是写动作：缺 `intent_id` / `expected_revision` 由 `Command` 拒绝。"""
    with pytest.raises(ValueError):
        Command(
            request_id=PREPARE_MISSING,
            action="prepare_run",
            project_id="project-x",
            parameters={},
        )
