import json

import pytest

from ai_test.infrastructure.file_store.records import ConflictError, FileRecordRepository


def test_record_writes_are_versioned_and_conflict_checked(tmp_path) -> None:
    records = FileRecordRepository(tmp_path / "workspace")
    assert records.put("projects", "p1", {"name": "one"}, expected_revision=0) == 1
    assert records.put("projects", "p1", {"name": "two"}, expected_revision=1) == 2
    assert records.get("projects", "p1") == {"name": "two", "revision": 2}
    with pytest.raises(ConflictError):
        records.put("projects", "p1", {"name": "stale"}, expected_revision=1)


def test_state_uses_bounded_chunk_index(tmp_path) -> None:
    records = FileRecordRepository(tmp_path / "workspace")
    for index in range(100):
        records.put("runs", f"run-{index}", {"index": index}, expected_revision=0)

    pointer = json.loads(records.current_file.read_text(encoding="utf-8"))
    state_path = records.generations / pointer["generation"] / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["schema_version"] == FileRecordRepository.STATE_SCHEMA
    assert state["revision"] == 100
    assert "records" not in state
    assert len(state["index_chunks"]) <= FileRecordRepository.SHARD_COUNT
    assert records.get("runs", "run-42") == {"index": 42, "revision": 1}
    assert len(list(records.list("runs"))) == 100


def test_legacy_state_migrates_to_chunked_index(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    generation = "gen-000001"
    state_dir = workspace / "generations" / generation
    record_dir = state_dir / "records" / "projects"
    record_dir.mkdir(parents=True)
    record_path = record_dir / "r00000001.json"
    record_path.write_text('{"name": "legacy", "revision": 1}\n', encoding="utf-8")
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "schema_version": FileRecordRepository.LEGACY_STATE_SCHEMA,
                "revision": 1,
                "records": {
                    "projects/p1": {
                        "revision": 1,
                        "path": record_path.relative_to(workspace).as_posix(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (workspace / "current.json").write_text(
        json.dumps({"schema_version": "aatp.workspace-pointer/1.0", "generation": generation}),
        encoding="utf-8",
    )

    records = FileRecordRepository(workspace)
    assert records.get("projects", "p1") == {"name": "legacy", "revision": 1}

    migrated = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    assert migrated["schema_version"] == FileRecordRepository.STATE_SCHEMA
    assert "records" not in migrated
    assert len(migrated["index_chunks"]) == 1


def test_check_writable_probes_workspace(tmp_path) -> None:
    records = FileRecordRepository(tmp_path / "workspace")
    writable, error = records.check_writable()
    assert writable is True
    assert error is None


def test_objects_are_content_addressed(tmp_path) -> None:
    from ai_test.infrastructure.file_store.objects import FileObjectStore

    store = FileObjectStore(tmp_path / "workspace")
    digest = store.put_bytes(b"evidence")
    assert len(digest) == 64
    assert store.read_bytes(digest) == b"evidence"