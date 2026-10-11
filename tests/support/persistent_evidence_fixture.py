"""Synthetic fixture bytes saved in the real project object store before references."""

from hashlib import sha256

from aitest.infrastructure.file_store.objects import FileObjectStore

EVIDENCE_BYTES = b"component fixture evidence\n"
EVIDENCE_DIGEST = "sha256:" + sha256(EVIDENCE_BYTES).hexdigest()
OUTPUT_BYTES = b"component fixture output a\n"
OUTPUT_DIGEST = "sha256:" + sha256(OUTPUT_BYTES).hexdigest()


def fixture_facts(facts):
    return facts.model_copy(
        update={
            "evidence_refs": tuple(
                ref.model_copy(update={"object_digest": EVIDENCE_DIGEST})
                for ref in facts.evidence_refs
            )
        }
    )


def save_fixture_bytes(root, project="project-1"):
    store = FileObjectStore(root)
    assert store.publish_bytes(project, EVIDENCE_BYTES).digest == EVIDENCE_DIGEST
    assert store.publish_bytes(project, OUTPUT_BYTES).digest == OUTPUT_DIGEST
