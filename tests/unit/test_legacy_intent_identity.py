"""Legacy standalone intent retries must prove the exact original record."""

import pytest

from aitest.infrastructure import security
from aitest.infrastructure.file_store import atomic
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.sharded_records import AuthorityTree, migrate_to_shards


@pytest.fixture
def saved(tmp_path):
    repo = FileRecordRepository(tmp_path)
    value = dict(
        intent_id="original",
        kind="case",
        record_id="first",
        expected_revision=0,
        payload={"project_id": "project", "summary": "original"},
    )
    assert repo.append_intent(**value) == 1
    return repo, value


@pytest.mark.parametrize("change", [{"kind": "plan"}, {"record_id": "other"}])
def test_same_body_with_different_business_identity_is_not_an_original_result(saved, change):
    repo, value = saved
    before = repo.path.read_bytes()
    with pytest.raises(ValueError, match="intent conflict"):
        repo.append_intent(**{**value, **change})
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize("problem", ["missing_body", "changed_body", "wrong_revision"])
def test_receipt_requires_its_exact_readable_original_body(saved, problem):
    repo, value = saved
    data = repo._load()
    if problem == "missing_body":
        data["records"]["case"]["first"].clear()
    elif problem == "changed_body":
        data["records"]["case"]["first"][0]["summary"] = "corrupted"
    else:
        data["intents"]["original"]["revision"] = 2
    repo._save(data)
    before = repo.path.read_bytes()
    with pytest.raises(ValueError):
        repo.append_intent(**value)
    assert repo.path.read_bytes() == before


def test_original_retry_survives_a_newer_revision_without_writing(saved):
    repo, value = saved
    repo.append("case", "first", 1, {"project_id": "project", "summary": "newer"})
    before = repo.path.read_bytes()
    restarted = FileRecordRepository(repo.root)
    assert restarted.append_intent(**{**value, "expected_revision": 2}) == 1
    assert repo.path.read_bytes() == before
    assert (
        repo.read(aggregate_kind="case", record_id="first", revision=2).payload["summary"]
        == "newer"
    )


def test_old_receipt_without_identity_is_not_silently_upgraded(saved):
    repo, value = saved
    data = repo._load()
    data["intents"]["original"] = {"fingerprint": "old-body-only", "revision": 1}
    repo._save(data)
    before = repo.path.read_bytes()
    with pytest.raises(ValueError):
        repo.append_intent(**value)
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize("field", ["intent_id", "kind", "record_id"])
@pytest.mark.parametrize("bad", [True, 1, "", "   "])
def test_intent_identity_is_strict_before_any_storage(tmp_path, monkeypatch, field, bad):
    repo = FileRecordRepository(tmp_path)
    monkeypatch.setattr(repo, "_load", lambda: pytest.fail("invalid identity reached storage"))
    value = dict(
        intent_id="intent",
        kind="case",
        record_id="case",
        expected_revision=0,
        payload={"project_id": "project"},
    )
    with pytest.raises(ValueError):
        repo.append_intent(**{**value, field: bad})


@pytest.mark.parametrize("bad", [None, [], 1, True])
def test_malformed_saved_receipt_is_a_blocked_read_without_mutation(saved, bad):
    repo, value = saved
    data = repo._load()
    data["intents"]["original"] = bad
    repo._save(data)
    before = repo.path.read_bytes()
    with pytest.raises(ValueError):
        repo.append_intent(**value)
    assert repo.path.read_bytes() == before


def test_sharded_original_retry_is_bounded_and_survives_restart(saved, monkeypatch):
    repo, value = saved
    migrate_to_shards(repo.root)
    monkeypatch.setattr(AuthorityTree, "items", lambda *_: pytest.fail("retry scanned history"))
    before = repo.path.read_bytes()
    restarted = FileRecordRepository(repo.root)
    assert restarted.append_intent(**value) == 1
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize("kind", ["intent", "append", "batch", "save"])
def test_unsafe_legacy_publication_never_reaches_a_temporary_file(tmp_path, monkeypatch, kind):
    repo = FileRecordRepository(tmp_path)
    registry = security.KnownSecretRegistry()
    registry.register("fixture-private-value")
    monkeypatch.setattr(security, "_GLOBAL_REGISTRY", registry)
    monkeypatch.setattr(atomic, "write_json", lambda *a, **k: pytest.fail("secret reached writer"))
    payload = {"project_id": "project", "summary": "fixture-private-value"}
    with pytest.raises(ValueError, match="safely"):
        if kind == "intent":
            repo.append_intent(
                intent_id="new", kind="case", record_id="case", expected_revision=0, payload=payload
            )
        elif kind == "append":
            repo.append("case", "case", 0, payload)
        elif kind == "batch":
            repo.append_batch([("case", "case", 0, payload)])
        else:
            repo._save({"records": {}, "commit": 0, "unsafe_old_metadata": payload})
    assert not repo.path.exists() and not tuple(tmp_path.glob(".records.json.*"))


@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"[]",
        b'{"records":{},"commit":0,"commit":1}',
        b'{"records":{},"commit":0,"history":{"value":NaN}}',
        b'{"records":{},"commit":0,"history":{"value":1e999}}',
        b'{"records":{},"commit":0,"history":{"value":-1e999}}',
    ],
)
def test_ambiguous_old_authority_is_preserved_and_blocked(tmp_path, raw):
    repo = FileRecordRepository(tmp_path)
    repo.path.write_bytes(raw)
    with pytest.raises(ValueError):
        repo.current_commit_sequence()
    assert repo.path.read_bytes() == raw


@pytest.mark.parametrize("counter", [True, "1", 1.5, -1])
def test_old_intent_does_not_convert_the_authority_counter(saved, counter):
    repo, value = saved
    data = repo._load()
    data["commit"] = counter
    repo._save(data)
    before = repo.path.read_bytes()
    with pytest.raises(ValueError):
        repo.append_intent(**value)
    assert repo.path.read_bytes() == before
