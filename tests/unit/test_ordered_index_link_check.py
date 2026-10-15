"""One metadata observation per ancestor without weakening fresh path checks."""

import os
import stat
from types import SimpleNamespace

import pytest

from aitest.infrastructure import path_compat
from aitest.infrastructure.file_store.ordered_index import OrderedIndexTree


@pytest.mark.parametrize("metadata, expected", [
    ({"st_mode": stat.S_IFDIR}, False),
    ({"st_mode": stat.S_IFLNK}, True),
    ({"st_mode": stat.S_IFDIR, "st_reparse_tag": 0xA0000003}, True),
    ({"st_mode": stat.S_IFREG, "st_reparse_tag": 0}, False),
    ({"st_mode": stat.S_IFDIR, "st_file_attributes": 0x400}, True),
])
def test_one_metadata_read_checks_both_link_kinds(monkeypatch, metadata, expected):
    calls = []

    def observed(path):
        calls.append(path)
        return SimpleNamespace(**metadata)

    monkeypatch.setattr(path_compat.os, "lstat", observed)
    assert path_compat.is_link("candidate") is expected
    assert calls == ["candidate"]


def test_missing_path_is_not_a_link_but_unreadable_path_blocks(monkeypatch):
    def missing(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(path_compat.os, "lstat", missing)
    assert not path_compat.is_link("candidate")

    def unreadable(path):
        raise PermissionError(path)

    monkeypatch.setattr(path_compat.os, "lstat", unreadable)
    with pytest.raises(PermissionError):
        path_compat.is_link("candidate")


def test_index_checks_each_ancestor_again_after_a_directory_becomes_a_junction(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    import subprocess

    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.mkdir()
    OrderedIndexTree._reject_links(alias / "missing-child")
    alias.rmdir()
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                            capture_output=True)
    assert result.returncode == 0
    try:
        assert path_compat.is_link(alias)
        with pytest.raises(ValueError, match="filesystem link"):
            OrderedIndexTree._reject_links(alias / "missing-child")
        with pytest.raises(ValueError, match="filesystem link"):
            OrderedIndexTree._reject_link_paths((target / "child", alias / "missing-child"))
    finally:
        alias.rmdir()


def test_batch_deduplicates_shared_ancestors_only_for_one_check(tmp_path, monkeypatch):
    calls = []
    original = path_compat.is_link

    def observed(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(path_compat, "is_link", observed)
    paths = (tmp_path / "a" / "leaf", tmp_path / "b" / "leaf")
    expected = set(part for path in paths for part in (path, *path.parents))
    OrderedIndexTree._reject_link_paths(paths)
    assert set(calls) == expected and len(calls) == len(expected)
    OrderedIndexTree._reject_link_paths(paths)
    assert len(calls) == 2 * len(expected)
