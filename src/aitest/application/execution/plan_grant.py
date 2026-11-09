"""计划级执行许可（``PlanExecutionGrant``）的保存、读取与动作匹配。

本模块把纯领域模型接到现有执行授权链上：

- 保存一条不可变的 ``plan_execution_grant`` 记录，返回 ``grant_id``、
  ``record_revision`` 与 ``grant_scope_digest``；
- 读取时逐字段核对记录信封、领域对象与摘要，损坏或跨工作空间一律拒绝；
- ``authorize_step`` 使用许可前，按领域函数核对状态、时间窗、动作类别、
  凭据、环境、计划/范围修订。

合同来源：设计文档 6.4/6.6/12.3（EXEC-11/12）、需求 P1-FR07/P1-AC31、
架构《02-执行与证据》第 6 节与《05-Trae接入》第 9 节。

边界：

- 本模块**不**替代 ``ActionAuthorization``；它只回答“许可覆盖不覆盖这个动作”；
- 撤销是**独立不可变记录**（``plan_grant_revocation``），原 ``plan_execution_grant``
  记录字节保持不变；读取时叠加为 ``status=revoked``，让已冻结的历史仍可按原字节审计；
- 同一计划只能绑定一份许可；只有当前绑定的许可已撤销时才允许落装配新许可的
  绑定新修订（设计 7.3「撤销许可」、EXEC-10），仍有效的许可继续按冲突处理；
- 预算、数据变更上限与允许主机随记录保存，但一期不在本模块累计或拦截
  （合同允许作为 P1 增强，字段先预留）；
- ``understanding_revision`` 目前只在计划的 ``coverage_contract`` 中可核；
  计划没有覆盖契约时没有可比的 understanding 记录，此时只按许可自身声明
  绑定（不伪造一个“当前值”），源码/环境漂移仍由既有执行来源核对处理；
- ``plan_revision`` 是**计划记录修订**（``PlanRevisionRef.revision_no``），
  与运行冻结的 ``PlanRevisionRef`` 使用同一身份，避免把内容修订和记录修订
  混成两个真值来源；
- ``environment_id`` 是环境记录身份（运行引用 ``环境@修订`` 的前缀），
  修订一致性仍由冻结准备与执行来源核对承担。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, cast

from pydantic import TypeAdapter

from aitest.application.approval_service import _digest
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import (
    acceptance_scope_to_payload,
    plan_coverage_contract_from_payload,
)
from aitest.application.planning.substrate import (
    AggregateKind,
    RecordReader,
    current_record,
)
from aitest.application.ports import (
    Clock,
    ControlledActorContext,
    RecordRepository,
    StageableWorkspaceUnitOfWork,
)
from aitest.contracts.prepared_run import PreparedRun
from aitest.domain.approvals import ApprovalConflict, ApprovalRequired, require_controlled_actor
from aitest.domain.execution.authorization import ResolvedExecutionAction
from aitest.domain.planning.grants import (
    GrantInvalidationReason,
    GrantRiskClass,
    GrantStatus,
    PlanExecutionGrant,
    grant_allows,
    grant_host_allowed,
    grant_invalidation_reason,
    grant_scope_digest,
)
from aitest.domain.planning.plans import Plan

GRANT_SCHEMA_VERSION = "aitest.plan-execution-grant/1.0"
GRANT_AGGREGATE_KIND = "plan_execution_grant"
USAGE_SCHEMA_VERSION = "aitest.plan-grant-usage/1.0"
USAGE_SCHEMA_VERSION_V11 = "aitest.plan-grant-usage/1.1"
USAGE_AGGREGATE_KIND = "plan_grant_usage"

#: 撤销事实单独不可变保存；原许可记录字节不因撤销而改写（EXEC-10）。
REVOCATION_SCHEMA_VERSION = "aitest.plan-grant-revocation/1.0"
REVOCATION_AGGREGATE_KIND = "plan_grant_revocation"

#: 按计划定位许可的绑定记录；准备阶段据此把 `grant_id` 写入 `PreparedRun`。
PLAN_GRANT_BINDING_KIND = "plan_grant_binding"
PLAN_GRANT_BINDING_SCHEMA_VERSION = "aitest.plan-grant-binding/1.0"

_REQUIRED_PARAMETERS = frozenset(
    {
        "plan_id",
        "plan_revision",
        "understanding_revision",
        "scope_digest",
        "allowed_action_classes",
        "credential_scope_ref",
        "environment_id",
        "risk_class",
    }
)
_OPTIONAL_PARAMETERS = frozenset(
    {
        "max_side_effect_actions",
        "max_data_mutation",
        "allowed_hosts",
        "time_window_start",
        "time_window_end",
        "budget",
    }
)


class PlanGrantRejected(ApprovalRequired):
    """许可存在但不能覆盖当前动作；``reason`` 是结构化原因。"""

    code = "PLAN_GRANT_REJECTED"

    def __init__(self, reason: str, message: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("a plan grant rejection requires its structured reason")
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SavedPlanGrant:
    """已保存许可的读取结果；``plan_id`` 只在创建时提供过才存在。"""

    grant: PlanExecutionGrant
    grant_scope_digest: str
    record_revision: int
    plan_id: str | None


@dataclass(frozen=True, slots=True)
class SavedPlanGrantRevocation:
    """已保存的撤销事实；撤销记录不可变，``reason`` 参与幂等核对。"""

    grant_id: str
    reason: str
    revoked_at: datetime


@dataclass(frozen=True, slots=True)
class PlanGrantBinding:
    """计划到许可的稳定绑定；准备阶段只读这个身份，不重复读许可正文。"""

    plan_id: str
    grant_id: str
    plan_revision: int
    grant_scope_digest: str


def _binding_from_payload(
    payload: Mapping[str, object], *, project_id: str, workspace_id: str, plan_id: str
) -> PlanGrantBinding:
    """逐字段核对绑定记录；未知字段或跨项目/工作空间一律拒绝。"""

    if set(payload) != {
        "schema_version",
        "workspace_id",
        "project_id",
        "plan_id",
        "grant_id",
        "plan_revision",
        "grant_scope_digest",
    }:
        raise ValueError("plan grant binding has unknown fields")
    if payload["schema_version"] != PLAN_GRANT_BINDING_SCHEMA_VERSION:
        raise ValueError("plan grant binding has an unknown schema")
    if payload["workspace_id"] != workspace_id or payload["project_id"] != project_id:
        raise ValueError("plan grant binding belongs to another workspace or project")
    if payload["plan_id"] != plan_id:
        raise ValueError("plan grant binding plan identity differs from its record")
    grant_id = payload["grant_id"]
    plan_revision = payload["plan_revision"]
    digest_value = payload["grant_scope_digest"]
    if not isinstance(grant_id, str) or not grant_id.strip():
        raise ValueError("plan grant binding grant identity cannot be verified")
    if type(plan_revision) is not int or plan_revision < 1:
        raise ValueError("plan grant binding plan revision cannot be verified")
    if not isinstance(digest_value, str) or not digest_value.strip():
        raise ValueError("plan grant binding scope digest cannot be verified")
    return PlanGrantBinding(
        plan_id=plan_id,
        grant_id=grant_id,
        plan_revision=plan_revision,
        grant_scope_digest=digest_value,
    )


def _revocation_from_payload(
    payload: Mapping[str, object],
    *,
    project_id: str,
    workspace_id: str,
    grant_id: str,
) -> SavedPlanGrantRevocation:
    """逐字段核对撤销记录；未知字段、跨项目/工作空间或身份不符一律拒绝。"""

    if set(payload) != {
        "schema_version",
        "workspace_id",
        "project_id",
        "grant_id",
        "reason",
        "revoked_at",
    }:
        raise ValueError("plan grant revocation has unknown fields")
    if payload["schema_version"] != REVOCATION_SCHEMA_VERSION:
        raise ValueError("plan grant revocation has an unknown schema")
    if payload["workspace_id"] != workspace_id or payload["project_id"] != project_id:
        raise ValueError("plan grant revocation belongs to another workspace or project")
    if payload["grant_id"] != grant_id:
        raise ValueError("plan grant revocation grant identity differs from its record")
    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("plan grant revocation reason cannot be verified")
    revoked_at = _optional_datetime(payload["revoked_at"], "revoked_at")
    if revoked_at is None:
        raise ValueError("plan grant revocation requires revoked_at")
    return SavedPlanGrantRevocation(grant_id=grant_id, reason=reason, revoked_at=revoked_at)


def read_plan_grant_binding(
    *, reader: RecordReader, project_id: str, workspace_id: str, plan_id: str
) -> PlanGrantBinding | None:
    """按计划读取**最新修订**的许可绑定；没有绑定返回 ``None``。

    准备阶段用它决定是否把 ``plan_grant_id`` 写进 ``PreparedRun``。同一计划
    在许可被撤销后允许重新绑定，因此这里必须读当前修订而不是固定第 1 修订；
    已冻结的旧 ``PreparedRun`` 仍保留它当时的 ``plan_grant_id``，不回写。
    读取失败或形状不符时抛 ``ValueError``：调用方必须显式决定“降级为旧逐动作
    路径”还是阻塞，不能把损坏记录当成有效许可。
    """

    if any(
        not isinstance(value, str) or not value.strip()
        for value in (project_id, workspace_id, plan_id)
    ):
        raise ValueError("plan grant binding lookup requires exact project, workspace and plan")
    record = current_record(
        reader,
        project_id=project_id,
        aggregate_kind=cast(AggregateKind, PLAN_GRANT_BINDING_KIND),
        record_id=plan_id,
    )
    if record is None:
        return None
    if (
        (record.aggregate_kind, record.record_id) != (PLAN_GRANT_BINDING_KIND, plan_id)
        or type(record.revision) is not int
        or record.revision < 1
        or not isinstance(record.payload, Mapping)
    ):
        raise ValueError("plan grant binding envelope cannot be verified")
    return _binding_from_payload(
        record.payload, project_id=project_id, workspace_id=workspace_id, plan_id=plan_id
    )


def plan_scope_digest(plan: Plan, *, project_id: str) -> str:
    """冻结范围的规范摘要；创建许可与授权核对使用同一口径。"""

    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    return payload_digest(acceptance_scope_to_payload(plan.scope, project_id=project_id))


def _environment_id(environment_ref: str) -> str:
    """``环境记录@修订`` 的执行引用取其记录身份；修订由冻结准备核对。"""

    return environment_ref.split("@", 1)[0]


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be an integer >= 1")
    return value


def _optional_non_negative_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be an integer >= 0")
    return value


def _text_sequence(value: object, name: str, *, required: bool) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence of non-empty strings")
    items = tuple(value)
    if required and not items:
        raise ValueError(f"{name} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{name} must contain only non-empty strings")
    return items


def _optional_datetime(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 datetime string") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a UTC offset")
    return parsed


_GRANT_SCOPE_REQUIRED = frozenset(
    {"allowed_action_classes", "credential_scope_ref", "environment_id", "risk_class"}
)
_GRANT_SCOPE_OPTIONAL = frozenset(
    {
        "max_side_effect_actions",
        "max_data_mutation",
        "allowed_hosts",
        "time_window_start",
        "time_window_end",
        "budget",
        "understanding_revision",
    }
)


def normalize_grant_scope(
    values: Mapping[str, object], *, require_understanding: bool
) -> dict[str, object]:
    """把计划确认里冻结的许可范围规范成可复算摘要的形状。

    计划确认挑战的输入摘要覆盖这一段；因此必须在确认前把集合排序、时间规范成
    ISO-8601，并拒绝未知字段。``plan_id`` / ``plan_revision`` / ``scope_digest``
    由服务端从已发布计划派生，调用方不得自报。
    """

    if not isinstance(values, Mapping):
        raise ValueError("plan grant scope must be an object")
    raw = dict(values)
    missing = _GRANT_SCOPE_REQUIRED - set(raw)
    unknown = set(raw) - _GRANT_SCOPE_REQUIRED - _GRANT_SCOPE_OPTIONAL
    if missing or unknown:
        raise ValueError(
            f"plan grant scope differs: missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    result: dict[str, object] = {
        "allowed_action_classes": sorted(
            set(
                _text_sequence(
                    raw["allowed_action_classes"], "allowed_action_classes", required=True
                )
            )
        ),
        "credential_scope_ref": _text(raw["credential_scope_ref"], "credential_scope_ref"),
        "environment_id": _text(raw["environment_id"], "environment_id"),
        "risk_class": GrantRiskClass(_text(raw["risk_class"], "risk_class")).value,
    }
    allowed_hosts = _text_sequence(raw.get("allowed_hosts"), "allowed_hosts", required=False)
    if allowed_hosts:
        result["allowed_hosts"] = sorted(set(allowed_hosts))
    for key in ("max_side_effect_actions", "max_data_mutation", "budget"):
        value = _optional_non_negative_int(raw.get(key), key)
        if value is not None:
            result[key] = value
    start = _optional_datetime(raw.get("time_window_start"), "time_window_start")
    end = _optional_datetime(raw.get("time_window_end"), "time_window_end")
    if (start is None) != (end is None):
        raise ValueError("time_window_start and time_window_end must be provided together")
    if start is not None and end is not None:
        result["time_window_start"] = start.isoformat()
        result["time_window_end"] = end.isoformat()
    understanding = raw.get("understanding_revision")
    if understanding is not None:
        result["understanding_revision"] = _positive_int(
            understanding, "understanding_revision"
        )
    elif require_understanding:
        raise ValueError("understanding_revision is required when the plan has no contract")
    return result


def _grant_payload(grant: PlanExecutionGrant) -> dict[str, Any]:
    """领域对象的规范落盘形状；集合排序，时间用 ISO-8601。"""

    return {
        "grant_id": grant.grant_id,
        "plan_revision": grant.plan_revision,
        "understanding_revision": grant.understanding_revision,
        "scope_digest": grant.scope_digest,
        "allowed_action_classes": sorted(grant.allowed_action_classes),
        "credential_scope_ref": grant.credential_scope_ref,
        "environment_id": grant.environment_id,
        "risk_class": grant.risk_class.value,
        "max_side_effect_actions": grant.max_side_effect_actions,
        "max_data_mutation": grant.max_data_mutation,
        "allowed_hosts": sorted(grant.allowed_hosts),
        "time_window_start": (
            grant.time_window_start.isoformat() if grant.time_window_start is not None else None
        ),
        "time_window_end": (
            grant.time_window_end.isoformat() if grant.time_window_end is not None else None
        ),
        "budget": grant.budget,
        "status": grant.status.value,
        "created_at": grant.created_at.isoformat(),
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at is not None else None,
    }


def _grant_binding(grant: PlanExecutionGrant) -> tuple[object, ...]:
    """幂等核对使用的绑定事实；``created_at`` 是创建时刻，不参与比较。"""

    return (
        grant.grant_id,
        grant.plan_revision,
        grant.understanding_revision,
        grant.scope_digest,
        tuple(sorted(grant.allowed_action_classes)),
        grant.credential_scope_ref,
        grant.environment_id,
        grant.risk_class,
        grant.max_side_effect_actions,
        grant.max_data_mutation,
        tuple(sorted(grant.allowed_hosts)),
        grant.time_window_start,
        grant.time_window_end,
        grant.budget,
        grant.status,
        grant.revoked_at,
    )


class PlanExecutionGrantService:
    """保存与读取计划级许可；不执行动作、不产生逐动作授权。"""

    def __init__(
        self,
        *,
        unit: StageableWorkspaceUnitOfWork,
        records: RecordRepository,
        workspace_id: str,
        actors: ControlledActorContext,
        clock: Clock,
    ) -> None:
        self.unit, self.records = unit, records
        self.workspace_id = workspace_id
        self.actors, self.clock = actors, clock

    def now(self) -> datetime:
        return self.clock.now()

    def create(
        self,
        *,
        project_id: str,
        intent_id: str,
        request_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        """把一次受控计划确认落成独立许可记录；相同意图重传返回原记录。"""

        require_controlled_actor(
            self.actors.current(), workspace_id=self.workspace_id, project_id=project_id
        )
        if (
            not isinstance(project_id, str)
            or not project_id.strip()
            or not isinstance(intent_id, str)
            or not intent_id.strip()
            or not isinstance(request_id, str)
            or not request_id.strip()
            or request_id == intent_id
        ):
            raise ValueError("plan grant requires an exact project, intent and request identity")
        values = dict(parameters)
        missing = _REQUIRED_PARAMETERS - set(values)
        unknown = set(values) - _REQUIRED_PARAMETERS - _OPTIONAL_PARAMETERS
        if missing or unknown:
            raise ValueError(
                f"plan grant parameters differ: missing={sorted(missing)} unknown={sorted(unknown)}"
            )

        plan_revision = _positive_int(values["plan_revision"], "plan_revision")
        understanding_revision = _positive_int(
            values["understanding_revision"], "understanding_revision"
        )
        scope_digest = _text(values["scope_digest"], "scope_digest")
        action_classes = _text_sequence(
            values["allowed_action_classes"], "allowed_action_classes", required=True
        )
        credential_scope_ref = _text(values["credential_scope_ref"], "credential_scope_ref")
        environment_id = _text(values["environment_id"], "environment_id")
        risk_class = GrantRiskClass(_text(values["risk_class"], "risk_class"))
        plan_id = _text(values["plan_id"], "plan_id")
        allowed_hosts = _text_sequence(values.get("allowed_hosts"), "allowed_hosts", required=False)
        start = _optional_datetime(values.get("time_window_start"), "time_window_start")
        end = _optional_datetime(values.get("time_window_end"), "time_window_end")
        if (start is None) != (end is None):
            raise ValueError("time_window_start and time_window_end must be provided together")
        max_side_effect_actions = _optional_non_negative_int(
            values.get("max_side_effect_actions"), "max_side_effect_actions"
        )
        max_data_mutation = _optional_non_negative_int(
            values.get("max_data_mutation"), "max_data_mutation"
        )
        budget = _optional_non_negative_int(values.get("budget"), "budget")

        self._validate_plan_binding(
            project_id=project_id,
            plan_id=plan_id,
            plan_revision=plan_revision,
            scope_digest=scope_digest,
            understanding_revision=understanding_revision,
        )

        identity = "plan-grant-" + _digest([self.workspace_id, project_id, intent_id])[7:]
        grant = PlanExecutionGrant(
            grant_id=identity,
            plan_revision=plan_revision,
            understanding_revision=understanding_revision,
            scope_digest=scope_digest,
            allowed_action_classes=frozenset(action_classes),
            credential_scope_ref=credential_scope_ref,
            environment_id=environment_id,
            risk_class=risk_class,
            max_side_effect_actions=max_side_effect_actions,
            max_data_mutation=max_data_mutation,
            allowed_hosts=frozenset(allowed_hosts),
            time_window_start=start,
            time_window_end=end,
            budget=budget,
            status=GrantStatus.ACTIVE,
            created_at=self.clock.now(),
            revoked_at=None,
        )
        grant_digest = grant_scope_digest(
            grant.plan_revision,
            grant.understanding_revision,
            grant.scope_digest,
            grant.allowed_action_classes,
            grant.credential_scope_ref,
            grant.environment_id,
        )
        payload = self._record_payload(project_id, plan_id, grant, grant_digest)
        binding = self._binding_payload(project_id, plan_id, grant, grant_digest)

        existing = self._read_current(project_id, identity)
        if existing is not None:
            self._require_same_intent(existing, plan_id, grant)
            self._require_saved_binding(
                project_id,
                plan_id,
                existing.grant.grant_id,
                existing.grant.plan_revision,
                existing.grant_scope_digest,
            )
            return self._created(existing.grant, existing.grant_scope_digest)

        self.unit.begin(request_id, project_id)
        try:
            existing = self._read_current(project_id, identity)
            if existing is not None:
                self._require_same_intent(existing, plan_id, grant)
                self._require_saved_binding(
                    project_id,
                    plan_id,
                    existing.grant.grant_id,
                    existing.grant.plan_revision,
                    existing.grant_scope_digest,
                )
                self.unit.rollback(request_id)
                return self._created(existing.grant, existing.grant_scope_digest)
            binding_revision = self._binding_target_revision(
                project_id,
                plan_id,
                grant.grant_id,
                grant.plan_revision,
                grant_digest,
            )
            if binding_revision is not None:
                self.unit.stage_record(
                    aggregate_kind=PLAN_GRANT_BINDING_KIND,
                    record_id=plan_id,
                    expected_revision=binding_revision,
                    payload=binding,
                )
            self.unit.stage_record(
                aggregate_kind=GRANT_AGGREGATE_KIND,
                record_id=identity,
                expected_revision=0,
                payload=payload,
            )
            self.unit.commit(request_id)
        except BaseException:
            self.unit.rollback(request_id)
            raise
        return self._created(grant, grant_digest)

    def read(self, *, project_id: str, grant_id: str) -> SavedPlanGrant:
        """按准确身份读取许可；未知身份、损坏信封或跨项目一律拒绝。"""

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id must not be empty")
        if not isinstance(grant_id, str) or not grant_id.strip():
            raise ValueError("grant_id must not be empty")
        revision = self.records.current_revision(
            aggregate_kind=GRANT_AGGREGATE_KIND, record_id=grant_id
        )
        if type(revision) is not int or revision < 0:
            raise ApprovalRequired("plan grant revision cannot be verified")
        if revision != 1:
            raise ApprovalRequired("the exact immutable plan grant is unavailable")
        try:
            saved = self.records.read(
                aggregate_kind=GRANT_AGGREGATE_KIND, record_id=grant_id, revision=1
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ApprovalRequired("the exact plan grant is unavailable") from error
        payload = getattr(saved, "payload", None)
        if (
            getattr(saved, "aggregate_kind", None),
            getattr(saved, "record_id", None),
            getattr(saved, "revision", None),
        ) != (GRANT_AGGREGATE_KIND, grant_id, 1) or not isinstance(payload, Mapping):
            raise ApprovalRequired("plan grant envelope cannot be verified")
        raw = dict(payload)
        if (
            raw.get("schema_version") != GRANT_SCHEMA_VERSION
            or raw.get("workspace_id") != self.workspace_id
            or raw.get("project_id") != project_id
        ):
            raise ApprovalRequired("plan grant has an unknown schema or owner")
        if set(raw) != {
            "schema_version",
            "workspace_id",
            "project_id",
            "plan_id",
            "grant",
            "grant_scope_digest",
            "input_digest",
        }:
            raise ApprovalRequired("plan grant has unknown fields")
        plan_id = raw["plan_id"]
        if plan_id is not None and (not isinstance(plan_id, str) or not plan_id.strip()):
            raise ApprovalRequired("plan grant plan identity cannot be verified")
        body = raw["grant"]
        if not isinstance(body, Mapping) or set(body) != {
            field.name for field in fields(PlanExecutionGrant)
        }:
            raise ApprovalRequired("plan grant body has unknown fields")
        try:
            grant = TypeAdapter(PlanExecutionGrant).validate_json(
                json.dumps(dict(body)), strict=True
            )
        except (TypeError, ValueError) as error:
            raise ApprovalRequired("plan grant body is malformed") from error
        if (
            grant.grant_id != grant_id
            or raw["input_digest"] != payload_digest(dict(body))
            or raw["grant_scope_digest"]
            != grant_scope_digest(
                grant.plan_revision,
                grant.understanding_revision,
                grant.scope_digest,
                grant.allowed_action_classes,
                grant.credential_scope_ref,
                grant.environment_id,
            )
        ):
            raise ApprovalRequired("plan grant identity or digest differs")
        # 撤销事实独立保存：读取时叠加为 revoked；原许可记录字节不改，
        # 已冻结的历史仍能按原修订审计“当时许可过什么”。
        revocation = self._read_revocation(project_id, grant_id)
        if revocation is not None:
            grant = replace(grant, status=GrantStatus.REVOKED, revoked_at=revocation.revoked_at)
        return SavedPlanGrant(
            grant=grant,
            grant_scope_digest=raw["grant_scope_digest"],
            record_revision=1,
            plan_id=plan_id,
        )

    def revoke(
        self,
        *,
        project_id: str,
        intent_id: str,
        request_id: str,
        grant_id: str,
        reason: str,
    ) -> Mapping[str, object]:
        """登记撤销一个仍有效的许可；撤销事实独立不可变，重传幂等。

        参数只接受准确许可身份与人类给出的原因。同一许可重传同一原因返回原
        撤销事实（提交序号不前进）；换原因按不同意图冲突处理。撤销不影响已
        完成的执行与已保存的授权记录，只让**后续**使用该许可的核对返回
        ``revoked``。
        """

        require_controlled_actor(
            self.actors.current(), workspace_id=self.workspace_id, project_id=project_id
        )
        if (
            not isinstance(project_id, str)
            or not project_id.strip()
            or not isinstance(intent_id, str)
            or not intent_id.strip()
            or not isinstance(request_id, str)
            or not request_id.strip()
            or request_id == intent_id
        ):
            raise ValueError(
                "plan grant revocation requires an exact project, intent and request identity"
            )
        if not isinstance(grant_id, str) or not grant_id.strip():
            raise ValueError("grant_id must be a non-empty string")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")

        saved = self.read(project_id=project_id, grant_id=grant_id)
        existing = self._read_revocation(project_id, grant_id)
        if existing is not None:
            return self._revocation_result(existing, reason=reason)
        if saved.grant.status is GrantStatus.REVOKED:
            raise ApprovalConflict("plan grant is already revoked by its immutable record")
        revoked_at = self.clock.now()
        payload = {
            "schema_version": REVOCATION_SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "project_id": project_id,
            "grant_id": grant_id,
            "reason": reason,
            "revoked_at": revoked_at.isoformat(),
        }

        self.unit.begin(request_id, project_id)
        try:
            existing = self._read_revocation(project_id, grant_id)
            if existing is not None:
                result = self._revocation_result(existing, reason=reason)
                self.unit.rollback(request_id)
                return result
            self.unit.stage_record(
                aggregate_kind=REVOCATION_AGGREGATE_KIND,
                record_id=grant_id,
                expected_revision=0,
                payload=payload,
            )
            self.unit.commit(request_id)
        except BaseException:
            self.unit.rollback(request_id)
            raise
        return {
            "grant_id": grant_id,
            "status": GrantStatus.REVOKED.value,
            "reason": reason,
            "revoked_at": revoked_at.isoformat(),
            "record_revision": 1,
        }

    def require_action_allowed(
        self,
        *,
        project_id: str,
        saved: SavedPlanGrant,
        plan: Plan,
        prepared: PreparedRun,
        action: ResolvedExecutionAction,
    ) -> None:
        """核对许可覆盖当前动作；不匹配返回结构化原因，不静默降级。"""

        grant = saved.grant
        # 撤销优先于其它失效原因（设计 7.3「撤销许可」）：即使计划或输入也变了，
        # 调用方只需要知道“这份许可已被撤销，需要重新确认”。
        if grant.status is GrantStatus.REVOKED:
            raise PlanGrantRejected(
                GrantInvalidationReason.REVOKED.value,
                "plan grant was invalidated by revoked",
            )
        if plan.record_revision is None or plan.record_revision != grant.plan_revision:
            raise PlanGrantRejected(
                GrantInvalidationReason.PLAN.value,
                "plan grant does not cover this exact plan revision",
            )
        if saved.plan_id is not None and saved.plan_id != plan.plan_id:
            raise PlanGrantRejected(
                GrantInvalidationReason.PLAN.value,
                "plan grant belongs to another plan",
            )
        contract = plan.coverage_contract
        understanding_revision = (
            contract.understanding_revision
            if contract is not None
            else grant.understanding_revision
        )
        reason = grant_invalidation_reason(
            grant,
            plan_revision=grant.plan_revision,
            understanding_revision=understanding_revision,
            scope_digest=plan_scope_digest(plan, project_id=project_id),
            credential_scope_ref=action.request.authorization_ref.credential_scope_ref,
            environment_id=prepared.environment.environment_id,
            now=self.clock.now(),
        )
        if reason is not None:
            raise PlanGrantRejected(reason.value, f"plan grant was invalidated by {reason.value}")
        if not grant_allows(
            grant,
            action_class=action.attempt.side_effect_class.value,
            credential_scope_ref=action.request.authorization_ref.credential_scope_ref,
            environment_id=prepared.environment.environment_id,
            now=self.clock.now(),
        ):
            raise PlanGrantRejected(
                "action_class",
                "plan grant was invalidated by action_class: "
                "this action class, credential scope or environment is not allowed",
            )
        if not grant_host_allowed(
            grant, action.request.registered_entry.entrypoint
        ):
            raise PlanGrantRejected(
                "allowed_hosts",
                "plan grant was invalidated by allowed_hosts: "
                "this HTTP entrypoint host is not allowed",
            )

    def _validate_plan_binding(
        self,
        *,
        project_id: str,
        plan_id: str,
        plan_revision: int,
        scope_digest: str,
        understanding_revision: int,
    ) -> None:
        """许可创建前核对计划记录、范围摘要与 understanding 修订。

        许可不能只凭调用方自报的计划身份创建；否则可以为不存在的计划或
        另一个范围伪造一份“看似绑定”的许可。计划与范围都必须按准确修订
        读取，且已发布。
        """
        try:
            saved_plan = self.records.read(
                aggregate_kind="plan", record_id=plan_id, revision=plan_revision
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PlanGrantRejected(
                GrantInvalidationReason.PLAN.value,
                "the exact plan for the grant is unavailable",
            ) from error
        plan_payload = getattr(saved_plan, "payload", None)
        if (
            getattr(saved_plan, "aggregate_kind", None),
            getattr(saved_plan, "record_id", None),
            getattr(saved_plan, "revision", None),
        ) != ("plan", plan_id, plan_revision) or not isinstance(plan_payload, Mapping):
            raise PlanGrantRejected(
                GrantInvalidationReason.PLAN.value,
                "plan grant plan envelope cannot be verified",
            )
        if (
            plan_payload.get("project_id") != project_id
            or plan_payload.get("plan_id") != plan_id
        ):
            raise PlanGrantRejected(
                GrantInvalidationReason.PLAN.value,
                "plan grant plan identity cannot be verified",
            )

        scope_id = plan_payload.get("scope_id")
        scope_revision = plan_payload.get("scope_revision")
        if not isinstance(scope_id, str) or not scope_id.strip() or type(scope_revision) is not int:
            raise PlanGrantRejected(
                GrantInvalidationReason.SCOPE.value,
                "plan grant plan has no exact scope identity",
            )
        try:
            saved_scope = self.records.read(
                aggregate_kind="acceptance_scope", record_id=scope_id, revision=scope_revision
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PlanGrantRejected(
                GrantInvalidationReason.SCOPE.value,
                "the exact plan scope for the grant is unavailable",
            ) from error
        scope_payload = getattr(saved_scope, "payload", None)
        if (
            getattr(saved_scope, "aggregate_kind", None),
            getattr(saved_scope, "record_id", None),
            getattr(saved_scope, "revision", None),
        ) != ("acceptance_scope", scope_id, scope_revision) or not isinstance(
            scope_payload, Mapping
        ):
            raise PlanGrantRejected(
                GrantInvalidationReason.SCOPE.value,
                "plan grant scope envelope cannot be verified",
            )
        if payload_digest(dict(scope_payload)) != scope_digest:
            raise PlanGrantRejected(
                GrantInvalidationReason.SCOPE.value,
                "plan grant scope digest differs from the saved scope",
            )

        contract = plan_coverage_contract_from_payload(plan_payload)
        if (
            contract is not None
            and contract.understanding_revision != understanding_revision
        ):
            raise PlanGrantRejected(
                GrantInvalidationReason.UNDERSTANDING.value,
                "plan grant understanding revision differs from the saved coverage contract",
            )

    def stage_side_effect_usage(
        self, *, project_id: str, saved: SavedPlanGrant, delta: int = 1
    ) -> None:
        """兼容入口：只累计副作用次数，不消耗数据变更预算。"""

        if saved.grant.max_side_effect_actions is None:
            return
        self.stage_action_usage(
            project_id=project_id,
            saved=saved,
            side_effect_delta=delta,
            data_mutation_delta=0,
        )

    def stage_action_usage(
        self,
        *,
        project_id: str,
        saved: SavedPlanGrant,
        side_effect_delta: int = 0,
        data_mutation_delta: int = 0,
    ) -> None:
        """在同一工作单元累计副作用与数据变更次数；超出任一预算即拒绝。

        数据变更口径：一期动作还没有独立的“数据变更”声明，因此把
        ``idempotent_write`` / ``non_idempotent_write`` / ``unknown`` 都按会修改
        业务数据处理；``unknown`` 按最严处理。只读动作不消耗任何预算。
        旧 1.0 usage 记录没有 ``data_mutations`` 字段；读取时把历史副作用次数
        视为历史数据变更次数，避免升级后低估已用预算。
        """

        side_limit = saved.grant.max_side_effect_actions
        data_limit = saved.grant.max_data_mutation
        if side_limit is None and data_limit is None:
            return
        if (
            type(side_effect_delta) is not int
            or side_effect_delta < 0
            or type(data_mutation_delta) is not int
            or data_mutation_delta < 0
        ):
            raise ValueError("usage deltas must be non-negative integers")
        grant_id = saved.grant.grant_id
        revision, used_side, used_data = self._usage_counts(
            project_id=project_id, grant_id=grant_id
        )
        next_side = used_side + side_effect_delta
        next_data = used_data + data_mutation_delta
        if side_limit is not None and next_side > side_limit:
            raise PlanGrantRejected(
                "max_side_effect_actions",
                "plan grant side-effect budget is exhausted",
            )
        if data_limit is not None and next_data > data_limit:
            raise PlanGrantRejected(
                "max_data_mutation",
                "plan grant data-mutation budget is exhausted",
            )
        if next_side == used_side and next_data == used_data:
            return
        self.unit.stage_record(
            aggregate_kind=USAGE_AGGREGATE_KIND,
            record_id=grant_id,
            expected_revision=revision,
            payload={
                "schema_version": USAGE_SCHEMA_VERSION_V11,
                "workspace_id": self.workspace_id,
                "project_id": project_id,
                "grant_id": grant_id,
                "side_effect_actions": next_side,
                "data_mutations": next_data,
            },
        )

    def _usage_counts(self, *, project_id: str, grant_id: str) -> tuple[int, int, int]:
        """读取精确 usage 记录；1.0 记录按保守口径补齐数据变更次数。"""

        revision = self.records.current_revision(
            aggregate_kind=USAGE_AGGREGATE_KIND, record_id=grant_id
        )
        if type(revision) is not int or revision < 0:
            raise ApprovalRequired("plan grant usage revision cannot be verified")
        if revision == 0:
            return 0, 0, 0
        try:
            record = self.records.read(
                aggregate_kind=USAGE_AGGREGATE_KIND,
                record_id=grant_id,
                revision=revision,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ApprovalRequired("plan grant usage is unavailable") from error
        payload = getattr(record, "payload", None)
        if (
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (USAGE_AGGREGATE_KIND, grant_id, revision) or not isinstance(
            payload, Mapping
        ):
            raise ApprovalRequired("plan grant usage envelope cannot be verified")
        schema = payload.get("schema_version")
        base = {"schema_version", "workspace_id", "project_id", "grant_id"}
        if schema == USAGE_SCHEMA_VERSION:
            expected = base | {"side_effect_actions"}
        elif schema == USAGE_SCHEMA_VERSION_V11:
            expected = base | {"side_effect_actions", "data_mutations"}
        else:
            raise ApprovalRequired("plan grant usage has an unknown schema")
        if (
            set(payload) != expected
            or payload.get("workspace_id") != self.workspace_id
            or payload.get("project_id") != project_id
            or payload.get("grant_id") != grant_id
            or type(payload.get("side_effect_actions")) is not int
            or payload["side_effect_actions"] < 0
        ):
            raise ApprovalRequired("plan grant usage fields cannot be verified")
        used_side = cast(int, payload["side_effect_actions"])
        if schema == USAGE_SCHEMA_VERSION:
            return revision, used_side, used_side
        if (
            type(payload.get("data_mutations")) is not int
            or payload["data_mutations"] < 0
        ):
            raise ApprovalRequired("plan grant data-mutation usage cannot be verified")
        return revision, used_side, cast(int, payload["data_mutations"])

    def _record_payload(
        self,
        project_id: str,
        plan_id: str | None,
        grant: PlanExecutionGrant,
        digest_value: str,
    ) -> dict[str, object]:
        body = _grant_payload(grant)
        return {
            "schema_version": GRANT_SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "project_id": project_id,
            "plan_id": plan_id,
            "grant": body,
            "grant_scope_digest": digest_value,
            "input_digest": payload_digest(body),
        }

    def _binding_payload(
        self,
        project_id: str,
        plan_id: str,
        grant: PlanExecutionGrant,
        digest_value: str,
    ) -> dict[str, object]:
        """计划到许可的稳定身份；与许可记录在同一次提交写入。"""

        return {
            "schema_version": PLAN_GRANT_BINDING_SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "project_id": project_id,
            "plan_id": plan_id,
            "grant_id": grant.grant_id,
            "plan_revision": grant.plan_revision,
            "grant_scope_digest": digest_value,
        }

    def _saved_binding(
        self, project_id: str, plan_id: str
    ) -> tuple[PlanGrantBinding, int] | None:
        """读取该计划**最新修订**的绑定；返回绑定与它的准确修订号。"""

        revision = self.records.current_revision(
            aggregate_kind=PLAN_GRANT_BINDING_KIND, record_id=plan_id
        )
        if type(revision) is not int or revision < 0:
            raise ApprovalRequired("plan grant binding revision cannot be verified")
        if revision == 0:
            return None
        try:
            saved = self.records.read(
                aggregate_kind=PLAN_GRANT_BINDING_KIND, record_id=plan_id, revision=revision
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ApprovalRequired("the exact plan grant binding is unavailable") from error
        payload = getattr(saved, "payload", None)
        if (
            getattr(saved, "aggregate_kind", None),
            getattr(saved, "record_id", None),
            getattr(saved, "revision", None),
        ) != (PLAN_GRANT_BINDING_KIND, plan_id, revision) or not isinstance(payload, Mapping):
            raise ApprovalRequired("plan grant binding envelope cannot be verified")
        try:
            binding = _binding_from_payload(
                payload,
                project_id=project_id,
                workspace_id=self.workspace_id,
                plan_id=plan_id,
            )
        except ValueError as error:
            raise ApprovalRequired("plan grant binding is malformed") from error
        return binding, revision

    def _binding_target_revision(
        self,
        project_id: str,
        plan_id: str,
        grant_id: str,
        plan_revision: int,
        digest_value: str,
    ) -> int | None:
        """返回绑定可写的目标修订；``None`` 表示已有匹配绑定、无需再写。

        - 还没有绑定：目标修订 ``0``（新建）；
        - 已绑定同一许可：``None``；
        - 已绑定另一份**仍有效**的许可：冲突，同一计划不能同时指向两份许可；
        - 已绑定另一份**已撤销**的许可：允许在当前修订之后追加一条新绑定，
          这是设计 7.3/EXEC-10 的“撤销后重新确认”；旧绑定与旧许可保留为历史。
        """

        saved = self._saved_binding(project_id, plan_id)
        if saved is None:
            return 0
        binding, revision = saved
        if (binding.grant_id, binding.plan_revision, binding.grant_scope_digest) == (
            grant_id,
            plan_revision,
            digest_value,
        ):
            return None
        if (
            self.read(project_id=project_id, grant_id=binding.grant_id).grant.status
            is not GrantStatus.REVOKED
        ):
            raise ApprovalConflict("plan already has a different plan execution grant")
        return revision

    def _require_saved_binding(
        self,
        project_id: str,
        plan_id: str,
        grant_id: str,
        plan_revision: int,
        digest_value: str,
    ) -> None:
        """核对待重传的许可仍与已有绑定一致；没有绑定按既有兼容行为接受。"""

        if (
            self._binding_target_revision(
                project_id, plan_id, grant_id, plan_revision, digest_value
            )
            not in (None, 0)
        ):
            raise ApprovalConflict("plan grant binding differs from its saved grant")

    def _read_revocation(
        self, project_id: str, grant_id: str
    ) -> SavedPlanGrantRevocation | None:
        """读取该许可的撤销事实；损坏/跨项目一律拒绝，不当作“未撤销”。"""

        revision = self.records.current_revision(
            aggregate_kind=REVOCATION_AGGREGATE_KIND, record_id=grant_id
        )
        if type(revision) is not int or revision < 0:
            raise ApprovalRequired("plan grant revocation revision cannot be verified")
        if revision == 0:
            return None
        if revision != 1:
            raise ApprovalRequired("the exact immutable plan grant revocation is unavailable")
        try:
            saved = self.records.read(
                aggregate_kind=REVOCATION_AGGREGATE_KIND, record_id=grant_id, revision=1
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ApprovalRequired("the exact plan grant revocation is unavailable") from error
        payload = getattr(saved, "payload", None)
        if (
            getattr(saved, "aggregate_kind", None),
            getattr(saved, "record_id", None),
            getattr(saved, "revision", None),
        ) != (REVOCATION_AGGREGATE_KIND, grant_id, 1) or not isinstance(payload, Mapping):
            raise ApprovalRequired("plan grant revocation envelope cannot be verified")
        try:
            return _revocation_from_payload(
                payload,
                project_id=project_id,
                workspace_id=self.workspace_id,
                grant_id=grant_id,
            )
        except ValueError as error:
            raise ApprovalRequired("plan grant revocation is malformed") from error

    @staticmethod
    def _revocation_result(
        existing: SavedPlanGrantRevocation, *, reason: str
    ) -> Mapping[str, object]:
        if existing.reason != reason:
            raise ApprovalConflict("plan grant revocation intent has a different reason")
        return {
            "grant_id": existing.grant_id,
            "status": GrantStatus.REVOKED.value,
            "reason": existing.reason,
            "revoked_at": existing.revoked_at.isoformat(),
            "record_revision": 1,
        }

    def _read_current(self, project_id: str, grant_id: str) -> SavedPlanGrant | None:
        revision = self.records.current_revision(
            aggregate_kind=GRANT_AGGREGATE_KIND, record_id=grant_id
        )
        if type(revision) is not int or revision < 0:
            raise ApprovalRequired("plan grant revision cannot be verified")
        return None if revision == 0 else self.read(project_id=project_id, grant_id=grant_id)

    @staticmethod
    def _require_same_intent(
        existing: SavedPlanGrant, plan_id: str | None, proposed: PlanExecutionGrant
    ) -> None:
        if existing.plan_id != plan_id or _grant_binding(existing.grant) != _grant_binding(
            proposed
        ):
            raise ApprovalConflict("plan grant intent was saved with different scope or envelope")

    @staticmethod
    def _created(grant: PlanExecutionGrant, digest_value: str) -> Mapping[str, object]:
        return {
            "grant_id": grant.grant_id,
            "record_revision": 1,
            "grant_scope_digest": digest_value,
        }


def plan_grant_environment_id(environment_ref: str) -> str:
    """执行引用到环境记录身份的公开映射，供授权读取方复用同一口径。"""

    return _environment_id(environment_ref)


__all__ = [
    "GRANT_AGGREGATE_KIND",
    "GRANT_SCHEMA_VERSION",
    "PLAN_GRANT_BINDING_KIND",
    "PLAN_GRANT_BINDING_SCHEMA_VERSION",
    "REVOCATION_AGGREGATE_KIND",
    "REVOCATION_SCHEMA_VERSION",
    "PlanExecutionGrantService",
    "PlanGrantBinding",
    "PlanGrantRejected",
    "SavedPlanGrant",
    "SavedPlanGrantRevocation",
    "normalize_grant_scope",
    "plan_grant_environment_id",
    "plan_scope_digest",
    "read_plan_grant_binding",
]
