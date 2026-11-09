"""PlanExecutionGrant 接入 authorize_step 的最小闭环。

覆盖：
- 创建许可 → prepare_execution → authorize_step(grant_id) 成功并保存 1.2；
- 许可的动作类别/凭据/环境/时间窗/状态/计划/范围不匹配时结构化拒绝；
- 同一许可重复授权幂等；
- 旧 approval_challenge 路径的命令形状不变；
- 1.2 记录能被 execute_step 的授权就绪检查读取。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from aitest.application.approval_service import _digest
from aitest.application.errors import CapabilityUnavailable
from aitest.application.execution.commands import ExecutionCommands
from aitest.application.execution.frozen_plan import FrozenRunPlanReader
from aitest.application.execution.plan_grant import (
    GRANT_AGGREGATE_KIND,
    USAGE_AGGREGATE_KIND,
    USAGE_SCHEMA_VERSION,
    PlanExecutionGrantService,
    PlanGrantRejected,
    SavedPlanGrant,
    grant_scope_digest,
    plan_scope_digest,
)
from aitest.application.execution.step_execution import SavedStepExecution
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.prepared_run import PreparedRun
from aitest.domain.approvals import EntryKind
from aitest.domain.planning.grants import (
    GrantRiskClass,
    GrantStatus,
    PlanExecutionGrant,
)
from aitest.domain.planning.plans import Plan
from aitest.interfaces.local.api import Session
from tests.support.memory_substrate import FixedClock, MemoryReader, MemoryUnitOfWork
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_execution_authorization_origin import FixtureActionResolver

HUMAN = Session("plan-grant-fixture", EntryKind.HUMAN_UI, True)
RELAY = Session("plan-grant-relay", EntryKind.AGENT_RELAY)


def command(
    action: str,
    *,
    parameters: dict[str, Any],
    request: str,
    intent: str,
    target: str | None = None,
    project: str = "project",
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


@dataclass
class Context:
    core: Any
    inputs: Any
    step: str
    parameters: dict[str, Any]
    action: Any
    plan: Plan
    scope_digest: str


@pytest.fixture
def context(authoritative) -> Context:
    core, inputs, _ = authoritative
    prepared_result = prepare(core, inputs).result
    assert prepared_result["status"] == "prepared", prepared_result
    prepared = PreparedRun.model_validate(prepared_result)
    frozen = FrozenRunPlanReader(core.unit_of_work.repo).read(prepared)
    registration = command(
        "register_run",
        request="plan-grant-registration",
        intent="plan-grant-registration-intent",
        target=prepared_result["prepared_run_id"],
        parameters={
            "prepared_run_id": prepared_result["prepared_run_id"],
            "record_revision": 1,
        },
        project=inputs.project_id,
    )
    registered = core.api.dispatch(registration, RELAY)
    assert registered.error is None, registered.error
    facts = ExecutionFacts.model_validate(registered.result)
    step = facts.steps[0].step_id
    core.execution_authorizations.action_resolver = FixtureActionResolver(core.unit_of_work)
    resolution = command(
        "prepare_execution",
        request="plan-grant-resolution",
        intent="plan-grant-execution-intent",
        target=step,
        parameters={"run_id": facts.run_id, "step_id": step},
        project=inputs.project_id,
    )
    resolved = core.api.dispatch(resolution, RELAY)
    assert resolved.error is None, resolved.error
    parameters = dict(resolved.result)
    _, action = core.execution_authorizations.resolver.read(
        inputs.project_id, parameters["execution_action_id"]
    )
    return Context(
        core=core,
        inputs=inputs,
        step=step,
        parameters=parameters,
        action=action,
        plan=frozen.plan,
        scope_digest=plan_scope_digest(frozen.plan, project_id=inputs.project_id),
    )


def grant_parameters(context: Context, **overrides: object) -> dict[str, object]:
    assert context.plan.record_revision is not None
    values: dict[str, object] = {
        "plan_id": context.plan.plan_id,
        "plan_revision": context.plan.record_revision,
        "understanding_revision": 1,
        "scope_digest": context.scope_digest,
        "allowed_action_classes": [context.action.attempt.side_effect_class.value],
        "credential_scope_ref": context.action.request.authorization_ref.credential_scope_ref,
        "environment_id": "env-local",
        "risk_class": "medium",
    }
    return values | overrides


def create_grant(
    context: Context,
    parameters: dict[str, object],
    *,
    request: str = "plan-grant-create",
    intent: str = "plan-grant-create-intent",
    session: Session = HUMAN,
):
    return context.core.api.dispatch(
        command(
            "create_plan_grant",
            request=request,
            intent=intent,
            parameters=parameters,
            project=context.inputs.project_id,
        ),
        session,
    )


def authorize(
    context: Context,
    grant_id: str,
    *,
    request: str = "plan-grant-authorize",
):
    parameters = {**context.parameters, "grant_id": grant_id}
    return context.core.api.dispatch(
        command(
            "authorize_step",
            request=request,
            intent=context.action.request.intent_id,
            target=context.step,
            parameters=parameters,
            project=context.inputs.project_id,
        ),
        HUMAN,
    )


def authorization_record(context: Context, identity: str) -> dict[str, Any]:
    saved = context.core.unit_of_work.repo.read(
        aggregate_kind="execution_authorization", record_id=identity, revision=1
    )
    return dict(saved.payload)


def test_plan_grant_is_saved_and_authorizes_the_exact_step(context):
    created = create_grant(context, grant_parameters(context))
    assert created.error is None, created.error
    assert set(created.result) == {"grant_id", "record_revision", "grant_scope_digest"}
    assert created.result["record_revision"] == 1

    response = authorize(context, created.result["grant_id"])
    assert response.error is None, response.error
    assert response.result["source"] == "plan_grant"
    assert response.result["grant_id"] == created.result["grant_id"]
    assert response.result["grant_scope_digest"] == created.result["grant_scope_digest"]

    identity = response.result["authorization_id"]
    record = authorization_record(context, identity)
    assert record["schema_version"] == "aitest.action-authorization/1.2"
    assert record["source"] == "plan_grant"
    assert record["grant_id"] == created.result["grant_id"]
    assert record["grant_scope_digest"] == created.result["grant_scope_digest"]
    assert record["parameters"] == {
        "execution_action_id": context.parameters["execution_action_id"],
        "record_revision": 1,
        "grant_id": created.result["grant_id"],
    }
    assert record["authorization_ref"]["authorization_id"] == identity
    assert record["input_digest"].startswith("sha256:")
    state = context.core.execution_authorizations._state(context.inputs.project_id, identity)
    assert state[1].value == "unused"
    # 计划许可路径不生成逐动作确认记录。
    confirmation_id = (
        "approval-confirm-"
        + _digest(
            [
                context.core.workspace.workspace_id,
                context.inputs.project_id,
                context.action.request.intent_id,
            ]
        )[7:]
    )
    assert context.core.unit_of_work.repo.current_revision(
        aggregate_kind="approval_confirmation", record_id=confirmation_id
    ) == 0


def test_same_grant_authorization_is_idempotent(context):
    created = create_grant(context, grant_parameters(context))
    assert created.error is None, created.error
    first = authorize(context, created.result["grant_id"])
    assert first.error is None, first.error
    before = context.core.unit_of_work.current_commit_sequence()
    again = authorize(context, created.result["grant_id"], request="plan-grant-authorize-again")
    assert again.error is None, again.error
    assert again.result == first.result
    assert context.core.unit_of_work.current_commit_sequence() == before


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"allowed_action_classes": ["idempotent_write"]}, "action_class"),
        ({"credential_scope_ref": "credential-other"}, "credential"),
        ({"environment_id": "env-other"}, "environment"),
    ],
)
def test_mismatched_grant_is_structurally_rejected(context, overrides, reason):
    created = create_grant(
        context,
        grant_parameters(context, **overrides),
        request="plan-grant-mismatch",
        intent="plan-grant-mismatch-intent",
    )
    assert created.error is None, created.error
    response = authorize(context, created.result["grant_id"], request="plan-grant-mismatch-auth")
    assert response.error is not None
    assert response.error.code == "PLAN_GRANT_REJECTED"
    assert reason in response.error.message


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"plan_revision": 2}, "plan"),
        ({"scope_digest": "sha256:" + "b" * 64}, "scope"),
    ],
)
def test_create_rejects_plan_or_scope_mismatch(context, overrides, reason):
    response = create_grant(
        context,
        grant_parameters(context, **overrides),
        request="plan-grant-create-mismatch",
        intent="plan-grant-create-mismatch-intent",
    )
    assert response.error is not None
    assert response.error.code == "PLAN_GRANT_REJECTED"
    assert reason in response.error.message


def test_read_only_action_does_not_consume_side_effect_budget(context):
    created = create_grant(
        context,
        grant_parameters(context, max_side_effect_actions=0),
        request="plan-grant-budget-read-only",
        intent="plan-grant-budget-read-only-intent",
    )
    assert created.error is None, created.error
    response = authorize(
        context, created.result["grant_id"], request="plan-grant-budget-read-only-auth"
    )
    assert response.error is None, response.error


def test_side_effect_budget_rejects_exhausted_grant():
    project = "project-budget"
    grant = PlanExecutionGrant(
        grant_id="grant-budget",
        plan_revision=1,
        understanding_revision=1,
        scope_digest="sha256:scope",
        allowed_action_classes=frozenset({"idempotent_write"}),
        credential_scope_ref="credential",
        environment_id="env",
        risk_class=GrantRiskClass.MEDIUM,
        max_side_effect_actions=0,
        max_data_mutation=None,
        allowed_hosts=frozenset(),
        time_window_start=None,
        time_window_end=None,
        budget=None,
        status=GrantStatus.ACTIVE,
        created_at=datetime.now(UTC),
        revoked_at=None,
    )
    saved = SavedPlanGrant(
        grant=grant,
        grant_scope_digest=grant_scope_digest(
            grant.plan_revision,
            grant.understanding_revision,
            grant.scope_digest,
            grant.allowed_action_classes,
            grant.credential_scope_ref,
            grant.environment_id,
        ),
        record_revision=1,
        plan_id="plan-budget",
    )
    unit = MemoryUnitOfWork()
    unit.open(project)
    service = PlanExecutionGrantService(
        unit=unit,
        records=MemoryReader(unit.store),
        workspace_id="workspace-budget",
        actors=Mock(),
        clock=FixedClock(),
    )

    with pytest.raises(PlanGrantRejected, match="side-effect budget"):
        service.stage_side_effect_usage(project_id=project, saved=saved)


def _data_mutation_grant(
    *, max_data_mutation: int, max_side_effect_actions: int | None = None
) -> SavedPlanGrant:
    grant = PlanExecutionGrant(
        grant_id="grant-data-budget",
        plan_revision=1,
        understanding_revision=1,
        scope_digest="sha256:scope",
        allowed_action_classes=frozenset({"idempotent_write"}),
        credential_scope_ref="credential",
        environment_id="env",
        risk_class=GrantRiskClass.MEDIUM,
        max_side_effect_actions=max_side_effect_actions,
        max_data_mutation=max_data_mutation,
        allowed_hosts=frozenset(),
        time_window_start=None,
        time_window_end=None,
        budget=None,
        status=GrantStatus.ACTIVE,
        created_at=datetime.now(UTC),
        revoked_at=None,
    )
    return SavedPlanGrant(
        grant=grant,
        grant_scope_digest=grant_scope_digest(
            grant.plan_revision,
            grant.understanding_revision,
            grant.scope_digest,
            grant.allowed_action_classes,
            grant.credential_scope_ref,
            grant.environment_id,
        ),
        record_revision=1,
        plan_id="plan-data-budget",
    )


def test_data_mutation_budget_rejects_exhausted_grant():
    project = "project-data-budget"
    unit = MemoryUnitOfWork()
    unit.open(project)
    service = PlanExecutionGrantService(
        unit=unit,
        records=MemoryReader(unit.store),
        workspace_id="workspace-data-budget",
        actors=Mock(),
        clock=FixedClock(),
    )
    with pytest.raises(PlanGrantRejected, match="data-mutation budget"):
        service.stage_action_usage(
            project_id=project,
            saved=_data_mutation_grant(max_data_mutation=0),
            side_effect_delta=1,
            data_mutation_delta=1,
        )


def test_legacy_usage_counts_previous_side_effects_as_data_mutations():
    project = "project-data-budget-legacy"
    unit = MemoryUnitOfWork()
    unit.open(project)
    grant_id = "grant-data-budget"
    unit.stage_record(
        aggregate_kind=USAGE_AGGREGATE_KIND,
        record_id=grant_id,
        expected_revision=0,
        payload={
            "schema_version": USAGE_SCHEMA_VERSION,
            "workspace_id": "workspace-data-budget",
            "project_id": project,
            "grant_id": grant_id,
            "side_effect_actions": 1,
        },
    )
    unit.commit()
    service = PlanExecutionGrantService(
        unit=unit,
        records=MemoryReader(unit.store),
        workspace_id="workspace-data-budget",
        actors=Mock(),
        clock=FixedClock(),
    )
    with pytest.raises(PlanGrantRejected, match="data-mutation budget"):
        service.stage_action_usage(
            project_id=project,
            saved=_data_mutation_grant(max_data_mutation=1),
            data_mutation_delta=1,
        )


def test_expired_time_window_is_rejected(context):
    past = datetime.now(UTC) - timedelta(hours=2)
    created = create_grant(
        context,
        grant_parameters(
            context,
            time_window_start=past.isoformat(),
            time_window_end=(past + timedelta(hours=1)).isoformat(),
        ),
        request="plan-grant-expired",
        intent="plan-grant-expired-intent",
    )
    assert created.error is None, created.error
    response = authorize(context, created.result["grant_id"], request="plan-grant-expired-auth")
    assert response.error is not None
    assert response.error.code == "PLAN_GRANT_REJECTED"
    assert "expired" in response.error.message


def test_revoked_grant_record_is_rejected(context):
    now = datetime.now(UTC)
    grant_id = "plan-grant-revoked-fixture"
    grant = PlanExecutionGrant(
        grant_id=grant_id,
        plan_revision=grant_parameters(context)["plan_revision"],
        understanding_revision=1,
        scope_digest=context.scope_digest,
        allowed_action_classes=frozenset({context.action.attempt.side_effect_class.value}),
        credential_scope_ref=context.action.request.authorization_ref.credential_scope_ref,
        environment_id="env-local",
        risk_class=GrantRiskClass.MEDIUM,
        max_side_effect_actions=None,
        max_data_mutation=None,
        allowed_hosts=frozenset(),
        time_window_start=None,
        time_window_end=None,
        budget=None,
        status=GrantStatus.REVOKED,
        created_at=now,
        revoked_at=now,
    )
    service = context.core.execution_authorizations.plan_grants
    assert service is not None
    from aitest.domain.planning.grants import grant_scope_digest

    digest_value = grant_scope_digest(
        grant.plan_revision,
        grant.understanding_revision,
        grant.scope_digest,
        grant.allowed_action_classes,
        grant.credential_scope_ref,
        grant.environment_id,
    )
    payload = service._record_payload(
        context.inputs.project_id, context.plan.plan_id, grant, digest_value
    )
    unit = context.core.unit_of_work
    unit.begin("plan-grant-revoked-fixture", context.inputs.project_id)
    unit.stage_record(
        aggregate_kind=GRANT_AGGREGATE_KIND,
        record_id=grant_id,
        expected_revision=0,
        payload=payload,
    )
    unit.commit("plan-grant-revoked-fixture")

    response = authorize(context, grant_id, request="plan-grant-revoked-auth")
    assert response.error is not None
    assert response.error.code == "PLAN_GRANT_REJECTED"
    assert "revoked" in response.error.message


def test_create_plan_grant_rejects_agent_relay(context):
    before = context.core.unit_of_work.current_commit_sequence()
    response = create_grant(context, grant_parameters(context), session=RELAY)
    assert response.error is not None
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert context.core.unit_of_work.current_commit_sequence() == before


def test_create_plan_grant_is_idempotent_and_rejects_changed_input(context):
    parameters = grant_parameters(context)
    first = create_grant(context, parameters)
    assert first.error is None, first.error
    before = context.core.unit_of_work.current_commit_sequence()
    again = create_grant(context, parameters, request="plan-grant-create-again")
    assert again.error is None, again.error
    assert again.result == first.result
    assert context.core.unit_of_work.current_commit_sequence() == before

    changed = create_grant(
        context,
        grant_parameters(context, risk_class="high"),
        request="plan-grant-create-changed",
    )
    assert changed.error is not None
    assert changed.error.code == "INTENT_CONFLICT"


def test_legacy_challenge_command_shape_is_unchanged():
    service = Mock()
    service.resolver.read.return_value = (
        {},
        SimpleNamespace(request=SimpleNamespace(step_id="step")),
    )
    service.grant.return_value = {"authorization_id": "legacy"}
    commands = ExecutionCommands(service)
    result = commands.grant(
        command(
            "authorize_step",
            request="legacy-challenge",
            intent="legacy-intent",
            target="step",
            parameters={
                "execution_action_id": "action",
                "record_revision": 1,
                "approval_challenge_id": "challenge",
            },
        )
    )
    assert result == {"authorization_id": "legacy"}
    service.grant.assert_called_once_with(
        project_id="project",
        intent_id="legacy-intent",
        request_id="legacy-challenge",
        parameters={"execution_action_id": "action", "record_revision": 1},
        challenge_id="challenge",
    )


def test_execute_step_with_grant_auto_authorizes(context):
    created = create_grant(context, grant_parameters(context))
    assert created.error is None, created.error
    execution = SavedStepExecution(
        context.core.execution_authorizations,
        context.core.execution_coordinator,
        Mock(),
        None,
    )
    commands = ExecutionCommands(
        context.core.execution_authorizations,
        execution=execution,
    )
    execute = command(
        "execute_step",
        request="plan-grant-execute",
        intent=context.action.request.intent_id,
        target=context.step,
        parameters={**context.parameters, "grant_id": created.result["grant_id"]},
        project=context.inputs.project_id,
    )

    with pytest.raises(CapabilityUnavailable):
        commands.execute(execute)

    identity = context.action.request.authorization_ref.authorization_id
    record = authorization_record(context, identity)
    assert record["schema_version"] == "aitest.action-authorization/1.2"
    assert record["source"] == "plan_grant"
    assert record["grant_id"] == created.result["grant_id"]


def test_1_2_record_is_accepted_by_execute_readiness(context):
    created = create_grant(context, grant_parameters(context))
    assert created.error is None, created.error
    response = authorize(context, created.result["grant_id"], request="plan-grant-execute-auth")
    assert response.error is None, response.error
    identity = response.result["authorization_id"]

    # execute_step 的第一道检查就是 _origin；1.2 必须能通过它。
    record, action = context.core.execution_authorizations._origin(
        context.inputs.project_id, identity
    )
    assert record["schema_version"] == "aitest.action-authorization/1.2"
    assert action.request.step_id == context.step

    execution = SavedStepExecution(
        context.core.execution_authorizations,
        context.core.execution_coordinator,
        Mock(),
        None,
    )
    with pytest.raises(CapabilityUnavailable):
        execution.execute(
            project_id=context.inputs.project_id,
            intent_id=context.action.request.intent_id,
            action_id=context.parameters["execution_action_id"],
            step_id=context.step,
        )
