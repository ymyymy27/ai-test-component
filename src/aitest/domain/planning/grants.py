"""计划级执行许可（``PlanExecutionGrant``）的纯领域模型。

合同来源：

- 设计文档《03-AI主导与非开发者体验设计草案》6.4、6.5、6.6、12.3；
- 需求文档 P1-FR07、P1-AC31；
- 架构文档《02-执行与证据》第 6 节；
- 架构文档《05-Trae接入》第 9 节。

本模块只承载不可变记录、字段校验与纯函数判断，不做 I/O，不依赖
application / infrastructure / contracts。`PlanExecutionGrant` 是**授权来源**，
不是逐动作授权记录：单个动作的已解析输入摘要必须在后续
`ActionAuthorization` 中核对，本模块不判断输入摘要。

失效判定有多个原因同时成立时按固定优先级返回第一个原因：
``revoked`` → ``expired``（状态或时间窗）→ ``plan`` → ``understanding``
→ ``scope`` → ``credential`` → ``environment``。时间窗为闭区间
``[start, end]``；早于 ``start`` 与晚于 ``end`` 都按 ``expired`` 处理，
因为现行原因集合里没有单独的“尚未生效”。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse


class GrantStatus(StrEnum):
    """计划级许可的生命周期状态。"""

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class GrantRiskClass(StrEnum):
    """计划级许可的风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GrantInvalidationReason(StrEnum):
    """计划级许可失效的结构化原因。

    取值与设计文档 6.4、12.3（FLOW-08 / EXEC-04）一致；动作类别不在此判断，
    由 :func:`grant_allows` 按单动作匹配。
    """

    PLAN = "plan"
    UNDERSTANDING = "understanding"
    SCOPE = "scope"
    CREDENTIAL = "credential"
    ENVIRONMENT = "environment"
    REVOKED = "revoked"
    EXPIRED = "expired"


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_positive_int(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be an integer >= 1")


def _require_non_negative_int(value: int | None, name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be an integer >= 0")


def _text_set(values: Iterable[str], name: str) -> frozenset[str]:
    if isinstance(values, str):
        raise ValueError(f"{name} must be a collection of non-empty strings")
    try:
        frozen = frozenset(values)
    except TypeError as exc:
        raise ValueError(f"{name} must be a collection of non-empty strings") from exc
    for value in frozen:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must not contain empty values")
    return frozen


@dataclass(frozen=True, slots=True)
class PlanExecutionGrant:
    """一次计划确认派生的能力信封，绑定范围、凭据、环境与预算。

    该记录只回答“计划级许可允许哪些动作类别、在哪个凭据/环境下、带多少
    预算”。逐动作的输入摘要、目标与授权来源仍由 `ActionAuthorization`
    保存；撤销或过期后，未执行动作回到待授权。
    """

    grant_id: str
    plan_revision: int
    understanding_revision: int
    scope_digest: str
    allowed_action_classes: frozenset[str]
    credential_scope_ref: str
    environment_id: str
    risk_class: GrantRiskClass
    max_side_effect_actions: int | None
    max_data_mutation: int | None
    allowed_hosts: frozenset[str]
    time_window_start: datetime | None
    time_window_end: datetime | None
    budget: int | None
    status: GrantStatus
    created_at: datetime
    revoked_at: datetime | None

    def __post_init__(self) -> None:
        for name in (
            "grant_id",
            "scope_digest",
            "credential_scope_ref",
            "environment_id",
        ):
            _require_text(getattr(self, name), name)
        _require_positive_int(self.plan_revision, "plan_revision")
        _require_positive_int(self.understanding_revision, "understanding_revision")

        action_classes = _text_set(self.allowed_action_classes, "allowed_action_classes")
        if not action_classes:
            raise ValueError("allowed_action_classes must not be empty")
        object.__setattr__(self, "allowed_action_classes", action_classes)
        object.__setattr__(self, "allowed_hosts", _text_set(self.allowed_hosts, "allowed_hosts"))

        _require_non_negative_int(self.max_side_effect_actions, "max_side_effect_actions")
        _require_non_negative_int(self.max_data_mutation, "max_data_mutation")
        _require_non_negative_int(self.budget, "budget")

        start = self.time_window_start
        end = self.time_window_end
        if (start is None) != (end is None):
            raise ValueError("time_window_start and time_window_end must be provided together")
        if start is not None:
            if not isinstance(start, datetime) or not isinstance(end, datetime):
                raise ValueError("time window must contain datetimes")
            try:
                if start >= end:
                    raise ValueError("time_window_start must be before time_window_end")
            except TypeError as exc:
                raise ValueError("time window endpoints must be comparable datetimes") from exc

        object.__setattr__(self, "risk_class", GrantRiskClass(str(self.risk_class)))
        status = GrantStatus(str(self.status))
        object.__setattr__(self, "status", status)
        if status is GrantStatus.REVOKED:
            if self.revoked_at is None:
                raise ValueError("a revoked grant requires revoked_at")
        elif self.revoked_at is not None:
            raise ValueError("only a revoked grant may set revoked_at")


def grant_scope_digest(
    plan_revision: int,
    understanding_revision: int,
    scope_digest: str,
    allowed_action_classes: Iterable[str],
    credential_scope_ref: str,
    environment_id: str,
) -> str:
    """计算计划级许可的范围摘要，返回 ``sha256:<hex>``。

    动作类别先排序并去重，因此集合的迭代顺序不影响结果。摘要不包含
    风险等级、预算、主机与时间窗：这些是能力信封，不是“允许做什么”的
    身份；同一范围的不同信封仍共享同一范围摘要。
    """

    _require_positive_int(plan_revision, "plan_revision")
    _require_positive_int(understanding_revision, "understanding_revision")
    _require_text(scope_digest, "scope_digest")
    _require_text(credential_scope_ref, "credential_scope_ref")
    _require_text(environment_id, "environment_id")
    action_classes = _text_set(allowed_action_classes, "allowed_action_classes")
    if not action_classes:
        raise ValueError("allowed_action_classes must not be empty")

    payload = {
        "plan_revision": plan_revision,
        "understanding_revision": understanding_revision,
        "scope_digest": scope_digest,
        "allowed_action_classes": sorted(action_classes),
        "credential_scope_ref": credential_scope_ref,
        "environment_id": environment_id,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def grant_allows(
    grant: PlanExecutionGrant,
    *,
    action_class: str,
    credential_scope_ref: str,
    environment_id: str,
    now: datetime,
) -> bool:
    """判断许可是否覆盖当前动作类别、凭据、环境与时间窗。

    只做计划级匹配：许可状态必须为 ``active``，时间窗（如有）覆盖 ``now``，
    动作类别属于 ``allowed_action_classes``，凭据与环境完全相等。动作输入
    摘要、目标和授权来源属于后续 `ActionAuthorization`，不在这里判断。
    """

    if grant.status is not GrantStatus.ACTIVE:
        return False
    if not _within_time_window(grant, now):
        return False
    return (
        action_class in grant.allowed_action_classes
        and credential_scope_ref == grant.credential_scope_ref
        and environment_id == grant.environment_id
    )


def grant_host_allowed(grant: PlanExecutionGrant, entrypoint: str) -> bool:
    """核对 HTTP entrypoint 的主机是否在许可白名单内。

    非 HTTP(S) 的本地命令没有 host 语义，不在这里拦截；HTTP(S) 且能解析出
    hostname 时，白名单非空就必须精确匹配（主机名大小写不敏感）。
    """
    if not grant.allowed_hosts:
        return True
    parsed = urlparse(entrypoint)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return True
    return parsed.hostname.lower() in {host.lower() for host in grant.allowed_hosts}


def grant_invalidation_reason(
    grant: PlanExecutionGrant,
    *,
    plan_revision: int,
    understanding_revision: int,
    scope_digest: str,
    credential_scope_ref: str,
    environment_id: str,
    now: datetime,
) -> GrantInvalidationReason | None:
    """返回许可失效的结构化原因；仍有效时返回 ``None``。

    撤销优先于过期，过期优先于上游修订或绑定变化；多个上游变化同时发生时
    按 ``plan`` → ``understanding`` → ``scope`` → ``credential`` →
    ``environment`` 的顺序返回第一个。调用方不得用“无关变化”跳过这里的
    判断；设计文档 6.4 要求无法判断影响时按失效处理。
    """

    if grant.status is GrantStatus.REVOKED:
        return GrantInvalidationReason.REVOKED
    if grant.status is GrantStatus.EXPIRED:
        return GrantInvalidationReason.EXPIRED
    if not _within_time_window(grant, now):
        return GrantInvalidationReason.EXPIRED
    if plan_revision != grant.plan_revision:
        return GrantInvalidationReason.PLAN
    if understanding_revision != grant.understanding_revision:
        return GrantInvalidationReason.UNDERSTANDING
    if scope_digest != grant.scope_digest:
        return GrantInvalidationReason.SCOPE
    if credential_scope_ref != grant.credential_scope_ref:
        return GrantInvalidationReason.CREDENTIAL
    if environment_id != grant.environment_id:
        return GrantInvalidationReason.ENVIRONMENT
    return None


def _within_time_window(grant: PlanExecutionGrant, now: datetime) -> bool:
    start = grant.time_window_start
    end = grant.time_window_end
    if start is None or end is None:
        return True
    try:
        return start <= now <= end
    except TypeError:
        # 不同类型/时区意识不一致时无法证明落在窗口内，按不允许处理。
        return False
