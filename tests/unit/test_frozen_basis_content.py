"""A frozen label or readable revision is not proof of matching saved content."""

from aitest.application.planning.drift import check_frozen_basis
from aitest.application.planning.plan_builder import template_version_ref
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.substrate import transaction
from aitest.contracts.prepared_run import PlanRevisionRef, TemplateVersionRef
from aitest.domain.planning.templates import TemplateRef
from tests.support.prepared_run_factory import build_scenario


def check(prepared, reader, kind):
    return next(
        item
        for item in check_frozen_basis(prepared, reader=reader).checks
        if item.source_kind == kind
    )


def test_matching_snapshot_label_without_manifest_is_unverified():
    scenario = build_scenario("git")
    frozen = scenario.prepared_run.snapshot
    with transaction(scenario.unit_of_work, scenario.prepared_run.project_id) as tx:
        tx.stage_record(
            aggregate_kind="source_snapshot",
            record_id=frozen.source_snapshot_id,
            expected_revision=None,
            payload={
                "project_id": scenario.prepared_run.project_id,
                "content_identity": frozen.content_identity,
            },
        )
        tx.commit()
    assert not check(scenario.prepared_run, scenario.reader, "source_snapshot").readable


def test_exact_repository_revision_with_different_plan_bytes_is_unverified():
    scenario = build_scenario("git")
    prepared = scenario.prepared_run
    ref = prepared.plan_revision
    saved = scenario.reader.read(
        aggregate_kind="plan", record_id=ref.revision_id, revision=ref.revision_no
    )
    original_digest = payload_digest(saved.payload)
    with transaction(scenario.unit_of_work, prepared.project_id) as tx:
        tx.stage_record(
            aggregate_kind="plan",
            record_id=ref.revision_id,
            expected_revision=saved.revision,
            payload={**saved.payload, "scope_id": "another-scope"},
        )
        tx.commit()
    altered_ref = PlanRevisionRef(
        revision_id=ref.revision_id, revision_no=saved.revision + 1, digest=original_digest
    )
    prepared = prepared.model_copy(update={"plan_revision": altered_ref})
    assert not check(prepared, scenario.reader, "plan").readable


def test_installed_template_identity_requires_exact_content_digest():
    scenario = build_scenario("git")
    frozen = TemplateVersionRef(
        template_id="python-library", version="1.0.0", digest="sha256:" + "0" * 64
    )
    prepared = scenario.prepared_run.model_copy(update={"template_versions": (frozen,)})
    assert not check(prepared, scenario.reader, "template_versions").readable


def test_installed_template_correct_digest_is_verifiable():
    scenario = build_scenario("git")
    frozen = template_version_ref(TemplateRef("python-library", "1.0.0"))
    prepared = scenario.prepared_run.model_copy(update={"template_versions": (frozen,)})
    assert check(prepared, scenario.reader, "template_versions").readable


def test_readable_case_without_frozen_content_is_unverified():
    scenario = build_scenario("git")
    prepared = scenario.prepared_run
    ref = prepared.case_revisions[0]
    with transaction(scenario.unit_of_work, prepared.project_id) as tx:
        tx.stage_record(
            aggregate_kind="case",
            record_id=ref.case_id,
            expected_revision=None,
            payload={
                "project_id": prepared.project_id,
                "case_id": ref.case_id,
                "revision": 1,
                "objective": "different content",
            },
        )
        tx.commit()
    assert not check(prepared, scenario.reader, "case_revisions").readable


def test_readable_rule_without_frozen_content_is_unverified():
    scenario = build_scenario("git")
    prepared = scenario.prepared_run
    ref = prepared.rule_versions[0]
    with transaction(scenario.unit_of_work, prepared.project_id) as tx:
        tx.stage_record(
            aggregate_kind="rule_version",
            record_id=ref.rule_id,
            expected_revision=None,
            payload={
                "project_id": prepared.project_id,
                "rule_id": ref.rule_id,
                "status": "published",
            },
        )
        tx.commit()
    assert not check(prepared, scenario.reader, "rule_versions").readable
