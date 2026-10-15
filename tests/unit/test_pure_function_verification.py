"""Frozen expectations cannot be supplied by the program being evaluated."""

from dataclasses import replace

import pytest

from aitest.application.evidence.pure_function import capture_pure_output
from aitest.application.ports import VerificationRequest
from tests.unit.test_dynamic_object import inputs


def request(**changes):
    return replace(VerificationRequest(
        verification_of="a", business_object_id="a", query_method="p:pure-function-stdout",
        target_deployment_ref="attempt:a", deadline_condition="immediate",
        expected_facts={"result": 5}, evidence_refs=("evidence:a:stdout:0",),
    ), **changes)


@pytest.mark.parametrize("raw,outcome", [
    (b'{"result":5}', "matched"), (b'{"result":6}', "mismatched"),
    (b'{"passed":true}', "no_result"), (b'{"result":true}', "mismatched"),
])
def test_saved_output_compared_to_independent_expected(raw, outcome):
    facts, objects = inputs(raw)
    captured = capture_pure_output(request(), facts, "s", objects)
    assert captured.verification.observation.value == outcome
    assert captured.actual_fields is not None
    assert captured.verification.evidence_refs == ("evidence:a:stdout:0",)


@pytest.mark.parametrize("changes", [
    {"business_object_id": "other"}, {"target_deployment_ref": "attempt:old"},
    {"query_method": "other:pure-function-stdout"}, {"evidence_refs": ()},
    {"deadline_condition": "poll_deadline_ms:100"},
])
def test_wrong_scope_rejected(changes):
    facts, objects = inputs(b'{"result":5}')
    with pytest.raises(ValueError):
        capture_pure_output(request(**changes), facts, "s", objects)


@pytest.mark.parametrize("raw", [b'[]', b'{"result":5,"result":6}', b'{"result":NaN}'])
def test_nonobject_and_ambiguous_output_rejected(raw):
    facts, objects = inputs(raw)
    with pytest.raises(ValueError):
        capture_pure_output(request(), facts, "s", objects)


def test_real_function_plan_execution_verification_report_export_restart(tmp_path, monkeypatch):
    from tests.unit.test_configured_business_verification import (
        test_published_plan_actual_execution_query_report_and_restart as chain,
    )

    chain(tmp_path, monkeypatch, pure=True)
