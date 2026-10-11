"""Current aliases reuse one verified manifest while retaining exact JSON types."""

import json
from copy import deepcopy

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.file_store.business_changes import BusinessChangeIndex
from aitest.infrastructure.file_store.commit_manifest import CommitMaterialError, FileCommitStore
from aitest.infrastructure.file_store.publication_backend import FilePublicationBackend
from tests.unit.test_canonical_commit_material import stage


def test_one_current_read_checks_each_business_directory_once(tmp_path, monkeypatch):
    core = assemble_workspace_core(tmp_path, instance_id="proof-budget")
    try:
        stage(core.unit_of_work, "proof", ("case", "task", "plan"))
        store = FileCommitStore(tmp_path)
        expected = store.read_current()
        checked = []
        original = BusinessChangeIndex.tree

        def tree(self, kind):
            checked.append(kind)
            return original(self, kind)

        monkeypatch.setattr(BusinessChangeIndex, "tree", tree)
        assert store.read_current() == expected
        assert checked == list(expected["manifest"]["business_change_index_root"]["types"])
    finally:
        core.lifetime_lock.release()


@pytest.mark.parametrize("change", ["bool", "float", "unknown", "digest", "event", "missing"])
def test_pointer_cannot_substitute_types_or_aliases_of_the_verified_material(tmp_path, change):
    core = assemble_workspace_core(tmp_path, instance_id="typed-pointer")
    try:
        stage(core.unit_of_work, "proof")
        store = FileCommitStore(tmp_path)
        original = store.current_path.read_bytes()
        pointer = json.loads(original)
        if change == "bool":
            pointer["index_root"]["last_commit_sequence"] = True
        elif change == "float":
            pointer["index_root"]["last_commit_sequence"] = 1.0
        elif change == "unknown":
            pointer["index_root"]["extra"] = 1
        elif change == "digest":
            pointer["index_root"]["snapshot_sha256"] = "0" * 64
        elif change == "event":
            pointer["event_cursor"] = "another-cursor"
        else:
            del pointer["index_root"]
        changed = json.dumps(pointer).encode("utf-8")
        FilePublicationBackend(tmp_path).replace_current(changed, previous=original)
        with pytest.raises(CommitMaterialError):
            store.read_current()
        FilePublicationBackend(tmp_path).replace_current(original, previous=changed)
        assert store.read_current(verify_material=True) is not None
    finally:
        core.lifetime_lock.release()


def test_equivalent_json_key_order_is_still_a_valid_pointer(tmp_path):
    core = assemble_workspace_core(tmp_path, instance_id="pointer-order")
    try:
        stage(core.unit_of_work, "proof")
        store = FileCommitStore(tmp_path)
        saved = store.read_current()
        pointer = deepcopy(saved["pointer"])
        previous = store.current_path.read_bytes()
        changed = json.dumps(dict(reversed(list(pointer.items())))).encode("utf-8")
        FilePublicationBackend(tmp_path).replace_current(changed, previous=previous)
        assert store.read_current() == saved
    finally:
        core.lifetime_lock.release()
