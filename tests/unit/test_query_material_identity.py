"""A valid digest cannot turn a wrong summary into a trusted query identity."""

from copy import deepcopy

import pytest

from aitest.application.ports import RecordQuery
from aitest.contracts.queries import QuerySpec
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.records import FileRecordRepository


def publish_leaf(tmp_path, *, family, original, changed):
    index = FileQueryIndex(tmp_path, shard_size=8)
    index.rebuild([original])
    meta = index._read_meta()
    snapshot = index._snapshot(meta)
    candidates = (
        index._generic_family_keys(original)
        + (index._report_physical_keys(original) if original.get("report_id") else [])
        + index._issue_physical_keys(original)
    )
    key = next(
        key
        for name, key in candidates
        if name == family and (family != "reports" or key[1] == "passed")
    )
    snapshot["families"][family] = index._directory(family).bulk_build(
        [(key, changed)], generation=meta["generation"], commit_id=meta["last_commit_sequence"]
    )
    # Publish the exact new leaf/root digests. This exercises semantic identity,
    # rather than failing on an unrelated byte digest that was not updated.
    index._write_meta(
        meta["generation"],
        meta["last_commit_sequence"],
        families=snapshot["families"],
        latest_keys=snapshot["latest_keys"],
    )
    return index


def row(kind="case"):
    value = dict(project_id="p", aggregate_kind=kind, record_id="id", revision=1, commit_sequence=1)
    if kind == "report":
        value.update(
            report_id="report", run_id="run", business_outcome="passed", published_sequence=1
        )
    if kind == "issue":
        value.update(updated_sequence=1, issue_index_entries=[dict(view="ALL", mask=0)])
    return value


@pytest.mark.parametrize("field", ["revision", "commit_sequence"])
@pytest.mark.parametrize("value", [True, "1", 1.5])
def test_stored_query_counter_cannot_be_coerced_into_a_valid_identity(tmp_path, field, value):
    original = row()
    index = publish_leaf(
        tmp_path, family="records-kind", original=original, changed=original | {field: value}
    )
    result = index.query_spec(QuerySpec(project_id="p", aggregate_kind="case"))
    assert (
        result.status == "maintenance_required"
        and result.items == ()
        and result.next_cursor is None
    )


@pytest.mark.parametrize("field", ["project_id", "aggregate_kind", "record_id"])
def test_physical_query_prefix_cannot_disguise_a_different_record(tmp_path, field):
    original = row()
    index = publish_leaf(
        tmp_path, family="records-kind", original=original, changed=original | {field: "foreign"}
    )
    result = index.query_spec(QuerySpec(project_id="p", aggregate_kind="case"))
    assert result.status == "maintenance_required" and result.items == ()


@pytest.mark.parametrize(
    "field", ["report_id", "business_outcome", "published_sequence", "aggregate_kind"]
)
def test_report_list_prefix_and_summary_must_describe_the_same_report(tmp_path, field):
    original = row("report")
    value = (
        2 if field == "published_sequence" else "failed" if field == "business_outcome" else "other"
    )
    index = publish_leaf(
        tmp_path, family="reports", original=original, changed=original | {field: value}
    )
    result = index.query_spec(QuerySpec(project_id="p", business_outcome="passed"))
    assert result.status == "maintenance_required" and result.items == ()


def test_report_by_run_prefix_cannot_disguise_another_run(tmp_path):
    original = row("report")
    index = FileQueryIndex(tmp_path)
    # Choose the run route explicitly; the report route is a distinct key.
    index.rebuild([original])
    meta = index._read_meta()
    snapshot = index._snapshot(meta)
    key = next(
        key
        for family, key in index._report_physical_keys(original)
        if family == "reports-byid" and key[1] == "run"
    )
    snapshot["families"]["reports-byid"] = index._directory("reports-byid").bulk_build(
        [(key, original | {"run_id": "other"})], generation=1, commit_id=1
    )
    index._write_meta(1, 1, families=snapshot["families"], latest_keys=snapshot["latest_keys"])
    result = index.query_spec(QuerySpec(project_id="p", run_id="run"))
    assert result.status == "maintenance_required" and result.items == ()


@pytest.mark.parametrize("damage", ["mask_bool", "view", "updated_sequence", "identity"])
def test_issue_view_key_cannot_disguise_another_facet_or_identity(tmp_path, damage):
    original = row("issue")
    changed = deepcopy(original)
    if damage == "mask_bool":
        changed["issue_index_entries"][0]["mask"] = False
    elif damage == "view":
        changed["issue_index_entries"][0]["view"] = "OPEN"
    elif damage == "updated_sequence":
        changed["updated_sequence"] = 2
    else:
        changed["record_id"] = "other"
    index = publish_leaf(tmp_path, family="issues", original=original, changed=changed)
    result = index.query_spec(QuerySpec(project_id="p", view="ALL"))
    assert result.status == "maintenance_required" and result.items == ()


@pytest.mark.parametrize("body", [{"project_id": "foreign"}, None, [["project_id", "p"]]])
def test_repository_query_does_not_hydrate_wrong_project_or_malformed_material(tmp_path, body):
    import json

    repo = FileRecordRepository(tmp_path)
    repo.append("case", "id", 0, {"project_id": "p"})
    data = json.loads((tmp_path / "records.json").read_text())
    data["records"]["case"]["id"][0] = body
    (tmp_path / "records.json").write_text(json.dumps(data), encoding="utf-8")
    FileQueryIndex(tmp_path).rebuild([row()])
    result = repo.query(RecordQuery(project_id="p", aggregate_kind="case"))
    assert result.status == "maintenance_required" and result.items == ()


def test_repository_query_summary_cannot_invent_a_report_business_outcome(tmp_path):
    repo = FileRecordRepository(tmp_path)
    original = row("report")
    repo.append(
        "report",
        "id",
        0,
        {
            "project_id": "p",
            "report_id": "report",
            "run_id": "run",
            "business_outcome": "failed",
            "published_sequence": 1,
        },
    )
    FileQueryIndex(tmp_path).rebuild([original])
    result = repo.query(RecordQuery(project_id="p", aggregate_kind="report"))
    assert result.status == "maintenance_required" and result.items == ()


@pytest.mark.parametrize(
    ("kind", "family", "selectors"),
    [
        ("case", "records-kind", {}),
        ("case", "records-rev", {"aggregate_kind": "case", "record_id": "id", "revision": 1}),
        ("report", "reports", {"business_outcome": "passed"}),
        ("report", "reports-byid", {"report_id": "report"}),
        ("issue", "issues", {"view": "ALL"}),
    ],
)
def test_valid_canonical_query_rows_keep_their_original_identity(tmp_path, kind, family, selectors):
    original = row(kind)
    index = publish_leaf(tmp_path, family=family, original=original, changed=original)
    result = index.query_spec(QuerySpec(project_id="p", **selectors))
    assert result.status == "ok" and result.items == (original,)


@pytest.mark.parametrize(
    "changed",
    [
        {"published_sequence": True},
        {"updated_sequence": "1"},
        {"content_revision": 1.5},
        {"report_id": True},
        {"run_id": ""},
        {"issue_index_entries": [dict(view="ALL", mask=True)]},
        {"issue_index_entries": [dict(view="unknown", mask=0)]},
        {"issue_index_entries": [dict(view="ALL", mask=0, facet_value=7)]},
    ],
)
def test_invalid_summary_fields_are_refused_before_index_publication(tmp_path, changed):
    from aitest.infrastructure.file_store.index import IndexMissing

    index = FileQueryIndex(tmp_path)
    original = row()
    index.rebuild([original])
    before = (tmp_path / "indexes.json").read_bytes()
    with pytest.raises(IndexMissing):
        index.publish(
            [original | {"record_id": "later", "commit_sequence": 2} | changed], commit_sequence=2
        )
    assert (tmp_path / "indexes.json").read_bytes() == before


def test_a_later_bad_row_does_not_expose_a_partial_success_page(tmp_path):
    index = FileQueryIndex(tmp_path)
    first, second = row() | {"record_id": "first"}, row() | {"record_id": "later"}
    index.rebuild([first, second])
    meta = index._read_meta()
    snapshot = index._snapshot(meta)
    pairs = []
    for item in (first, second):
        key = next(
            key for family, key in index._generic_family_keys(item) if family == "records-kind"
        )
        pairs.append((key, item if item is first else item | {"project_id": "foreign"}))
    snapshot["families"]["records-kind"] = index._directory("records-kind").bulk_build(
        pairs, generation=1, commit_id=1
    )
    index._write_meta(1, 1, families=snapshot["families"], latest_keys=snapshot["latest_keys"])
    result = index.query_spec(QuerySpec(project_id="p", limit=1))
    assert (
        result.status == "maintenance_required"
        and result.items == ()
        and result.next_cursor is None
    )


def test_nullable_issue_wildcards_preserve_existing_physical_keys(tmp_path):
    original = row("issue")
    original["issue_index_entries"][0].update(facet_value=None, severity=None)
    index = publish_leaf(tmp_path, family="issues", original=original, changed=original)
    result = index.query_spec(QuerySpec(project_id="p", view="ALL"))
    assert result.status == "ok" and result.items == (original,)


@pytest.mark.parametrize("sharded", [False, True])
def test_exact_query_uses_actual_submission_owner_when_body_has_no_project(
    tmp_path, monkeypatch, sharded
):
    from aitest.infrastructure.file_store.sharded_records import (
        ShardedCommits,
        ShardedRows,
        migrate_to_shards,
    )

    repo = FileRecordRepository(tmp_path)
    repo.commit_transaction(
        [("execution_intent", "id", 0, {"state": "saved"})],
        request_id="first",
        intent_id="original-intent",
        project_id="p",
        workspace_id="workspace",
    )
    if sharded:
        migrate_to_shards(tmp_path)

        def forbidden(self):
            raise AssertionError("normal sharded query must not enumerate history")

        monkeypatch.setattr(ShardedRows, "__iter__", forbidden)
        monkeypatch.setattr(ShardedCommits, "__iter__", forbidden)
    result = repo.query(
        RecordQuery(project_id="p", aggregate_kind="execution_intent", record_id="id")
    )
    assert result.status == "ok" and len(result.items) == 1
    assert result.items[0].payload == {"state": "saved"} and result.items[0].revision == 1
