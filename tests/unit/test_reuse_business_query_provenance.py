"""Saved query proof, not readable object presence, backs a historical observation."""

from types import SimpleNamespace

import pytest

from aitest.application.execution.reuse_material import validate_source_material
from aitest.contracts.execution_facts import VerificationObservationFact
from tests.unit.test_saved_business_verification import command, service


@pytest.fixture
def original(tmp_path):
    saved, before, query = service(tmp_path)
    result = saved.apply(command(before))
    facts = saved.coordinator.read_current_facts(project_id=before.project_id, run_id=before.run_id)
    facts = facts.model_copy(
        update={
            "evidence_refs": tuple(
                item
                for item in facts.evidence_refs
                if item.evidence_id == result["evidence"]["evidence_id"]
            )
        }
    )
    # Isolate the query branch; this component fixture has synthetic output blocks.
    # Whole-case/actual command provenance is covered by the default integration.
    attempt = facts.attempts[0].model_copy(update={"output_blocks": ()})
    checkpoint = saved.coordinator.read_checkpoint(
        project_id=before.project_id, attempt_id=attempt.attempt_id
    )
    prepared = saved.authorizations._context.return_value[2]
    source = SimpleNamespace(
        facts=facts,
        original_preparation=prepared,
        steps=(SimpleNamespace(attempt=attempt, checkpoint=checkpoint),),
    )
    return source, saved, result, query


def validate(source, saved):
    return validate_source_material(source, saved.objects, records=saved.records)


def test_accurate_query_source_is_read_only_without_query_adapter(original):
    source, saved, _, query = original
    saved.resolver = saved.verifier = None
    before = saved.unit.commit_seq()
    validate(source, saved)
    assert saved.unit.commit_seq() == before
    assert query.read_business_object.call_count == 1


@pytest.mark.parametrize(
    "damage",
    [
        "observation",
        "query_scope",
        "evidence_origin",
        "evidence_code",
        "missing_query",
        "missing_admission",
        "missing_evidence_ref",
        "missing_inline_evidence",
    ],
)
def test_readable_actual_object_does_not_hide_wrong_query_provenance(
    original,
    monkeypatch,
    damage,
):
    source, saved, result, query = original
    facts = source.facts
    identity = result["verification"]["verification_id"]
    if damage in ("observation", "query_scope"):
        verification = facts.verifications[-1].model_copy(
            update={
                "observation": VerificationObservationFact.MISMATCHED,
            }
            if damage == "observation"
            else {"query_method": "another-query"}
        )
        source.facts = facts.model_copy(update={"verifications": (verification,)})
    elif damage in ("evidence_origin", "evidence_code"):
        evidence = facts.evidence_refs[-1]
        evidence = (
            evidence.model_copy(update={"source_instance_id": "another-core"})
            if (damage == "evidence_origin")
            else evidence.model_copy(
                update={
                    "code_identity": evidence.code_identity.model_copy(
                        update={"revision_ref": "another-source"},
                    )
                }
            )
        )
        source.facts = facts.model_copy(update={"evidence_refs": (evidence,)})
    elif damage == "missing_inline_evidence":
        source.facts = facts.model_copy(update={"evidence_refs": ()})
    else:
        kind, rid = {
            "missing_query": ("verification", identity),
            "missing_admission": ("execution_intent", identity + "-admission"),
            "missing_evidence_ref": ("evidence_ref", identity + "-evidence"),
        }[damage]
        current = saved.records.current_revision

        def revision(**kw):
            return 0 if (kw["aggregate_kind"], kw["record_id"]) == (kind, rid) else current(**kw)

        monkeypatch.setattr(saved.records, "current_revision", revision)
    before = saved.unit.commit_seq()
    with pytest.raises(ValueError):
        validate(source, saved)
    assert saved.unit.commit_seq() == before
    assert query.read_business_object.call_count == 1


def test_duplicate_query_observations_cannot_be_selected_by_dictionary_order(original):
    source, saved, _, _ = original
    actual = source.facts.verifications[0]
    wrong = actual.model_copy(update={"observation": VerificationObservationFact.MISMATCHED})
    for values in ((actual, wrong), (wrong, actual), (actual, actual)):
        source.facts = source.facts.model_copy(update={"verifications": values})
        with pytest.raises(ValueError, match="ambiguous"):
            validate(source, saved)


def test_query_proof_must_use_original_case_source_identity(original):
    source, saved, _, _ = original
    source.original_preparation.plain_manifest_digest = "sha256:another-frozen-source"
    with pytest.raises(ValueError, match="saved proof"):
        validate(source, saved)


@pytest.mark.parametrize("missing", ["records", "objects"])
def test_known_query_material_requires_actual_proof_ports(original, missing):
    source, saved, _, query = original
    with pytest.raises(ValueError, match="exact record and object readers"):
        validate_source_material(source, saved.objects if missing != "objects" else None,
                                 records=saved.records if missing != "records" else None)
    assert query.read_business_object.call_count == 1
