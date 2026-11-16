"""DEC-021: explicit build/minimal-start plans, digest and prepare gating."""

from __future__ import annotations

import sys
from dataclasses import replace
from typing import cast

import pytest
from pydantic import ValidationError

from aitest.application.planning.prepare_run import preparation_payload, prepare_run
from aitest.contracts.prepared_run import (
    ExecutionSourceBinding,
    PreparedRunStatusFact,
    SourceCheckApplicability,
    SourceCheckPlan,
    SourceCheckPlans,
    TemplateVersionRef,
)
from aitest.domain.execution.sources import (
    ExecutionSourceVerification,
    SourceCheckStatusState,
    SourceCheckType,
)
from tests.unit.test_prepare_run import _inputs, _world

_BUILD = SourceCheckPlan(
    applicability=SourceCheckApplicability.REQUIRED,
    command=("python", "-m", "build", "--no-isolation"),
    descriptor="pyproject.toml",
    timeout_ms=120_000,
)
_START = SourceCheckPlan(
    applicability=SourceCheckApplicability.REQUIRED,
    command=("python", "-m", "service"),
    ready_when="stdout_contains:ready",
    timeout_ms=10_000,
)


def _service_inputs(**overrides: object):
    return replace(
        _inputs(
            template_versions=(
                TemplateVersionRef(
                    template_id="python-service", version="1.0.0", digest="sha256:tpl-svc"
                ),
            )
        ),
        **overrides,
    )


def _bind(plans: SourceCheckPlans) -> ExecutionSourceBinding:
    return _inputs().execution_source.model_copy(update={"source_checks": plans})


def test_plan_requires_a_reason_for_not_applicable() -> None:
    with pytest.raises(ValidationError):
        SourceCheckPlan(applicability=SourceCheckApplicability.NOT_APPLICABLE)


def test_minimal_start_requires_a_mechanical_ready_marker() -> None:
    with pytest.raises(ValidationError):
        SourceCheckPlans(
            minimal_start=SourceCheckPlan(
                applicability=SourceCheckApplicability.REQUIRED,
                command=("python", "-m", "service"),
            )
        )


def test_build_rejects_a_readiness_marker() -> None:
    with pytest.raises(ValidationError):
        SourceCheckPlans(
            build=SourceCheckPlan(
                applicability=SourceCheckApplicability.REQUIRED,
                command=("python", "-m", "build"),
                ready_when="stdout_contains:done",
            )
        )


def test_descriptor_cannot_escape_the_frozen_workdir() -> None:
    for descriptor in ("../pyproject.toml", ".", "/etc/passwd", "C:/Windows/system.ini"):
        with pytest.raises(ValidationError):
            SourceCheckPlan(
                applicability=SourceCheckApplicability.REQUIRED,
                command=("python", "-m", "build"),
                descriptor=descriptor,
            )


def test_registered_source_checks_change_the_request_digest() -> None:
    baseline = preparation_payload(_inputs())
    with_plans = preparation_payload(
        replace(
            _inputs(),
            execution_source=_bind(SourceCheckPlans(build=_BUILD, minimal_start=_START)),
        )
    )
    assert baseline["execution_source"]["source_checks"] == {
        "build": None,
        "minimal_start": None,
    }
    assert with_plans["execution_source"]["source_checks"]["build"] is not None
    assert baseline != with_plans


def test_python_service_without_plans_is_blocked_at_prepare() -> None:
    unit_of_work, reader, clock = _world()
    prepared = prepare_run(
        _service_inputs(), unit_of_work=unit_of_work, reader=reader, clock=clock
    )
    assert prepared.status is PreparedRunStatusFact.BLOCKED
    assert {reason.code for reason in prepared.blocking_reasons} == {
        "source_check_plan_unregistered"
    }
    assert reader.find_preparation(
        project_id="p1", client_id="c1", prepare_request_id="req-1"
    ) is None


def test_python_service_with_both_plans_is_prepared() -> None:
    inputs = _service_inputs(
        execution_source=_bind(SourceCheckPlans(build=_BUILD, minimal_start=_START))
    )
    unit_of_work, reader, clock = _world()
    prepared = prepare_run(inputs, unit_of_work=unit_of_work, reader=reader, clock=clock)
    assert prepared.status is PreparedRunStatusFact.PREPARED
    assert prepared.execution_source.source_checks.build == _BUILD


def test_python_service_with_optional_plan_is_still_blocked() -> None:
    inputs = _service_inputs(
        execution_source=_bind(
            SourceCheckPlans(
                build=_BUILD.model_copy(
                    update={"applicability": SourceCheckApplicability.OPTIONAL}
                ),
                minimal_start=_START,
            )
        )
    )
    unit_of_work, reader, clock = _world()
    prepared = prepare_run(inputs, unit_of_work=unit_of_work, reader=reader, clock=clock)
    assert prepared.status is PreparedRunStatusFact.BLOCKED


def test_python_library_defaults_to_not_applicable_without_blocking() -> None:
    inputs = _inputs(
        template_versions=(
            TemplateVersionRef(
                template_id="python-library", version="1.0.0", digest="sha256:tpl-lib"
            ),
        )
    )
    unit_of_work, reader, clock = _world()
    prepared = prepare_run(inputs, unit_of_work=unit_of_work, reader=reader, clock=clock)
    assert prepared.status is PreparedRunStatusFact.PREPARED


def _verification() -> ExecutionSourceVerification:
    from datetime import UTC, datetime

    from aitest.domain.execution.runs import FailureClass, PlanRevisionRef
    from aitest.domain.execution.sources import SourceVerificationState

    return ExecutionSourceVerification(
        verification_id="source-verification:snap-1",
        project_id="p1",
        plan_revision_ref=PlanRevisionRef(
            revision_id="plan-1", revision_no=1, digest="sha256:plan-1"
        ),
        expected_source_content_digest="sha256:content-1",
        materialized_snapshot_ref="C:/work",
        observed_source_digest="sha256:content-1",
        state=SourceVerificationState.VERIFIED,
        failure_class=FailureClass.PASSED,
        verified_at=datetime.now(UTC),
    )


def test_required_check_failure_downgrades_the_whole_verification() -> None:
    from aitest.application.execution.source_checks import merge_source_checks
    from aitest.domain.execution.sources import (
        SourceCheckStatus,
        SourceCheckStatusState,
        SourceCheckType,
        SourceVerificationState,
    )

    failed = SourceCheckStatus(
        check_type=SourceCheckType.MINIMAL_START,
        state=SourceCheckStatusState.UNKNOWN,
        reason="source_check_plan_unregistered",
    )
    merged = merge_source_checks(
        _verification(), statuses=(failed,), required_failures=(failed,)
    )
    assert merged.state is SourceVerificationState.UNKNOWN
    assert merged.gap_ids == (
        "source_check_unknown:minimal_start:source_check_plan_unregistered",
    )
    assert merged.checks == (failed,)


def test_optional_check_failure_is_recorded_without_downgrading() -> None:
    from aitest.application.execution.source_checks import merge_source_checks
    from aitest.domain.execution.sources import (
        SourceCheckStatus,
        SourceCheckStatusState,
        SourceCheckType,
        SourceVerificationState,
    )

    failed = SourceCheckStatus(
        check_type=SourceCheckType.APPLICABLE_BUILD,
        state=SourceCheckStatusState.MISMATCH,
        reason="source check exit 2",
    )
    merged = merge_source_checks(_verification(), statuses=(failed,))
    assert merged.state is SourceVerificationState.VERIFIED
    assert merged.gap_ids == ()
    assert merged.checks == (failed,)


class _VerifiedRunner:
    def run(self, request):  # type: ignore[no-untyped-def]
        from aitest.application.execution.source_checks import SourceCommandOutcome
        from aitest.domain.execution.runs import FailureClass

        return SourceCommandOutcome(
            state=SourceCheckStatusState.VERIFIED,
            failure_class=FailureClass.PASSED,
            reason=f"{request.check_type.value} ok",
            evidence_ref=f"objects/p1/{request.check_type.value}",
        )


def _statuses(prepared: dict[str, object], runner: object, layer: str = "L1"):
    from aitest.bootstrap import _registered_source_check_statuses

    return _registered_source_check_statuses(
        prepared,
        project_id="p1",
        technical_id="snap-1",
        workdir="C:/work",
        interpreter=sys.executable,
        layer=layer,
        runner=cast(object, runner),  # type: ignore[arg-type]
        environment_ref="env-1@1",
        rules_revision="rules-1",
        adapter_version="python-checks/1.0",
    )


def test_service_registration_runs_both_checks_and_requires_them() -> None:
    prepared = {
        "execution_source": {
            "source_checks": {
                "build": _BUILD.model_dump(mode="json"),
                "minimal_start": _START.model_dump(mode="json"),
            }
        },
        "template_versions": [{"template_id": "python-service"}],
    }
    statuses, failures, results = _statuses(prepared, _VerifiedRunner())
    assert [item.check_type for item in statuses] == [
        SourceCheckType.APPLICABLE_BUILD,
        SourceCheckType.MINIMAL_START,
    ]
    assert all(item.state is SourceCheckStatusState.VERIFIED for item in statuses)
    assert failures == ()
    assert [item.check_type for item in results] == [
        SourceCheckType.APPLICABLE_BUILD,
        SourceCheckType.MINIMAL_START,
    ]
    assert all(item.attempt_id == "pending-attempt" for item in results)
    assert all(item.failure_class.value == "passed" for item in results)


def test_new_record_without_plans_marks_library_checks_not_applicable() -> None:
    prepared = {
        "execution_source": {"source_checks": {"build": None, "minimal_start": None}},
        "template_versions": [{"template_id": "python-library"}],
    }
    statuses, failures, _ = _statuses(prepared, None)
    assert failures == ()
    assert all(item.state is SourceCheckStatusState.NOT_APPLICABLE for item in statuses)
    assert all("template_default_not_applicable" in (item.reason or "") for item in statuses)


def test_legacy_service_record_without_the_key_keeps_an_unknown_gap() -> None:
    prepared = {
        "execution_source": {},
        "template_versions": [{"template_id": "python-service"}],
    }
    statuses, failures, _ = _statuses(prepared, None)
    assert failures == statuses
    assert all(item.state is SourceCheckStatusState.UNKNOWN for item in statuses)
    assert all(item.reason == "source_check_plan_unregistered" for item in statuses)


def test_non_l1_action_does_not_run_build_or_start() -> None:
    prepared = {
        "execution_source": {
            "source_checks": {
                "build": _BUILD.model_dump(mode="json"),
                "minimal_start": _START.model_dump(mode="json"),
            }
        },
        "template_versions": [{"template_id": "python-service"}],
    }
    statuses, failures, results = _statuses(prepared, _VerifiedRunner(), layer="L2")
    assert failures == ()
    assert all(item.state is SourceCheckStatusState.NOT_APPLICABLE for item in statuses)
    assert all(item.reason == "not_an_l1_action" for item in statuses)
    assert results == ()
