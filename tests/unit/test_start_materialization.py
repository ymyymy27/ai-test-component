"""start 物化与工作目录解析：真实文件证据 + 越界/覆盖/多键反例。"""

from __future__ import annotations

from pathlib import Path

import pytest

from aitest.application.execution.start_materialization import (
    StartMaterializationBlocked,
    StartMaterializer,
)
from aitest.application.execution.start_source_binding import SourceBindingUnverified
from aitest.contracts.prepared_run import ExecutionSourceBinding
from aitest.infrastructure.adapters.source_snapshot import (
    FileSourceSnapshotStore,
    SnapshotError,
)
from aitest.infrastructure.file_store.workspace import Workspace

_DIGEST = "sha256:" + "a" * 64


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


def _binding(**overrides: object) -> ExecutionSourceBinding:
    values: dict[str, object] = {
        "registered_entry": "public-command",
        "entry_arguments": ("tests/acceptance",),
        "cwd_mapping": "workdir:pkg",
        "allowed_env_keys": ("PYTHONPATH",),
        "secret_refs": ("MODEL_API_KEY",),
        "test_config_ref": "config:default@1",
        "adapter_versions": {"command": "1.0.0"},
        "resolved_input_digest": _DIGEST,
    }
    values.update(overrides)
    return ExecutionSourceBinding(**values)  # type: ignore[arg-type]


def test_materializes_into_the_fixed_workdir_and_admits(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="prepare")
    materializer = StartMaterializer(store, workspace_root=tmp_path)

    admitted = materializer.materialize(
        snapshot_id=str(pinned["snapshot_id"]), run_id="run-1", binding=_binding()
    )

    workdir = tmp_path.resolve() / "workdirs" / "run-1"
    assert admitted["workdir"] == workdir.as_posix()
    assert workdir.is_dir()
    assert admitted["cwd"] == (workdir / "pkg").resolve().as_posix()
    assert admitted["adapter_kind"] == "command"
    assert admitted["arguments"] == ("tests/acceptance",)
    assert str(admitted["source_binding_digest"]).startswith("sha256:")
    relative = {item["relative_path"] for item in admitted["paths"]}  # type: ignore[union-attr]
    assert relative == {"main.py", "pkg/util.py"}
    for item in admitted["paths"]:  # type: ignore[union-attr]
        assert Path(item["actual_path"]).is_file()


def test_workdir_must_be_one_safe_segment_inside_workdirs(tmp_path: Path) -> None:
    materializer = StartMaterializer(object(), workspace_root=tmp_path)  # type: ignore[arg-type]
    for run_id in ("", "  ", ".", "..", "a/b", "a\\b", "C:evil"):
        with pytest.raises(StartMaterializationBlocked):
            materializer.workdir(run_id)
    assert materializer.workdir("run-1") == (tmp_path.resolve() / "workdirs" / "run-1")


def test_materialization_is_idempotent_and_never_overwrites(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="prepare")
    materializer = StartMaterializer(store, workspace_root=tmp_path)
    snapshot_id = str(pinned["snapshot_id"])
    first = materializer.materialize(snapshot_id=snapshot_id, run_id="run-1", binding=_binding())
    # 跨入口重传：同一快照读取同一冻结结果，不重复物化、不覆盖
    second = materializer.materialize(snapshot_id=snapshot_id, run_id="run-1", binding=_binding())
    assert second["source_binding_digest"] == first["source_binding_digest"]
    assert second["paths"] == first["paths"]
    # 目标字节被改动后不再匹配冻结清单：端口按既有语义拒绝覆盖
    (tmp_path.resolve() / "workdirs" / "run-1" / "main.py").write_bytes(b"tampered")
    with pytest.raises(SnapshotError):
        materializer.materialize(snapshot_id=snapshot_id, run_id="run-1", binding=_binding())


def test_ambiguous_adapter_key_blocks_before_writing(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="prepare")
    materializer = StartMaterializer(store, workspace_root=tmp_path)
    with pytest.raises(SourceBindingUnverified):
        materializer.materialize(
            snapshot_id=str(pinned["snapshot_id"]),
            run_id="run-2",
            binding=_binding(adapter_versions={"command": "1.0.0", "http": "1.0.0"}),
        )
    assert not (tmp_path.resolve() / "workdirs" / "run-2").exists()


def test_unknown_snapshot_is_refused(tmp_path: Path) -> None:
    Workspace(tmp_path)
    materializer = StartMaterializer(FileSourceSnapshotStore(tmp_path), workspace_root=tmp_path)
    with pytest.raises((SnapshotError, ValueError)):
        materializer.materialize(
            snapshot_id="missing-snapshot", run_id="run-3", binding=_binding()
        )
