"""Real object reads close reference availability, not current reuse eligibility."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.application.execution.reuse_material import validate_source_material
from aitest.contracts.execution_facts import EvidenceKindFact
from aitest.domain.execution.runs import CapturedOutputBlock, OutputStreamName
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.spool import FileSpoolStore


def material(tmp_path):
    store = FileObjectStore(tmp_path)
    spool = FileSpoolStore(tmp_path)
    manifest = spool.persist_blocks((CapturedOutputBlock(
        "run", "step", "attempt", OutputStreamName.STDOUT, 0, 0, b"actual saved output\n",
    ),))
    output = manifest.blocks[0]
    evidence = store.publish_bytes("project", b"actual saved evidence\n")
    attempt = SimpleNamespace(
        attempt_id="attempt", run_id="run", step_id="step",
        output_blocks=(output,), output_block_refs=(output,),
    )
    ref = SimpleNamespace(
        evidence_id="evidence", attempt_id="attempt", project_id="project", run_id="run",
        step_id="step", object_digest=evidence.digest, object_size=evidence.size, media_type=None,
    )
    source = SimpleNamespace(
        steps=(SimpleNamespace(attempt=attempt, checkpoint=SimpleNamespace(attempt=attempt)),),
        spool=spool,
        facts=SimpleNamespace(project_id="project", run_id="run", evidence_refs=(ref,)),
    )
    return source, store, output, evidence


def validate(source, store):
    validate_source_material(source, store, source.spool)


def test_all_saved_objects_are_read_once_without_rewriting_facts(tmp_path):
    source, store, output, evidence = material(tmp_path)
    reader = Mock(wraps=store)
    validate(source, reader)
    assert [call.args[0].digest for call in reader.read_bytes.call_args_list] == [
        evidence.digest,
    ]
    assert not reader.publish_bytes.called


@pytest.mark.parametrize("which", ["output", "evidence"])
@pytest.mark.parametrize("damage", ["missing", "corrupt", "truncated"])
def test_saved_metadata_does_not_hide_absent_or_damaged_bytes(tmp_path, which, damage):
    source, store, output, evidence = material(tmp_path)
    ref = output if which == "output" else evidence
    path = (tmp_path / (
        "spool/attempt/stdout.log" if which == "output" else ref.relative_path
    )).resolve()
    assert path.is_relative_to(tmp_path.resolve())
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"wrong" if damage == "corrupt" else b"")
    with pytest.raises(ValueError, match="output_material|saved bytes"):
        validate(source, store)


@pytest.mark.parametrize("field", ["project_id", "run_id", "step_id"])
def test_evidence_has_the_exact_selected_source_ownership(tmp_path, field):
    source, store, _, _ = material(tmp_path)
    setattr(source.facts.evidence_refs[0], field, "another-owner")
    with pytest.raises(ValueError, match="another project, run or step"):
        validate(source, store)


def test_block_cannot_borrow_another_selected_attempt_identity(tmp_path):
    source, store, _, _ = material(tmp_path)
    second = SimpleNamespace(attempt_id="second", step_id="other-step", output_blocks=())
    source.steps += (SimpleNamespace(attempt=second),)
    source.steps[0].checkpoint.attempt = SimpleNamespace(
        attempt_id=second.attempt_id, step_id="step", run_id="run", output_block_refs=(),
    )
    with pytest.raises(ValueError, match="another attempt"):
        validate(source, store)


@pytest.mark.parametrize("result", [b"other bytes", None, bytearray(b"actual saved output\n")])
def test_an_object_port_return_is_independently_checked(tmp_path, result):
    source, _, _, _ = material(tmp_path)
    store = Mock()
    store.read_bytes.return_value = result
    with pytest.raises(ValueError, match="differ from their exact reference"):
        validate(source, store)


@pytest.mark.parametrize(
    "field,value", [("digest", "../../path"), ("length", True), ("length", -1)],
)
def test_bad_byte_reference_is_rejected_before_port_read(tmp_path, field, value):
    source, _, _, _ = material(tmp_path)
    original = source.steps[0].attempt.output_block_refs[0]
    store = Mock()
    with pytest.raises(ValueError):
        source.steps[0].attempt.output_block_refs = (replace(original, **{field: value}),)
        validate(source, store)
    store.read_bytes.assert_not_called()


def test_saved_output_requires_an_exact_checkpoint_before_read(tmp_path):
    source, store, _, _ = material(tmp_path)
    source.steps[0].checkpoint = None
    with pytest.raises(ValueError, match="exact checkpoint"):
        validate(source, store)


def test_missing_reader_is_not_evidence_availability(tmp_path):
    source, _, _, _ = material(tmp_path)
    with pytest.raises(ValueError, match="object reader"):
        validate(source, None)


def test_same_digest_with_different_size_is_not_covered_by_previous_read(tmp_path):
    source, store, output, _ = material(tmp_path)
    source.facts.evidence_refs[0].object_digest = output.digest
    source.facts.evidence_refs[0].object_size = output.length + 1
    with pytest.raises(ValueError):
        validate(source, store)


def test_duplicate_evidence_identity_does_not_hide_a_second_reference(tmp_path):
    source, store, _, _ = material(tmp_path)
    source.facts.evidence_refs *= 2
    with pytest.raises(ValueError, match="ambiguous"):
        validate(source, store)


def test_unexecuted_history_is_readable_without_creating_a_material_proof():
    source = SimpleNamespace(
        steps=(SimpleNamespace(attempt=None),), facts=SimpleNamespace(evidence_refs=()), spool=None,
    )
    validate(source, None)


@pytest.mark.parametrize("exact", [False, True])
def test_permanent_output_requires_exact_published_block_identity(tmp_path, exact):
    source, store, output, _ = material(tmp_path)
    saved = store.publish_bytes("project", source.spool.read_block(output))
    ref = SimpleNamespace(
        evidence_id="evidence:attempt:stdout:0" if exact else "another-evidence",
        attempt_id="attempt", project_id="project", run_id="run", step_id="step",
        object_digest=saved.digest, object_size=saved.size, media_type=None,
        evidence_kind=EvidenceKindFact.COMMAND_OUTPUT,
    )
    source.facts.evidence_refs += (ref,)
    source.spool = None
    if exact:
        validate(source, store)
    else:
        with pytest.raises(ValueError, match="output_material_reader_unavailable"):
            validate(source, store)


def test_same_bytes_from_another_selected_attempt_cannot_replace_source_ownership(tmp_path):
    source, store, output, _ = material(tmp_path)
    saved = store.publish_bytes("project", source.spool.read_block(output))
    other = SimpleNamespace(attempt_id="other", step_id="other-step", output_blocks=())
    source.steps += (SimpleNamespace(attempt=other, checkpoint=None),)
    source.facts.evidence_refs += (SimpleNamespace(
        evidence_id="evidence:attempt:stdout:0", attempt_id="other", project_id="project",
        run_id="run", step_id="other-step", object_digest=saved.digest, object_size=saved.size,
        media_type=None, evidence_kind=EvidenceKindFact.COMMAND_OUTPUT,
    ),)
    with pytest.raises(ValueError, match="differs from its exact checkpoint"):
        validate(source, store)
