"""PlanExecutionGrant 撤销与同计划重绑语义（设计 7.3、12.3 EXEC-10；P1-AC31）。

覆盖：

- 创建许可 → 撤销 → 后续授权按 ``PLAN_GRANT_REJECTED/revoked`` 拒绝；
- 撤销落独立不可变记录，原许可记录字节保持 ``active``；
- 同一许可重传同一原因幂等，换原因按意图冲突；
- 撤销后同一计划可创建新许可并落绑定新修订；新准备读到新 ``grant_id``，
  旧 ``PreparedRun`` 仍保留它冻结时的旧 ``grant_id``；
- 许可未撤销时同一计划新建许可仍冲突；
- agent_relay 不能撤销。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from aitest.application.execution.plan_grant import (
    GRANT_AGGREGATE_KIND,
    PLAN_GRANT_BINDING_KIND,
    REVOCATION_AGGREGATE_KIND,
    REVOCATION_SCHEMA_VERSION,
    read_plan_grant_binding,
)
from aitest.domain.planning.grants import GrantStatus
from aitest.interfaces.local.api import Session
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_plan_grant_authorization import (
    HUMAN,
    RELAY,
    Context,
    authorize,
    command,
    create_grant,
    grant_parameters,
)
from tests.unit.test_plan_grant_authorization import (
    context as context,
)


def revoke(
    core: Any,
    project: str,
    grant_id: str,
    *,
    reason: str = "user cancelled the run",
    request: str = "plan-grant-revoke",
    intent: str = "plan-grant-revoke-intent",
    session: Session = HUMAN,
):
    return core.api.dispatch(
        command(
            "revoke_plan_grant",
            request=request,
            intent=intent,
            target=grant_id,
            parameters={"grant_id": grant_id, "reason": reason},
            project=project,
        ),
        session,
    )


def test_revoked_grant_rejects_authorization_and_keeps_original_bytes(context: Context):
    created = create_grant(context, grant_parameters(context))
    assert created.error is None, created.error
    grant_id = created.result["grant_id"]

    revoked = revoke(context.core, context.inputs.project_id, grant_id)
    assert revoked.error is None, revoked.error
    assert set(revoked.result) == {
        "grant_id",
        "status",
        "reason",
        "revoked_at",
        "record_revision",
    }
    assert revoked.result["grant_id"] == grant_id
    assert revoked.result["status"] == "revoked"
    assert revoked.result["reason"] == "user cancelled the run"
    assert revoked.result["record_revision"] == 1

    # 读取叠加撤销事实：状态与撤销时刻可见，原记录修订不变。
    service = context.core.execution_authorizations.plan_grants
    assert service is not None
    saved = service.read(project_id=context.inputs.project_id, grant_id=grant_id)
    assert saved.grant.status is GrantStatus.REVOKED
    assert saved.grant.revoked_at is not None
    assert saved.record_revision == 1
    original = context.core.unit_of_work.repo.read(
        aggregate_kind=GRANT_AGGREGATE_KIND, record_id=grant_id, revision=1
    )
    assert original.payload["grant"]["status"] == "active"
    assert original.payload["grant"]["revoked_at"] is None

    rejection = authorize(context, grant_id, request="plan-grant-revoked-auth")
    assert rejection.error is not None
    assert rejection.error.code == "PLAN_GRANT_REJECTED"
    assert "revoked" in rejection.error.message


def test_revoke_is_idempotent_and_rejects_a_different_reason(context: Context):
    created = create_grant(context, grant_parameters(context))
    assert created.error is None, created.error
    grant_id = created.result["grant_id"]

    first = revoke(context.core, context.inputs.project_id, grant_id, request="revoke-first")
    assert first.error is None, first.error
    committed = context.core.unit_of_work.current_commit_sequence()

    again = revoke(context.core, context.inputs.project_id, grant_id, request="revoke-again")
    assert again.error is None, again.error
    assert again.result == first.result
    assert context.core.unit_of_work.current_commit_sequence() == committed

    changed = revoke(
        context.core,
        context.inputs.project_id,
        grant_id,
        reason="another reason",
        request="revoke-changed",
        intent="plan-grant-revoke-changed-intent",
    )
    assert changed.error is not None
    assert changed.error.code == "INTENT_CONFLICT"
    assert context.core.unit_of_work.current_commit_sequence() == committed

    record = context.core.unit_of_work.repo.read(
        aggregate_kind=REVOCATION_AGGREGATE_KIND, record_id=grant_id, revision=1
    )
    assert record.payload == {
        "schema_version": REVOCATION_SCHEMA_VERSION,
        "workspace_id": context.core.workspace.workspace_id,
        "project_id": context.inputs.project_id,
        "grant_id": grant_id,
        "reason": "user cancelled the run",
        "revoked_at": first.result["revoked_at"],
    }


def test_revoked_grant_allows_rebinding_and_new_preparation(context: Context):
    core, inputs = context.core, context.inputs
    plan_id = context.plan.plan_id
    first = create_grant(context, grant_parameters(context))
    assert first.error is None, first.error
    first_grant = first.result["grant_id"]

    # 新准备请求身份，避免命中夹具在创建许可前保存的旧准备（那份没有许可身份）。
    before = prepare(
        core,
        replace(inputs, prepare_request_id="prepare-request-before-revoke"),
        request="prepare-before-revoke",
        intent="prepare-intent-before-revoke",
    ).result
    assert before["status"] == "prepared", before
    assert before["plan_grant_id"] == first_grant

    revoked = revoke(core, inputs.project_id, first_grant, request="revoke-before-rebind")
    assert revoked.error is None, revoked.error

    replacement = create_grant(
        context,
        grant_parameters(context, risk_class="high"),
        request="plan-grant-create-replacement",
        intent="plan-grant-create-replacement-intent",
    )
    assert replacement.error is None, replacement.error
    second_grant = replacement.result["grant_id"]
    assert second_grant != first_grant

    binding = core.unit_of_work.repo.read(
        aggregate_kind=PLAN_GRANT_BINDING_KIND, record_id=plan_id, revision=2
    )
    assert binding.payload["grant_id"] == second_grant
    assert binding.payload["plan_id"] == plan_id
    latest = read_plan_grant_binding(
        reader=core.unit_of_work.repo,
        project_id=inputs.project_id,
        workspace_id=core.workspace.workspace_id,
        plan_id=plan_id,
    )
    assert latest is not None
    assert latest.grant_id == second_grant

    after = prepare(
        core,
        replace(inputs, prepare_request_id="prepare-request-after-revoke"),
        request="prepare-after-revoke",
        intent="prepare-intent-after-revoke",
    ).result
    assert after["status"] == "prepared", after
    assert after["prepared_run_id"] != before["prepared_run_id"]
    assert after["plan_grant_id"] == second_grant

    # 旧运行冻结的许可身份不回写为新的。
    old_saved = core.unit_of_work.repo.read(
        aggregate_kind="prepared_run", record_id=before["prepared_run_id"], revision=1
    )
    assert old_saved.payload["plan_grant_id"] == first_grant
    new_saved = core.unit_of_work.repo.read(
        aggregate_kind="prepared_run", record_id=after["prepared_run_id"], revision=1
    )
    assert new_saved.payload["plan_grant_id"] == second_grant


def test_same_plan_new_grant_still_conflicts_while_active(context: Context):
    created = create_grant(context, grant_parameters(context))
    assert created.error is None, created.error

    conflict = create_grant(
        context,
        grant_parameters(context, risk_class="high"),
        request="plan-grant-create-conflict",
        intent="plan-grant-create-conflict-intent",
    )
    assert conflict.error is not None
    assert conflict.error.code == "INTENT_CONFLICT"
    assert (
        context.core.unit_of_work.repo.current_revision(
            aggregate_kind=PLAN_GRANT_BINDING_KIND, record_id=context.plan.plan_id
        )
        == 1
    )


def test_agent_relay_cannot_revoke(context: Context):
    created = create_grant(context, grant_parameters(context))
    assert created.error is None, created.error
    grant_id = created.result["grant_id"]
    committed = context.core.unit_of_work.current_commit_sequence()

    response = revoke(context.core, context.inputs.project_id, grant_id, session=RELAY)
    assert response.error is not None
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert context.core.unit_of_work.current_commit_sequence() == committed
    assert (
        context.core.unit_of_work.repo.current_revision(
            aggregate_kind=REVOCATION_AGGREGATE_KIND, record_id=grant_id
        )
        == 0
    )


def test_revoke_requires_an_exact_saved_grant(context: Context):
    response = revoke(context.core, context.inputs.project_id, "plan-grant-unknown")
    assert response.error is not None
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
