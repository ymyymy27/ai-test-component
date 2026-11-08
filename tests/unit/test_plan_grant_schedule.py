"""计划驱动自动使用 `PlanExecutionGrant`：绑定、准备、调度与旧路径兼容。

覆盖：

- `create_plan_grant` 额外落一条 `plan_grant_binding`，同一计划/意图幂等、改输入冲突；
- 准备时把计划的 `grant_id` 写进 `PreparedRun.plan_grant_id`，没有绑定则为 `None`；
- 已绑定许可的计划驱动 Run 在推进未授权步骤前自动生成 1.2 动作授权，不产生
  `approval_challenge` / `ApprovalConfirmation`；
- 许可过期或类别/凭据/环境不匹配时调度停止并返回结构化阻塞，不执行；
- 没有许可绑定的旧 Run 仍按已有逐动作授权推进（1.1）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from aitest.application.approval_service import _digest
from aitest.application.execution.plan_grant import (
    PLAN_GRANT_BINDING_KIND,
    PLAN_GRANT_BINDING_SCHEMA_VERSION,
)
from aitest.application.planning.publish import payload_digest
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts, RunControlStateFact
from aitest.domain.approvals import EntryKind
from aitest.interfaces.local.api import Session
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_execution_authorization_origin import FixtureActionResolver
from tests.unit.test_initial_run_registration import register
from tests.unit.test_saved_run_schedule import grant_step

HUMAN = Session("plan-grant-schedule", EntryKind.HUMAN_UI, True)

#: 与 `FixtureActionResolver` 产出的固定动作一致（`tests/unit/test_serial_runner.py`）。
ACTION_CLASS = "read_only"
CREDENTIAL_SCOPE = "credential-scope-1"
ENVIRONMENT_ID = "env-local"


class Resolver(FixtureActionResolver):
    """固定动作解析器；适配版本与 `FakeExecutionPort` 一致以走真实监听路径。"""

    def resolve(self, **kwargs):
        action = super().resolve(**kwargs)
        return replace(
            action,
            attempt=replace(action.attempt, adapter_version=FakeExecutionPort.adapter_version),
        )


class ExitingPort(FakeExecutionPort):
    """自动授权产生的新尝试在测试里没有预登记：按身份补一份一次轮询即退出的规格。"""

    def start(self, request):
        if self._specs.get(request.attempt_id) is None:
            self.register(
                FakeExecutionSpec(
                    request.attempt_id,
                    request.run_id,
                    request.step_id,
                    running_observations_before_exit=0,
                )
            )
        return super().start(request)


def command(
    action: str,
    *,
    parameters: dict[str, object],
    request: str,
    intent: str,
    project: str,
    target: str | None = None,
) -> Command:
    return Command(
        action=action,
        request_id=request,
        project_id=project,
        intent_id=intent,
        expected_revision=0,
        target=target,
        parameters=parameters,
    )


def dispatch_grant(core, parameters, *, project, request, intent):
    return core.api.dispatch(
        command(
            "create_plan_grant",
            parameters=parameters,
            request=request,
            intent=intent,
            project=project,
        ),
        HUMAN,
    )


def schedule_command(facts: ExecutionFacts, *, intent="schedule-intent", request="schedule"):
    return command(
        "start_run",
        project=facts.project_id,
        intent=intent,
        request=request,
        target=facts.run_id,
        parameters={
            "run_id": facts.run_id,
            "base_snapshot_commit_id": facts.snapshot_commit_id,
        },
    )


def plan_identity(core, inputs):
    """读取已发布计划与其冻结范围，得到许可创建所需的准确身份。"""

    plan_id = inputs.plan_revision.revision_id
    revision = inputs.plan_revision.revision_no
    plan = core.unit_of_work.repo.read(
        aggregate_kind="plan", record_id=plan_id, revision=revision
    )
    payload = dict(plan.payload)
    scope_revision = payload["scope_revision"]
    assert type(scope_revision) is int
    scope = core.unit_of_work.repo.read(
        aggregate_kind="acceptance_scope",
        record_id=str(payload["scope_id"]),
        revision=scope_revision,
    )
    return plan_id, revision, payload_digest(dict(scope.payload))


def grant_parameters(core, inputs, **overrides: object) -> dict[str, object]:
    plan_id, revision, scope_digest = plan_identity(core, inputs)
    values: dict[str, object] = {
        "plan_id": plan_id,
        "plan_revision": revision,
        "understanding_revision": 1,
        "scope_digest": scope_digest,
        "allowed_action_classes": [ACTION_CLASS],
        "credential_scope_ref": CREDENTIAL_SCOPE,
        "environment_id": ENVIRONMENT_ID,
        "risk_class": "medium",
    }
    return values | overrides


def create_grant(core, inputs, **overrides: object):
    return dispatch_grant(
        core,
        grant_parameters(core, inputs, **overrides),
        project=inputs.project_id,
        request="plan-grant-create",
        intent="plan-grant-create-intent",
    )


def install_execution(core, port) -> Resolver:
    resolver = Resolver(core.unit_of_work)
    core.execution_authorizations.action_resolver = resolver
    core.step_execution.execution_port = port
    return resolver


def saved_authorization(core, identity: str) -> dict[str, object]:
    record = core.unit_of_work.repo.read(
        aggregate_kind="execution_authorization", record_id=identity, revision=1
    )
    return dict(record.payload)


def confirmation_id(core, project: str, intent: str) -> str:
    return (
        "approval-confirm-"
        + _digest([core.workspace.workspace_id, project, intent])[7:]
    )


def authorization_id(core, project: str, intent: str) -> str:
    return (
        "execution-grant-"
        + _digest([core.workspace.workspace_id, project, intent])[7:]
    )


def test_plan_grant_binding_is_saved_and_preparation_finds_it(authoritative):
    core, inputs, _ = authoritative
    project = inputs.project_id
    parameters = grant_parameters(core, inputs)

    created = dispatch_grant(
        core,
        parameters,
        project=project,
        request="plan-grant-create",
        intent="plan-grant-create-intent",
    )
    assert created.error is None, created.error
    assert set(created.result) == {"grant_id", "record_revision", "grant_scope_digest"}

    binding = core.unit_of_work.repo.read(
        aggregate_kind=PLAN_GRANT_BINDING_KIND,
        record_id=parameters["plan_id"],
        revision=1,
    )
    assert dict(binding.payload) == {
        "schema_version": PLAN_GRANT_BINDING_SCHEMA_VERSION,
        "workspace_id": core.workspace.workspace_id,
        "project_id": project,
        "plan_id": parameters["plan_id"],
        "grant_id": created.result["grant_id"],
        "plan_revision": parameters["plan_revision"],
        "grant_scope_digest": created.result["grant_scope_digest"],
    }

    committed = core.unit_of_work.current_commit_sequence()
    again = dispatch_grant(
        core,
        parameters,
        project=project,
        request="plan-grant-create-again",
        intent="plan-grant-create-intent",
    )
    assert again.error is None, again.error
    assert again.result == created.result
    assert core.unit_of_work.current_commit_sequence() == committed

    # 同一计划不能静默改绑另一份许可：换意图即换许可身份，必须显式冲突。
    different = dispatch_grant(
        core,
        grant_parameters(core, inputs, risk_class="high"),
        project=project,
        request="plan-grant-create-other",
        intent="plan-grant-create-other-intent",
    )
    assert different.error is not None
    assert different.error.code == "INTENT_CONFLICT"

    prepared = prepare(core, inputs).result
    assert prepared["status"] == "prepared", prepared
    assert prepared["plan_grant_id"] == created.result["grant_id"]


def test_planned_schedule_auto_authorizes_every_step_and_repeats_idempotently(authoritative):
    core, inputs, _ = authoritative
    project = inputs.project_id
    created = create_grant(core, inputs)
    assert created.error is None, created.error
    grant_id = created.result["grant_id"]

    prepared = prepare(core, inputs).result
    assert prepared["plan_grant_id"] == grant_id
    facts = register(core, prepared)
    port = ExitingPort()
    resolver = install_execution(core, port)

    result = core.api.dispatch(schedule_command(facts), RELAY)
    assert result.error is None, result.error
    assert result.result["status"] == "execution_completed", result.result
    assert result.result["plan_grant_block"] is None
    final = ExecutionFacts.model_validate(result.result["execution_facts"])
    assert final.run.control_state is RunControlStateFact.COMPLETED
    assert len(port.execution_order) == len(facts.steps)
    assert resolver.calls == len(facts.steps)

    for attempt in final.attempts:
        assert attempt.authorization_ref is not None
        saved = saved_authorization(core, attempt.authorization_ref)
        assert saved["schema_version"] == "aitest.action-authorization/1.2"
        assert saved["source"] == "plan_grant"
        assert saved["grant_id"] == grant_id
        assert saved["grant_scope_digest"] == created.result["grant_scope_digest"]
        assert "confirmation_id" not in saved
        assert attempt.intent_id is not None
        # 计划许可路径不生成逐动作人工确认。
        assert (
            core.unit_of_work.repo.current_revision(
                aggregate_kind="approval_confirmation",
                record_id=confirmation_id(core, project, attempt.intent_id),
            )
            == 0
        )

    committed = core.unit_of_work.current_commit_sequence()
    calls = resolver.calls
    recalled = core.api.dispatch(
        schedule_command(facts, request="schedule-recall"), RELAY
    )
    assert recalled.error is None, recalled.error
    assert recalled.result["status"] == "execution_completed"
    assert core.unit_of_work.current_commit_sequence() == committed
    assert resolver.calls == calls


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {
                "time_window_start": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                "time_window_end": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            },
            "expired",
        ),
        ({"environment_id": "env-other"}, "environment"),
    ],
)
def test_blocked_plan_grant_stops_the_schedule_without_execution(
    authoritative, overrides, reason
):
    core, inputs, _ = authoritative
    project = inputs.project_id
    created = create_grant(core, inputs, **overrides)
    assert created.error is None, created.error
    grant_id = created.result["grant_id"]

    prepared = prepare(core, inputs).result
    assert prepared["plan_grant_id"] == grant_id
    facts = register(core, prepared)
    port = FakeExecutionPort()
    install_execution(core, port)

    result = core.api.dispatch(schedule_command(facts), RELAY)
    assert result.error is None, result.error
    assert result.result["status"] == "waiting_authorization"
    block = result.result["plan_grant_block"]
    assert block is not None
    assert block["code"] == "PLAN_GRANT_REJECTED"
    assert block["reason"] == reason
    assert block["grant_id"] == grant_id
    assert block["step_id"] == facts.steps[0].step_id

    # 停止且不执行：没有调度出去的尝试，也没有 1.2 授权记录。
    assert port.execution_order == []
    current = core.execution_coordinator.read_current_facts(
        project_id=project, run_id=facts.run_id
    )
    assert current is not None and not current.attempts
    intent = core.run_schedule._plan_grant_intent(facts, facts.steps[0].step_id)
    assert (
        core.unit_of_work.repo.current_revision(
            aggregate_kind="execution_authorization",
            record_id=authorization_id(core, project, intent),
        )
        == 0
    )


def test_run_without_grant_still_advances_only_after_per_action_consent(authoritative):
    core, inputs, _ = authoritative
    project = inputs.project_id
    prepared = prepare(core, inputs).result
    assert prepared["plan_grant_id"] is None
    facts = register(core, prepared)
    port = FakeExecutionPort()
    install_execution(core, port)

    waiting = core.api.dispatch(schedule_command(facts), RELAY)
    assert waiting.error is None, waiting.error
    assert waiting.result["status"] == "waiting_authorization"
    assert waiting.result["plan_grant_block"] is None
    assert port.execution_order == []

    action = grant_step(core, facts, facts.steps[0], intent="run-step-0")
    original = core.execution_authorizations._origin(
        project, action.request.authorization_ref.authorization_id
    )[0]
    assert original["schema_version"] == "aitest.action-authorization/1.1"
    assert "source" not in original

    advanced = core.api.dispatch(
        schedule_command(facts, request="schedule-after-consent"), RELAY
    )
    assert advanced.error is None, advanced.error
    assert port.execution_order == [action.attempt.attempt_id]
    assert advanced.result["plan_grant_block"] is None
    assert advanced.result["status"] in {"waiting_authorization", "execution_completed"}
