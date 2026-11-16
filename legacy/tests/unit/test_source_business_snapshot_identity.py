"""Same byte identity must not collide with a different frozen source contract."""

import pytest

from tests.unit.test_default_source_analysis import (
    analyze,
    dispatch,
    seed,
)
from tests.unit.test_default_source_analysis import (
    stack as stack,
)


@pytest.mark.parametrize(
    "changed",
    [
        {"source_scope": "selected-files/new-scope"},
        {"refetch_dependencies": ["frozen-repository-object"]},
        {"refetch_scope": "selected-files-offline"},
    ],
)
def test_changed_frozen_scope_does_not_overwrite_or_collide_with_same_bytes(stack, changed):
    core, source, _root = stack
    seed(core, source)
    first = analyze(core)
    assert first.error is None
    repo = core.unit_of_work.repo
    original = dict(
        repo.read(
            aggregate_kind="source_snapshot", record_id=first.result["snapshot_id"], revision=1
        ).payload
    )
    second = dispatch(
        core,
        "analyze_project",
        expected=1,
        binding_revision=1,
        intent="changed-source-contract",
        request="changed-source-request",
        parameters={
            "binding_id": "binding-project",
            "purpose": "prepare",
            "source_scope": ".",
            "exclusion_rules": [".git"],
            **changed,
        },
    )
    assert second.error is None, second.error
    assert second.result["content_identity"] == first.result["content_identity"]
    assert second.result["pinned_snapshot_id"] == first.result["pinned_snapshot_id"]
    assert second.result["snapshot_id"] != first.result["snapshot_id"]
    assert second.result["source_current_ref"]["record_revision"] == 2
    assert (
        dict(
            repo.read(
                aggregate_kind="source_snapshot", record_id=first.result["snapshot_id"], revision=1
            ).payload
        )
        == original
    )
    latest = repo.read(
        aggregate_kind="source_snapshot", record_id=second.result["snapshot_id"], revision=1
    ).payload
    assert all(latest[field] == value for field, value in changed.items())
    repeated = analyze(core, request="read-old-contract")
    assert repeated.error is None and repeated.result == first.result | {"reused": True}


def test_selected_file_order_and_duplicates_do_not_create_false_material_mismatch(stack):
    core, source, _root = stack
    (source / "helper.py").write_text("HELPER = 1\n", encoding="utf-8")
    seed(core, source)
    parameters = {
        "binding_id": "binding-project",
        "purpose": "prepare",
        "source_scope": ".",
        "selected_paths": ["main.py", "helper.py"],
    }
    first = dispatch(
        core,
        "analyze_project",
        intent="selected-source",
        request="selected-request",
        binding_revision=1,
        parameters=parameters,
    )
    assert first.error is None, first.error
    before = core.unit_of_work.current_commit_sequence()
    repeated = dispatch(
        core,
        "analyze_project",
        intent="selected-source",
        request="repeat-selected",
        binding_revision=1,
        parameters=parameters | {"selected_paths": ["helper.py", "main.py", "helper.py"]},
    )
    assert repeated.error is None, repeated.error
    assert repeated.result == first.result | {"reused": True}
    assert core.unit_of_work.current_commit_sequence() == before
    saved = core.unit_of_work.repo.read(
        aggregate_kind="source_snapshot", record_id=first.result["snapshot_id"], revision=1
    ).payload
    assert saved["selected_paths"] == ["helper.py", "main.py"]
