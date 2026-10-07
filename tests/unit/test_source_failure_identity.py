"""Code failure and actual source correspondence are separate saved facts."""

import sys
from dataclasses import replace

import pytest

from aitest.application.execution.source_checks import (
    SourceProbeObservation,
    SourceVerificationService,
)
from aitest.domain.execution.runs import FailureClass
from aitest.domain.execution.sources import SourceCheckType, SourceVerificationState
from tests.unit.test_source_checks import _request


@pytest.fixture
def source(tmp_path):
    material = tmp_path / "materialized"
    material.mkdir()
    entry = material / "module.py"
    entry.write_text("def broken(:\n", encoding="utf-8")
    request = _request(material)
    observation = SourceProbeObservation(
        observed_source_digest=request.expected_source_binding_digest,
        observed_entry_ref=str(entry), observed_import_ref=str(entry),
        observed_interpreter_ref=sys.executable, failure_class=FailureClass.SOURCE_ERROR,
        evidence_refs=("original-syntax-output",),
    )
    return request, observation


@pytest.mark.parametrize("kind", list(SourceCheckType))
def test_actual_code_failure_cannot_invent_a_different_source_identity(source, kind):
    request, observation = source
    result = SourceVerificationService().check(replace(request, check_type=kind), observation)
    assert result.verification.state is SourceVerificationState.VERIFIED
    assert result.verification.gap_ids == ()
    assert result.verification.failure_class is FailureClass.SOURCE_ERROR
    assert result.check_result.failure_class is FailureClass.SOURCE_ERROR
    assert result.check_result.evidence_refs == ("original-syntax-output",)


@pytest.mark.parametrize("missing", ["observed_entry_ref", "observed_interpreter_ref",
                                    "observed_import_ref"])
def test_code_failure_does_not_hide_missing_actual_source_proof(source, missing):
    request, observation = source
    result = SourceVerificationService().check(request, replace(observation, **{missing: None}))
    assert result.verification.state is SourceVerificationState.UNVERIFIED
    assert result.verification.gap_ids
    assert result.check_result.failure_class is FailureClass.SOURCE_ERROR


@pytest.mark.parametrize("damage", ["digest", "entry", "import"])
def test_real_source_mismatch_is_still_rejected_even_when_code_failed(source, tmp_path, damage):
    request, observation = source
    outside = tmp_path / "original.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    changed = replace(
        observation,
        **{
            {"digest": "observed_source_digest", "entry": "observed_entry_ref",
             "import": "observed_import_ref"}[damage]:
            "sha256:different" if damage == "digest" else str(outside)
        },
    )
    result = SourceVerificationService().check(request, changed)
    assert result.verification.state is SourceVerificationState.MISMATCH
    assert result.check_result.failure_class is FailureClass.SOURCE_ERROR


@pytest.mark.parametrize(
    ("failure", "state"),
    [(FailureClass.TOOL_FAILURE, SourceVerificationState.UNKNOWN),
     (FailureClass.ENVIRONMENT_UNREACHABLE, SourceVerificationState.BLOCKED),
     (FailureClass.DEPENDENCY_MISSING, SourceVerificationState.BLOCKED)],
)
def test_unobservable_tool_or_environment_failure_never_certifies_source(source, failure, state):
    request, observation = source
    result = SourceVerificationService().check(request, replace(observation, failure_class=failure))
    assert result.verification.state is state
    assert result.check_result.failure_class is failure
