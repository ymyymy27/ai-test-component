from pathlib import Path

from aitest.application.review.execution_decision_adapter import build_decision_facts
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.records import FileRecordRepository

FIXTURE_DIR = Path("docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures")


def test_real_object_and_snapshot_round_trip(tmp_path: Path) -> None:
    facts = ExecutionFacts.model_validate_json(
        (FIXTURE_DIR / "non_utf8.json").read_text(encoding="utf-8")
    )
    object_store = FileObjectStore(tmp_path)
    for evidence_id, sidecar_name in (
        ("evidence-utf8-out", "non_utf8.stdout.bin"),
        ("evidence-utf8-err", "non_utf8.stderr.bin"),
    ):
        evidence = next(item for item in facts.evidence_refs if item.evidence_id == evidence_id)
        raw_bytes = (FIXTURE_DIR / sidecar_name).read_bytes()
        stored = object_store.publish_bytes(
            facts.project_id,
            raw_bytes,
            media_type=evidence.media_type or "application/octet-stream",
        )
        assert stored.digest == evidence.object_digest
        assert stored.size == evidence.object_size
        assert object_store.read_bytes(stored) == raw_bytes

    repository = FileRecordRepository(tmp_path)
    expected_commit = repository.current_commit_sequence() + 1
    aligned = ExecutionFacts.model_validate(
        {
            **facts.model_dump(mode="json"),
            "snapshot_commit_id": f"commit-{expected_commit}",
            "snapshot_revision": 1,
        }
    )
    revision = repository.append(
        "execution_facts",
        aligned.run_id,
        0,
        aligned.model_dump(mode="json"),
    )
    stored = repository.read(
        aggregate_kind="execution_facts",
        record_id=aligned.run_id,
        revision=revision,
    )
    restored = ExecutionFacts.model_validate(stored.payload)
    decision = build_decision_facts(restored)

    assert repository.current_commit_sequence() == expected_commit
    assert restored.snapshot_commit_id == f"commit-{expected_commit}"
    assert decision.snapshot_commit_id == restored.snapshot_commit_id
    assert decision.snapshot_cursor == restored.snapshot_cursor
