"""PlanExecutionGrant 的字段校验、范围摘要与授权/失效判断。"""

import re
from datetime import UTC, datetime, timedelta

import pytest

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

_NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
_SCOPE_DIGEST = "sha256:" + "a" * 64
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _grant(**overrides: object) -> PlanExecutionGrant:
    base: dict[str, object] = {
        "grant_id": "grant-1",
        "plan_revision": 3,
        "understanding_revision": 2,
        "scope_digest": _SCOPE_DIGEST,
        "allowed_action_classes": frozenset({"http_request", "db_write"}),
        "credential_scope_ref": "credential:test",
        "environment_id": "env-test",
        "risk_class": GrantRiskClass.MEDIUM,
        "max_side_effect_actions": 10,
        "max_data_mutation": 100,
        "allowed_hosts": frozenset({"localhost"}),
        "time_window_start": _NOW - timedelta(hours=1),
        "time_window_end": _NOW + timedelta(hours=1),
        "budget": 50,
        "status": GrantStatus.ACTIVE,
        "created_at": _NOW,
        "revoked_at": None,
    }
    return PlanExecutionGrant(**(base | overrides))  # type: ignore[arg-type]


def _reason(
    grant: PlanExecutionGrant,
    *,
    plan_revision: int = 3,
    understanding_revision: int = 2,
    scope_digest: str = _SCOPE_DIGEST,
    credential_scope_ref: str = "credential:test",
    environment_id: str = "env-test",
) -> GrantInvalidationReason | None:
    return grant_invalidation_reason(
        grant,
        plan_revision=plan_revision,
        understanding_revision=understanding_revision,
        scope_digest=scope_digest,
        credential_scope_ref=credential_scope_ref,
        environment_id=environment_id,
        now=_NOW,
    )


# ------------------------------------------------------------ construction


def test_grant_accepts_valid_specification() -> None:
    grant = _grant()

    assert grant.status is GrantStatus.ACTIVE
    assert grant.risk_class is GrantRiskClass.MEDIUM
    assert grant.allowed_action_classes == frozenset({"http_request", "db_write"})
    assert grant.allowed_hosts == frozenset({"localhost"})
    assert grant.revoked_at is None


def test_grant_coerces_enum_strings() -> None:
    grant = _grant(status="revoked", revoked_at=_NOW, risk_class="high")

    assert grant.status is GrantStatus.REVOKED
    assert grant.risk_class is GrantRiskClass.HIGH


@pytest.mark.parametrize(
    "field",
    ["grant_id", "scope_digest", "credential_scope_ref", "environment_id"],
)
def test_grant_rejects_blank_identity(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _grant(**{field: "   "})


@pytest.mark.parametrize("field", ["plan_revision", "understanding_revision"])
@pytest.mark.parametrize("value", [0, -1])
def test_grant_rejects_non_positive_revisions(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        _grant(**{field: value})


def test_grant_rejects_empty_action_classes() -> None:
    with pytest.raises(ValueError, match="allowed_action_classes"):
        _grant(allowed_action_classes=frozenset())


def test_grant_rejects_blank_action_class() -> None:
    with pytest.raises(ValueError, match="allowed_action_classes"):
        _grant(allowed_action_classes=frozenset({"http_request", " "}))


def test_grant_rejects_blank_allowed_host() -> None:
    with pytest.raises(ValueError, match="allowed_hosts"):
        _grant(allowed_hosts=frozenset({"localhost", ""}))


@pytest.mark.parametrize(
    "field",
    ["max_side_effect_actions", "max_data_mutation", "budget"],
)
def test_grant_rejects_negative_limits(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _grant(**{field: -1})


def test_grant_accepts_zero_limits() -> None:
    grant = _grant(max_side_effect_actions=0, max_data_mutation=0, budget=0)

    assert grant.max_side_effect_actions == 0
    assert grant.max_data_mutation == 0
    assert grant.budget == 0


@pytest.mark.parametrize(
    ("start", "end"),
    [(_NOW, None), (None, _NOW)],
)
def test_grant_rejects_unpaired_time_window(start: datetime | None, end: datetime | None) -> None:
    with pytest.raises(ValueError, match="provided together"):
        _grant(time_window_start=start, time_window_end=end)


@pytest.mark.parametrize(
    ("start", "end"),
    [(_NOW, _NOW), (_NOW + timedelta(hours=1), _NOW)],
)
def test_grant_rejects_non_increasing_time_window(start: datetime, end: datetime) -> None:
    with pytest.raises(ValueError, match="before time_window_end"):
        _grant(time_window_start=start, time_window_end=end)


def test_grant_rejects_revoked_without_timestamp() -> None:
    with pytest.raises(ValueError, match="revoked_at"):
        _grant(status=GrantStatus.REVOKED)


@pytest.mark.parametrize("status", [GrantStatus.ACTIVE, GrantStatus.EXPIRED])
def test_grant_rejects_timestamp_on_non_revoked_grant(status: GrantStatus) -> None:
    with pytest.raises(ValueError, match="only a revoked grant"):
        _grant(status=status, revoked_at=_NOW)


# ------------------------------------------------------------ scope digest


def test_grant_scope_digest_is_stable_and_order_insensitive() -> None:
    first = grant_scope_digest(
        3,
        2,
        _SCOPE_DIGEST,
        {"http_request", "db_write"},
        "credential:test",
        "env-test",
    )
    second = grant_scope_digest(
        3,
        2,
        _SCOPE_DIGEST,
        ["db_write", "http_request", "http_request"],
        "credential:test",
        "env-test",
    )
    third = grant_scope_digest(
        3,
        2,
        _SCOPE_DIGEST,
        frozenset({"db_write", "http_request"}),
        "credential:test",
        "env-test",
    )

    assert first == second == third
    assert _DIGEST_PATTERN.fullmatch(first)


@pytest.mark.parametrize(
    "overrides",
    [
        {"plan_revision": 4},
        {"understanding_revision": 3},
        {"scope_digest": "sha256:" + "b" * 64},
        {"allowed_action_classes": ["http_request"]},
        {"credential_scope_ref": "credential:prod"},
        {"environment_id": "env-prod"},
    ],
)
def test_grant_scope_digest_binds_every_component(overrides: dict[str, object]) -> None:
    base: dict[str, object] = {
        "plan_revision": 3,
        "understanding_revision": 2,
        "scope_digest": _SCOPE_DIGEST,
        "allowed_action_classes": frozenset({"http_request", "db_write"}),
        "credential_scope_ref": "credential:test",
        "environment_id": "env-test",
    }

    assert grant_scope_digest(**(base | overrides)) != grant_scope_digest(  # type: ignore[arg-type]
        **base  # type: ignore[arg-type]
    )


def test_grant_scope_digest_rejects_empty_action_classes() -> None:
    with pytest.raises(ValueError, match="allowed_action_classes"):
        grant_scope_digest(3, 2, _SCOPE_DIGEST, (), "credential:test", "env-test")


# ------------------------------------------------------------ grant_allows


def test_grant_allows_matching_action() -> None:
    assert grant_allows(
        _grant(),
        action_class="http_request",
        credential_scope_ref="credential:test",
        environment_id="env-test",
        now=_NOW,
    )


def test_grant_allows_without_time_window() -> None:
    assert grant_allows(
        _grant(time_window_start=None, time_window_end=None),
        action_class="db_write",
        credential_scope_ref="credential:test",
        environment_id="env-test",
        now=_NOW,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"action_class": "db_drop"},
        {"credential_scope_ref": "credential:prod"},
        {"environment_id": "env-prod"},
    ],
)
def test_grant_allows_rejects_mismatched_binding(overrides: dict[str, str]) -> None:
    arguments = {
        "action_class": "http_request",
        "credential_scope_ref": "credential:test",
        "environment_id": "env-test",
    } | overrides

    assert not grant_allows(_grant(), now=_NOW, **arguments)


def test_grant_allows_rejects_elapsed_time_window() -> None:
    grant = _grant(
        time_window_start=_NOW - timedelta(hours=2),
        time_window_end=_NOW - timedelta(hours=1),
    )

    assert not grant_allows(
        grant,
        action_class="http_request",
        credential_scope_ref="credential:test",
        environment_id="env-test",
        now=_NOW,
    )


def test_grant_allows_rejects_not_yet_open_time_window() -> None:
    grant = _grant(
        time_window_start=_NOW + timedelta(hours=1),
        time_window_end=_NOW + timedelta(hours=2),
    )

    assert not grant_allows(
        grant,
        action_class="http_request",
        credential_scope_ref="credential:test",
        environment_id="env-test",
        now=_NOW,
    )


def test_grant_allows_rejects_revoked_grant() -> None:
    grant = _grant(status=GrantStatus.REVOKED, revoked_at=_NOW)

    assert not grant_allows(
        grant,
        action_class="http_request",
        credential_scope_ref="credential:test",
        environment_id="env-test",
        now=_NOW,
    )


def test_grant_allows_rejects_expired_status() -> None:
    grant = _grant(status=GrantStatus.EXPIRED)

    assert not grant_allows(
        grant,
        action_class="http_request",
        credential_scope_ref="credential:test",
        environment_id="env-test",
        now=_NOW,
    )


# --------------------------------------------------- grant_invalidation_reason


def test_grant_host_allowed_matches_http_entrypoint_host() -> None:
    assert grant_host_allowed(_grant(), "http://localhost:8080/workflow")
    assert not grant_host_allowed(_grant(), "https://example.com/workflow")
    assert grant_host_allowed(_grant(allowed_hosts=frozenset()), "https://example.com")


def test_grant_host_allowed_ignores_non_http_entrypoints() -> None:
    """本地命令没有 host 语义，不能因为白名单存在就误拦。"""

    assert grant_host_allowed(_grant(), "pytest")
    assert grant_host_allowed(_grant(), "python -m app")


def test_grant_invalidation_reason_is_none_while_current() -> None:
    assert _reason(_grant()) is None


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"plan_revision": 4}, GrantInvalidationReason.PLAN),
        ({"understanding_revision": 3}, GrantInvalidationReason.UNDERSTANDING),
        ({"scope_digest": "sha256:" + "b" * 64}, GrantInvalidationReason.SCOPE),
        ({"credential_scope_ref": "credential:prod"}, GrantInvalidationReason.CREDENTIAL),
        ({"environment_id": "env-prod"}, GrantInvalidationReason.ENVIRONMENT),
    ],
)
def test_grant_invalidation_reason_reports_changed_binding(
    changes: dict[str, object], expected: GrantInvalidationReason
) -> None:
    assert _reason(_grant(), **changes) is expected


def test_grant_invalidation_reason_reports_revoked_grant() -> None:
    grant = _grant(status=GrantStatus.REVOKED, revoked_at=_NOW)

    assert _reason(grant) is GrantInvalidationReason.REVOKED


def test_grant_invalidation_reason_reports_explicit_expiry() -> None:
    grant = _grant(status=GrantStatus.EXPIRED)

    assert _reason(grant) is GrantInvalidationReason.EXPIRED


def test_grant_invalidation_reason_reports_elapsed_time_window() -> None:
    grant = _grant(
        time_window_start=_NOW - timedelta(hours=2),
        time_window_end=_NOW - timedelta(hours=1),
    )

    assert _reason(grant) is GrantInvalidationReason.EXPIRED


def test_grant_invalidation_reason_reports_not_yet_open_time_window() -> None:
    grant = _grant(
        time_window_start=_NOW + timedelta(hours=1),
        time_window_end=_NOW + timedelta(hours=2),
    )

    assert _reason(grant) is GrantInvalidationReason.EXPIRED


def test_grant_invalidation_reason_prefers_revocation_over_binding_change() -> None:
    grant = _grant(status=GrantStatus.REVOKED, revoked_at=_NOW)

    assert _reason(grant, plan_revision=99) is GrantInvalidationReason.REVOKED


def test_grant_invalidation_reason_prefers_expiry_over_binding_change() -> None:
    grant = _grant(status=GrantStatus.EXPIRED)

    assert _reason(grant, plan_revision=99) is GrantInvalidationReason.EXPIRED
