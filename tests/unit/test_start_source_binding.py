"""C 侧 start 来源绑定解析：真实物化映射 + 独立重算 + 逐文件有界核对。

依据 AB-001 1.34 与 BC-001 第 6 节。本文件既用真实 `FileSourceSnapshotStore`
留真实文件证据，也用最小桩覆盖"物化未核实/映射缺失/摘要不符/字节不符/越界"等反例。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from aitest.application.execution.start_source_binding import (
    SourceBindingUnverified,
    StartSourceBindingResolver,
)
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.file_store.workspace import Workspace


class _Stub:
    """只实现 materialize 的端口桩，用于构造不可核实的物化结果。"""

    def __init__(self, result: object) -> None:
        self._result = result

    def materialize(self, snapshot_id: str, destination: str) -> object:
        return self._result


@pytest.fixture
def source(tmp_path: Path) -> Path:
    directory = tmp_path / "project-src"
    directory.mkdir()
    (directory / "main.py").write_text("print('hello')", encoding="utf-8")
    sub = directory / "pkg"
    sub.mkdir()
    (sub / "util.py").write_text("VALUE = 1", encoding="utf-8")
    return directory


@pytest.fixture
def store(tmp_path: Path) -> FileSourceSnapshotStore:
    Workspace(tmp_path)
    return FileSourceSnapshotStore(tmp_path)


def _entry(actual: str, content: bytes, relative: str = "main.py") -> dict[str, object]:
    return {
        "relative_path": relative,
        "actual_path": actual,
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


def _claim(entries: list[dict[str, object]]) -> str:
    encoded = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _materialized(snapshot_id: str, destination: str, **extra: object) -> dict[str, object]:
    return {
        "state": "materialized",
        "verified": True,
        "snapshot_id": snapshot_id,
        "destination": destination,
    } | extra


def _raw_entry(
    relative: object, actual: object, digest: object, size: object
) -> dict[str, object]:
    return {
        "relative_path": relative,
        "actual_path": actual,
        "sha256": digest,
        "size": size,
    }


def test_resolve_real_materialization_yields_source_binding_digest(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="prepare")
    destination = tmp_path / "workdir"

    result = StartSourceBindingResolver(store).resolve(
        snapshot_id=str(pinned["snapshot_id"]), destination=str(destination)
    )

    assert result["snapshot_id"] == pinned["snapshot_id"]
    assert result["workdir"] == destination.resolve().as_posix()
    paths = result["paths"]
    assert {item["relative_path"] for item in paths} == {"main.py", "pkg/util.py"}  # type: ignore[union-attr]
    for item in paths:  # type: ignore[union-attr]
        actual = Path(item["actual_path"])
        assert actual.is_relative_to(destination.resolve()) and actual.is_file()
    encoded = json.dumps(
        paths, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert result["source_binding_digest"] == "sha256:" + hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize(
    "result",
    [
        {"state": "rejected", "verified": False, "refused": ["main.py"]},
        {"state": "materialized", "verified": False},
        {"state": "materialized", "verified": True},
    ],
)
def test_unverified_materialization_is_refused(result: Mapping[str, object]) -> None:
    resolver = StartSourceBindingResolver(_Stub(result))  # type: ignore[arg-type]
    with pytest.raises(SourceBindingUnverified):
        resolver.resolve(snapshot_id="step-revision-1", destination="C:/work")


def test_missing_mapping_or_digest_is_refused(tmp_path: Path) -> None:
    actual = tmp_path / "main.py"
    actual.write_bytes(b"x")
    entry = _entry(actual.as_posix(), b"x")
    cases = [
        _materialized("s", str(tmp_path), content_digest=_claim([entry])),
        _materialized("s", str(tmp_path), paths=[entry]),
        _materialized("s", str(tmp_path), paths=[entry], content_digest="sha256:short"),
        _materialized("other", str(tmp_path), paths=[entry], content_digest=_claim([entry])),
        _materialized("s", "", paths=[entry], content_digest=_claim([entry])),
    ]
    for result in cases:
        resolver = StartSourceBindingResolver(_Stub(result))  # type: ignore[arg-type]
        with pytest.raises(SourceBindingUnverified):
            resolver.resolve(snapshot_id="s", destination=str(tmp_path))


def test_tampered_mapping_claim_is_refused(tmp_path: Path) -> None:
    actual = tmp_path / "main.py"
    actual.write_bytes(b"x")
    entry = _entry(actual.as_posix(), b"x")
    tampered = dict(entry) | {"size": 99}
    result = {
        "state": "materialized",
        "verified": True,
        "snapshot_id": "s",
        "destination": str(tmp_path),
        "paths": [tampered],
        "content_digest": _claim([entry]),
    }
    with pytest.raises(SourceBindingUnverified):
        StartSourceBindingResolver(_Stub(result)).resolve(  # type: ignore[arg-type]
            snapshot_id="s", destination=str(tmp_path)
        )


def test_bytes_differing_from_their_reference_are_refused(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="prepare")
    destination = tmp_path / "workdir"
    materialized = store.materialize(str(pinned["snapshot_id"]), str(destination))
    (destination / "main.py").write_bytes(b"print('tampered')")
    with pytest.raises(SourceBindingUnverified):
        StartSourceBindingResolver(_Stub(materialized)).resolve(  # type: ignore[arg-type]
            snapshot_id=str(pinned["snapshot_id"]), destination=str(destination)
        )


def test_mapped_path_escaping_the_workdir_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"x")
    entry = _entry(outside.as_posix(), b"x")
    result = {
        "state": "materialized",
        "verified": True,
        "snapshot_id": "s",
        "destination": (tmp_path / "workdir").as_posix(),
        "paths": [entry],
        "content_digest": _claim([entry]),
    }
    with pytest.raises(SourceBindingUnverified):
        StartSourceBindingResolver(_Stub(result)).resolve(  # type: ignore[arg-type]
            snapshot_id="s", destination=str(tmp_path / "workdir")
        )


_GOOD_DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "entry",
    [
        _raw_entry("", "x", _GOOD_DIGEST, 1),
        _raw_entry("../escape.py", "x", _GOOD_DIGEST, 1),
        _raw_entry("main.py", "x", "sha256:xyz", 1),
        _raw_entry("main.py", "x", _GOOD_DIGEST, True),
        _raw_entry("main.py", "x", _GOOD_DIGEST, -1),
    ],
)
def test_malformed_mapping_entries_are_refused(entry: dict[str, object], tmp_path: Path) -> None:
    result = _materialized(
        "s", str(tmp_path), paths=[entry], content_digest=_claim([entry])
    )
    with pytest.raises(SourceBindingUnverified):
        StartSourceBindingResolver(_Stub(result)).resolve(  # type: ignore[arg-type]
            snapshot_id="s", destination=str(tmp_path)
        )


@pytest.mark.parametrize(
    "snapshot_id,destination",
    [("", "C:/work"), ("s", " "), (None, "C:/work")],
)
def test_missing_start_inputs_are_refused(snapshot_id: object, destination: object) -> None:
    resolver = StartSourceBindingResolver(_Stub({}))  # type: ignore[arg-type]
    with pytest.raises(SourceBindingUnverified):
        resolver.resolve(snapshot_id=snapshot_id, destination=destination)  # type: ignore[arg-type]


def test_two_expected_paths_must_not_share_one_actual_file(tmp_path: Path) -> None:
    """两个期望来源不能解析到同一个实际文件（同一文件不能充当两个来源）。"""
    actual = tmp_path / "main.py"
    actual.write_bytes(b"x")
    first = _entry(actual.as_posix(), b"x", "a.py")
    second = _entry(actual.as_posix(), b"x", "b.py")
    result = _materialized(
        "s", str(tmp_path), paths=[first, second], content_digest=_claim([first, second])
    )
    with pytest.raises(SourceBindingUnverified):
        StartSourceBindingResolver(_Stub(result)).resolve(  # type: ignore[arg-type]
            snapshot_id="s", destination=str(tmp_path)
        )


def test_hard_linked_actual_file_is_refused(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    """实际来源文件不能靠硬链接与 workdir 外共享字节。"""
    pinned = store.pin(canonical_path=str(source), purpose="prepare")
    destination = tmp_path / "workdir"
    materialized = store.materialize(str(pinned["snapshot_id"]), str(destination))
    outside = tmp_path / "outside.py"
    try:
        os.link(destination / "main.py", outside)
    except OSError:  # 文件系统不支持硬链接：如实跳过，不当作通过
        pytest.skip("hard links are not supported on this filesystem")
    with pytest.raises(SourceBindingUnverified):
        StartSourceBindingResolver(_Stub(materialized)).resolve(  # type: ignore[arg-type]
            snapshot_id=str(pinned["snapshot_id"]), destination=str(destination)
        )


def test_workdir_must_be_the_requested_fixed_workdir(tmp_path: Path) -> None:
    """物化声明的目标目录必须等于请求的固定 workdir，不能只凭自述。"""
    actual = tmp_path / "main.py"
    actual.write_bytes(b"x")
    entry = _entry(actual.as_posix(), b"x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _materialized(
        "s", elsewhere.as_posix(), paths=[entry], content_digest=_claim([entry])
    )
    with pytest.raises(SourceBindingUnverified):
        StartSourceBindingResolver(_Stub(result)).resolve(  # type: ignore[arg-type]
            snapshot_id="s", destination=str(tmp_path / "requested")
        )


def test_frozen_expected_paths_must_be_covered_exactly(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    """给出冻结期望时，映射必须恰好覆盖：不缺项、不多项、不重复。"""
    pinned = store.pin(canonical_path=str(source), purpose="prepare")
    snapshot_id = str(pinned["snapshot_id"])
    resolver = StartSourceBindingResolver(store)

    covered = resolver.resolve(
        snapshot_id=snapshot_id,
        destination=str(tmp_path / "workdir-ok"),
        expected_relative_paths=["main.py", "pkg/util.py"],
    )
    assert {item["relative_path"] for item in covered["paths"]} == {  # type: ignore[union-attr]
        "main.py",
        "pkg/util.py",
    }

    for expected, destination in (
        (["main.py"], "workdir-missing"),
        (["main.py", "pkg/util.py", "extra.py"], "workdir-extra"),
        (["main.py", "main.py"], "workdir-duplicate"),
        ([""], "workdir-blank"),
    ):
        with pytest.raises(SourceBindingUnverified):
            resolver.resolve(
                snapshot_id=snapshot_id,
                destination=str(tmp_path / destination),
                expected_relative_paths=expected,
            )
