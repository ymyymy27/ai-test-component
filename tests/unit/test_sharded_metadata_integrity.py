"""Bad authority metadata must not become a usable revision through coercion."""

import pytest

from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.sharded_records import (
    SCHEMA,
    AuthorityTree,
    ShardedCommits,
    ShardedRows,
    _key,
    open_authority,
)


@pytest.mark.parametrize("revision", [True, "1", 1.5])
def test_identity_revision_does_not_coerce_non_integer_metadata(tmp_path, revision):
    tree = AuthorityTree(tmp_path)
    tree.put(
        _key("identity", "project", "project"), {"project_id": "project", "revision": revision}
    )
    with pytest.raises(ValueError):
        len(ShardedRows(tree, "project", "project"))


@pytest.mark.parametrize("metadata", [None, {}, {"project_id": "project"}, [["revision", 1]]])
def test_existing_identity_cannot_disappear_or_change_shape(tmp_path, metadata):
    tree = AuthorityTree(tmp_path)
    tree.put(_key("identity", "project", "project"), metadata)
    with pytest.raises(ValueError):
        len(ShardedRows(tree, "project", "project"))


@pytest.mark.parametrize("count", [True, "1", 1.5])
def test_ledger_count_does_not_coerce_non_integer_metadata(tmp_path, count):
    tree = AuthorityTree(tmp_path)
    tree.put(_key("ledger_count"), count)
    with pytest.raises(ValueError):
        len(ShardedCommits(tree))


def test_bool_authority_watermark_cannot_equal_an_integer_header(tmp_path):
    tree = AuthorityTree(tmp_path)
    tree.put(_key("authority_commit"), True)
    with pytest.raises(ValueError):
        open_authority(tmp_path, {"schema": SCHEMA, "root": tree.pointer, "commit": 1})


def test_unrecognized_header_schema_cannot_reuse_a_valid_tree(tmp_path):
    tree = AuthorityTree(tmp_path)
    tree.put(_key("authority_commit"), 1)
    with pytest.raises(ValueError):
        open_authority(tmp_path, {"schema": "unknown", "root": tree.pointer, "commit": 1})


def test_value_reference_to_another_directory_is_an_unverified_material(tmp_path):
    tree = AuthorityTree(tmp_path)
    wrong = tree._write({"entries": {}})
    tree.pointer = tree._write({"entries": {_key("record"): wrong}})
    with pytest.raises(ValueError):
        tree.get(_key("record"))


def test_repository_exact_read_does_not_treat_true_as_revision_one(tmp_path):
    repo = FileRecordRepository(tmp_path)
    repo.append("project", "project", 0, {"project_id": "project", "name": "first"})
    with pytest.raises(ValueError):
        repo.read(aggregate_kind="project", record_id="project", revision=True)


@pytest.mark.parametrize("method", ["append", "append_batch", "commit_transaction"])
def test_boolean_expected_revision_cannot_publish_another_business_record(tmp_path, method):
    repo = FileRecordRepository(tmp_path)
    repo.append("project", "project", 0, {"project_id": "project", "name": "first"})
    before = (tmp_path / "records.json").read_bytes()
    pending = [("project", "project", True, {"project_id": "project", "name": "changed"})]
    with pytest.raises(ValueError):
        if method == "append":
            repo.append(*pending[0])
        elif method == "append_batch":
            repo.append_batch(pending)
        else:
            repo.commit_transaction(
                pending,
                request_id="bad-request",
                intent_id="bad-intent",
                project_id="project",
                workspace_id="workspace",
            )
    assert (tmp_path / "records.json").read_bytes() == before


@pytest.mark.parametrize(
    "damage", ["bool_revision", "bool_commit", "broken_reference", "wrong_record"]
)
def test_bad_saved_intent_receipt_does_not_return_a_laundered_success(tmp_path, damage):
    import json

    repo = FileRecordRepository(tmp_path)
    pending = [("project", "project", 0, {"project_id": "project", "name": "original"})]
    kwargs = dict(
        request_id="first",
        intent_id="original-intent",
        project_id="project",
        workspace_id="workspace",
    )
    repo.commit_transaction(pending, **kwargs)
    data = json.loads((tmp_path / "records.json").read_text(encoding="utf-8"))
    saved = data["intents"]["original-intent"]
    if damage == "bool_revision":
        saved["created"][0][2] = True
    elif damage == "bool_commit":
        saved["commit_sequence"] = True
    elif damage == "broken_reference":
        saved["created"] = [["project"]]
    else:
        data["records"]["project"]["project"].append({"project_id": "project", "name": "later"})
        saved["created"][0][2] = 2
        data["commit"] = 2
    (tmp_path / "records.json").write_text(json.dumps(data), encoding="utf-8")
    before = (tmp_path / "records.json").read_bytes()
    with pytest.raises(ValueError):
        repo.commit_transaction(pending, **(kwargs | {"request_id": "retry"}))
    assert (tmp_path / "records.json").read_bytes() == before


def test_absent_identity_owner_initialization_and_history_keep_their_actual_semantics(tmp_path):
    tree = AuthorityTree(tmp_path)
    rows = ShardedRows(tree, "project", "project")
    assert len(rows) == 0 and rows.metadata == {}
    rows.set_owner("project")
    assert rows.metadata == {"revision": 0, "project_id": "project"}
    rows.append({"project_id": "project", "name": "first"})
    rows.append({"project_id": "project", "name": "second"})
    assert len(rows) == 2 and rows[-1]["name"] == "second" and rows[0]["name"] == "first"
    with pytest.raises(TypeError):
        rows[True]
    with pytest.raises(IndexError):
        rows[-3]


@pytest.mark.parametrize("body", [None, [["name", "converted"]]])
def test_existing_record_body_cannot_be_missing_or_converted_to_an_object(tmp_path, body):
    tree = AuthorityTree(tmp_path)
    tree.put(_key("identity", "project", "project"), {"project_id": "project", "revision": 1})
    tree.put(_key("record", "project", "project", 1), body)
    with pytest.raises(ValueError):
        ShardedRows(tree, "project", "project")[0]
