from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from aitest.application.evidence.pytest_failures import (
    cluster_identity,
    cluster_pytest_failures,
    collect_pytest_failures,
    failure_signature,
    matching_draft,
    normalize_failure_message,
    pytest_issue_drafts,
)
from aitest.contracts.execution_facts import (
    EvidenceIntegrityFact,
    EvidenceKindFact,
)

JUNIT_TWO_SAME_SIGNATURE = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" failures="2" errors="0" skipped="0">
  <testcase classname="tests.test_order" name="test_a">
    <failure type="AssertionError" message="boom in /tmp/a/foo.py">traceback</failure>
  </testcase>
  <testcase classname="tests.test_order" name="test_b">
    <failure type="AssertionError" message="boom in /tmp/b/foo.py">traceback</failure>
  </testcase>
</testsuite></testsuites>
"""

JUNIT_TWO_DIFFERENT_SIGNATURES = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" failures="2" errors="0" skipped="0">
  <testcase classname="tests.test_order" name="test_a">
    <failure type="AssertionError" message="expected 1 got 2">traceback</failure>
  </testcase>
  <testcase classname="tests.test_order" name="test_b">
    <failure type="ValueError" message="bad value">traceback</failure>
  </testcase>
</testsuite></testsuites>
"""


class _Objects:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read_bytes(self, ref: object) -> bytes:
        return self.content


def _facts(content: bytes, *, attempt: str = "attempt-1"):
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    evidence = SimpleNamespace(
        evidence_id="evidence-1",
        attempt_id=attempt,
        evidence_kind=EvidenceKindFact.ARTIFACT,
        integrity=EvidenceIntegrityFact.COMPLETE,
        media_type="application/xml; profile=junit",
        gap_ids=(),
        project_id="project-1",
        object_digest=digest,
        object_size=len(content),
    )
    return SimpleNamespace(
        project_id="project-1",
        run_id="run-1",
        evidence_refs=(evidence,),
        steps=(SimpleNamespace(step_id="step-1", case_id="case-1"),),
        attempts=(SimpleNamespace(attempt_id=attempt, step_id="step-1"),),
    )


def _drafts(content: bytes):
    facts = _facts(content)
    return pytest_issue_drafts(facts, _Objects(content))  # type: ignore[arg-type]


def test_same_signature_with_incidental_paths_clusters_once() -> None:
    drafts = _drafts(JUNIT_TWO_SAME_SIGNATURE)
    assert len(drafts) == 1
    cluster = drafts[0]
    assert len(cluster.failures) == 2
    assert {item.node_id for item in cluster.failures} == {
        "tests.test_order::test_a",
        "tests.test_order::test_b",
    }
    assert cluster.locations == ("location_unknown",)
    assert cluster.evidence_refs == ("evidence-1",)


def test_different_signatures_stay_apart() -> None:
    drafts = _drafts(JUNIT_TWO_DIFFERENT_SIGNATURES)
    assert len(drafts) == 2
    assert {item.failure_type for item in drafts} == {"AssertionError", "ValueError"}
    assert all(len(item.failures) == 1 for item in drafts)


def test_replaced_attempt_failures_are_not_reported() -> None:
    """Replaced attempts remain history but must not duplicate current failures."""

    facts = _facts(JUNIT_TWO_SAME_SIGNATURE, attempt="attempt-old")
    facts.current_attempt_by_step = {"step-1": "attempt-new"}
    assert (
        collect_pytest_failures(facts, _Objects(JUNIT_TWO_SAME_SIGNATURE))  # type: ignore[arg-type]
        == ()
    )
    facts.current_attempt_by_step = {"step-1": "attempt-old"}
    assert len(
        collect_pytest_failures(facts, _Objects(JUNIT_TWO_SAME_SIGNATURE))  # type: ignore[arg-type]
    ) == 2


def test_missing_location_is_explicit() -> None:
    failures = collect_pytest_failures(
        _facts(JUNIT_TWO_SAME_SIGNATURE), _Objects(JUNIT_TWO_SAME_SIGNATURE)
    )  # type: ignore[arg-type]
    assert failures
    assert all(item.location_unknown for item in failures)
    assert all(item.location == "location_unknown" for item in failures)


def test_signature_ignores_temporary_paths_and_addresses() -> None:
    first = failure_signature(
        failure_type="RuntimeError",
        category="failure",
        message="failed for /tmp/a/data at 0xdeadbeef id 550e8400-e29b-41d4-a716-446655440000",
        suspect_file=None,
        file=None,
    )
    second = failure_signature(
        failure_type="RuntimeError",
        category="failure",
        message="failed for /tmp/b/data at 0x1234 id 550e8400-e29b-41d4-a716-446655440001",
        suspect_file=None,
        file=None,
    )
    assert first == second
    assert normalize_failure_message("x 0x1f") == "x <addr>"


def test_cluster_identity_requires_a_sha256_digest() -> None:
    with pytest.raises(ValueError):
        cluster_identity({"run_id": "r", "step_id": "s", "signature": "short"})
    with pytest.raises(ValueError):
        cluster_identity({"run_id": "r", "step_id": "s", "signature": "A" * 64})


def test_matching_draft_rejects_an_unknown_cluster() -> None:
    drafts = _drafts(JUNIT_TWO_DIFFERENT_SIGNATURES)
    unknown = failure_signature(
        failure_type="KeyError", category="failure", message="missing",
        suspect_file=None, file=None,
    )
    with pytest.raises(ValueError):
        matching_draft(drafts, "run-1", "step-1", unknown)


def test_cluster_identity_is_deterministic_and_server_owned() -> None:
    drafts = cluster_pytest_failures(
        "project-1",
        "run-1",
        collect_pytest_failures(
            _facts(JUNIT_TWO_SAME_SIGNATURE), _Objects(JUNIT_TWO_SAME_SIGNATURE)
        ),  # type: ignore[arg-type]
    )
    assert len(drafts) == 1
    assert drafts[0].issue_id.startswith("issue:pytest:")
    assert drafts[0].payload()["schema_version"] == "aitest.pytest-issue-draft/1.0"
    assert len(drafts[0].payload()["failures"]) == 2  # type: ignore[arg-type]


def test_failure_comparison_separates_new_existing_and_fixed() -> None:
    from aitest.application.evidence.pytest_failures import compare_failure_nodes

    compared = compare_failure_nodes(
        ("a", "b", "c"),
        ("b", "c", "d"),
    )
    assert compared.new_node_ids == ("a",)
    assert compared.existing_node_ids == ("b", "c")
    assert compared.fixed_node_ids == ("d",)
