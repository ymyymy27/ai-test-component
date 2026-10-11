"""Immutable identities must survive caller mutation and ambiguous JSON input."""

import hashlib

import pytest

from aitest.infrastructure.file_store.index import _key_cmp
from aitest.infrastructure.file_store.ordered_index import OrderedIndexTree
from aitest.infrastructure.file_store.sharded_records import AuthorityTree


@pytest.mark.parametrize("tree_kind", ["authority", "ordered"])
def test_saved_material_cannot_change_through_its_original_input(tmp_path, tree_kind):
    payload = {"nested": {"value": "original"}}
    if tree_kind == "authority":
        tree = AuthorityTree(tmp_path)
        tree.put("key", payload)
        pointer = tree.pointer
    else:
        tree = OrderedIndexTree(tmp_path, _key_cmp, leaf_size=8)
        tree.bulk_build([(("key",), payload)])
        pointer = tree.root
    payload["nested"]["value"] = "caller-mutation"
    if tree_kind == "authority":
        assert tree.get("key") == {"nested": {"value": "original"}}
        assert AuthorityTree(tmp_path, pointer).get("key") == tree.get("key")
    else:
        assert tree.get(("key",)) == {"nested": {"value": "original"}}
        assert OrderedIndexTree(tmp_path, _key_cmp, leaf_size=8, root=pointer).get(
            ("key",)
        ) == tree.get(("key",))


@pytest.mark.parametrize("tree_kind", ["authority", "ordered"])
def test_read_result_cannot_mutate_a_cached_immutable_record(tmp_path, tree_kind):
    payload = {"nested": {"value": "original"}}
    if tree_kind == "authority":
        tree = AuthorityTree(tmp_path)
        tree.put("key", payload)
        result = tree.get("key")
    else:
        tree = OrderedIndexTree(tmp_path, _key_cmp, leaf_size=8)
        tree.bulk_build([(("key",), payload)])
        result = tree.get(("key",))
    result["nested"]["value"] = "caller-mutation"
    assert (tree.get("key") if tree_kind == "authority" else tree.get(("key",))) == {
        "nested": {"value": "original"}
    }


@pytest.mark.parametrize("tree_kind", ["authority", "ordered"])
@pytest.mark.parametrize(
    "raw_value",
    [
        '{"fact":false,"fact":true}',
        '{"fact":NaN}',
        '{"fact":Infinity}',
        '{"fact":-Infinity}',
        '{"fact":1e999}',
        '{"fact":-1e999}',
    ],
)
def test_self_hashed_ambiguous_json_is_not_verified_material(tmp_path, tree_kind, raw_value):
    if tree_kind == "authority":
        raw = ('{"value":' + raw_value + "}").encode()
        digest = hashlib.sha256(raw).hexdigest()
        directory = tmp_path / "record-store"
        directory.mkdir()
        (directory / (digest + ".json")).write_bytes(raw)
        tree = AuthorityTree(tmp_path)
        with pytest.raises(ValueError):
            tree._read(digest)
    else:
        raw = ('{"entries":[{"k":["key"],"v":' + raw_value + "}]}").encode()
        digest = hashlib.sha256(raw).hexdigest()
        (tmp_path / (digest + ".json")).write_bytes(raw)
        tree = OrderedIndexTree(tmp_path, _key_cmp, leaf_size=8)
        ref = dict(file=digest + ".json", count=1, first=["key"], last=["key"], level=0)
        with pytest.raises(ValueError):
            tree.read(ref)


def test_public_scan_cannot_mutate_the_next_cached_read(tmp_path):
    tree = OrderedIndexTree(tmp_path, _key_cmp, leaf_size=8)
    tree.bulk_build([(("key",), {"nested": {"value": "original"}})])
    _, result = next(tree.scan(lower=("key",), upper=("key",), after=None, descending=False))
    result["nested"]["value"] = "changed"
    assert tree.get(("key",)) == {"nested": {"value": "original"}}


def test_authority_items_cannot_mutate_the_next_cached_read(tmp_path):
    tree = AuthorityTree(tmp_path)
    tree.put("key", {"nested": {"value": "original"}})
    _, result = next(tree.items())
    result["nested"]["value"] = "changed"
    assert tree.get("key") == {"nested": {"value": "original"}}


@pytest.mark.parametrize("tree_kind", ["authority", "ordered"])
def test_cache_uses_the_actual_json_container_shape(tmp_path, tree_kind):
    original = {"arguments": ("one", "two")}
    if tree_kind == "authority":
        tree = AuthorityTree(tmp_path)
        tree.put("key", original)
        result = tree.get("key")
    else:
        tree = OrderedIndexTree(tmp_path, _key_cmp, leaf_size=8)
        tree.bulk_build([(("key",), original)])
        result = tree.get(("key",))
    assert result == {"arguments": ["one", "two"]}
    assert original == {"arguments": ("one", "two")}


@pytest.mark.parametrize("budget", [True, -2, 0, "1"])
def test_invalid_budget_is_refused_before_reading(tmp_path, budget):
    from aitest.infrastructure.file_store.material_json import read_material_bytes

    with pytest.raises(ValueError):
        read_material_bytes(tmp_path / "does-not-exist", budget)


def test_bounded_material_read_does_not_read_a_whole_oversized_file(tmp_path, monkeypatch):
    import io
    from pathlib import Path

    from aitest.infrastructure.file_store.material_json import read_material_bytes

    reads = []

    class GrowingFile(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: GrowingFile(b"x" * 4096))
    with pytest.raises(ValueError):
        read_material_bytes(tmp_path / "growing", 16)
    assert reads == [17]


def test_authority_budget_is_enforced_before_cache_or_publication(tmp_path, monkeypatch):
    from aitest.infrastructure.file_store import sharded_records

    monkeypatch.setattr(sharded_records, "_MAX_NODE_BYTES", 256)
    tree = AuthorityTree(tmp_path)
    with pytest.raises(ValueError):
        tree.put("key", "x" * 512)
    assert tree.pointer is None and not tree._cache
    assert not (tmp_path / "record-store").exists()


@pytest.mark.parametrize("method", ["get", "items", "put"])
def test_authority_value_node_cannot_disguise_a_tree_directory(tmp_path, method):
    tree = AuthorityTree(tmp_path)
    tree.pointer = tree._write({"value": {"fact": True}})
    with pytest.raises(ValueError):
        if method == "get":
            tree.get("key")
        elif method == "items":
            list(tree.items())
        else:
            tree.put("key", "value")


def test_authority_hash_branch_cannot_hide_invalid_routing_keys(tmp_path):
    import json

    tree = AuthorityTree(tmp_path)
    child = tree._write({"entries": {}})
    raw = json.dumps({"children": {"not-a-hash-digit": child}}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / "record-store" / (digest + ".json")).write_bytes(raw)
    with pytest.raises(ValueError):
        tree._read(digest)
