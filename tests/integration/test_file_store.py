import pytest

from ai_test.infrastructure.file_store.records import ConflictError, FileRecordRepository


def test_record_writes_are_versioned_and_conflict_checked(tmp_path) -> None:
    records = FileRecordRepository(tmp_path / "workspace")
    assert records.put("projects", "p1", {"name": "one"}, expected_revision=0) == 1
    assert records.put("projects", "p1", {"name": "two"}, expected_revision=1) == 2
    assert records.get("projects", "p1") == {"name": "two", "revision": 2}
    with pytest.raises(ConflictError):
        records.put("projects", "p1", {"name": "stale"}, expected_revision=1)


def test_objects_are_content_addressed(tmp_path) -> None:
    from ai_test.infrastructure.file_store.objects import FileObjectStore

    store = FileObjectStore(tmp_path / "workspace")
    digest = store.put_bytes(b"evidence")
    assert len(digest) == 64
    assert store.read_bytes(digest) == b"evidence"

