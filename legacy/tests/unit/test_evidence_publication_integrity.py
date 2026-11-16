"""Evidence identity is checked at the publication boundary, including late filtering."""

import hashlib
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aitest.application.evidence.publication import EvidencePublicationContext, EvidencePublisher
from aitest.domain.evidence.evidence import CodeIdentity, SourceBindingKind
from aitest.domain.execution.runs import CapturedOutputBlock, OutputBlockRef, OutputStreamName
from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.security import KnownSecretRegistry


def _context():
    return EvidencePublicationContext(
        project_id="project-1",
        source_instance_id="source-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        code_identity=CodeIdentity(
            binding_kind=SourceBindingKind.PLAIN,
            workspace_ref="workspace-1",
            file_manifest_digest="sha256:source",
        ),
    )


def _block(content):
    return OutputBlockRef(
        block_id="block-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_index=0,
        offset=0,
        length=len(content),
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        complete=True,
        capture_source="runtime",
    )


def _capture(content):
    return CapturedOutputBlock(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_index=0,
        offset=0,
        content=content,
        complete=True,
        capture_source="runtime",
    )


def test_late_registered_credential_cannot_upgrade_changed_object_bytes_to_complete_evidence(
    tmp_path,
):
    secret = "1234567890"  # Same byte length as [REDACTED]: digest must also be checked.
    content = ("old " + secret + "\n").encode()
    spool = FileSpoolStore(tmp_path)
    spool.persist_blocks([_capture(content)])
    registry = KnownSecretRegistry()
    objects = FileObjectStore(tmp_path, registry=registry)
    registry.register(secret)
    publisher = EvidencePublisher(spool, objects)
    with pytest.raises(ValueError, match="preserve the verified output bytes"):
        publisher.publish_attempt(_context())
    for path in (tmp_path / "objects").rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


@pytest.mark.parametrize("field", ["project_id", "digest", "size", "stored_bytes"])
def test_unverified_object_store_receipt_cannot_create_complete_evidence(tmp_path, field):
    content = b"safe evidence\n"
    spool = FileSpoolStore(tmp_path)
    spool.persist_blocks([_capture(content)])
    actual = FileObjectStore(tmp_path)

    class AlteredStore:
        def publish_bytes(self, project_id, content, *, media_type):
            ref = actual.publish_bytes(project_id, content, media_type=media_type)
            if field == "stored_bytes":
                (tmp_path / ref.relative_path).write_bytes(b"changed after publish\n")
                return ref
            value = {"project_id": "foreign", "digest": "sha256:wrong", "size": ref.size + 1}[field]
            return replace(ref, **{field: value})

        def read_bytes(self, ref):
            return actual.read_bytes(ref)

    with pytest.raises(ValueError):
        EvidencePublisher(spool, AlteredStore()).publish_attempt(_context())


def test_spool_port_must_provide_the_exact_frozen_block_bytes_before_object_write(tmp_path):
    block = _block(b"expected\n")
    spool = SimpleNamespace(read_block=lambda ref: b"different\n")
    with pytest.raises(ValueError, match="frozen output block"):
        EvidencePublisher(spool, FileObjectStore(tmp_path)).publish_blocks(_context(), [block])
    assert not (tmp_path / "objects").exists()


def test_normal_spool_to_object_evidence_keeps_digest_size_and_exact_bytes(tmp_path):
    content = "真实字节证据\n".encode()
    spool, objects = FileSpoolStore(tmp_path), FileObjectStore(tmp_path)
    spool.persist_blocks([_capture(content)])
    refs = EvidencePublisher(spool, objects).publish_attempt(_context())
    assert len(refs) == 1 and refs[0].object_digest == _block(content).digest
    assert refs[0].object_size == len(content)
    assert refs[0].integrity.value == "complete"
    assert (
        tmp_path / "objects/project-1" / refs[0].object_digest.removeprefix("sha256:")
    ).read_bytes() == content


def test_object_reference_cannot_rebind_another_projects_relative_path(tmp_path):
    objects = FileObjectStore(tmp_path)
    ref = objects.publish_bytes("project-1", b"safe\n")
    assert objects.read_bytes(ref) == b"safe\n"
    with pytest.raises(ValueError, match="project/digest path"):
        objects.read_bytes(replace(ref, project_id="project-2"))


@pytest.mark.skipif(os.name != "nt", reason="Windows Junction contract")
def test_object_project_directory_cannot_alias_another_project(tmp_path):
    import _winapi

    objects = FileObjectStore(tmp_path)
    ref = objects.publish_bytes("project-1", b"safe\n")
    link = tmp_path / "objects/project-2"
    _winapi.CreateJunction(str(tmp_path / "objects/project-1"), str(link))
    assert compat.is_junction(link)
    before = (tmp_path / ref.relative_path).read_bytes()
    with pytest.raises(ValueError, match="link"):
        objects.publish_bytes("project-2", b"other\n")
    with pytest.raises(ValueError, match="link"):
        objects.read_bytes(
            replace(
                ref,
                project_id="project-2",
                relative_path=ref.relative_path.replace("project-1", "project-2"),
            )
        )
    assert (tmp_path / ref.relative_path).read_bytes() == before
