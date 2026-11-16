import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore


def test_scope_and_purpose_produce_distinct_snapshot_identities(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("x=1\n", encoding="utf-8")
    store = FileSourceSnapshotStore(tmp_path / "workspace")

    selected = store.pin(
        canonical_path=str(source),
        purpose="selected",
        selected_paths=("a.py",),
    )
    whole = store.pin(canonical_path=str(source), purpose="whole")

    # 字节完全相同，但范围/用途不同 → 不同快照身份与各自清单（A-15）。
    assert selected["snapshot_id"] != whole["snapshot_id"]
    assert selected["selected_paths"] == ["a.py"]
    assert whole["selected_paths"] == []

    # 整目录新增 b.py 必须能被整目录快照检出。
    (source / "b.py").write_text("x=2\n", encoding="utf-8")
    drift_whole = store.detect_changes(str(whole["snapshot_id"]))
    assert drift_whole["state"] == "changed"
    assert drift_whole["added"] == ["b.py"]

    # 单文件快照的范围不含 b.py，仍判 unchanged。
    drift_selected = store.detect_changes(str(selected["snapshot_id"]))
    assert drift_selected["state"] == "unchanged"


def test_identical_scope_pin_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("x=1\n", encoding="utf-8")
    store = FileSourceSnapshotStore(tmp_path / "workspace")
    first = store.pin(canonical_path=str(source), purpose="p1")
    second = store.pin(canonical_path=str(source), purpose="p1")
    assert first["snapshot_id"] == second["snapshot_id"]


def test_purpose_change_with_same_bytes_uses_separate_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("x=1\n", encoding="utf-8")
    store = FileSourceSnapshotStore(tmp_path / "workspace")
    run_snapshot = store.pin(canonical_path=str(source), purpose="run")
    publish_snapshot = store.pin(
        canonical_path=str(source), purpose="publication"
    )
    assert run_snapshot["snapshot_id"] != publish_snapshot["snapshot_id"]
    # blob 内容去重：两个清单引用同一 blob 存储对象。
    assert run_snapshot["files"][0]["sha256"] == (
        publish_snapshot["files"][0]["sha256"]
    )


def test_exclusion_rule_change_changes_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("x=1\n", encoding="utf-8")
    gen = source / "generated"
    gen.mkdir()
    (gen / "g.txt").write_text("gen\n", encoding="utf-8")
    store = FileSourceSnapshotStore(tmp_path / "workspace")

    without_rule = store.pin(canonical_path=str(source), purpose="p")
    with_rule = store.pin(
        canonical_path=str(source),
        purpose="p",
        exclusion_rules=("generated",),
    )
    assert without_rule["snapshot_id"] != with_rule["snapshot_id"]
    assert len(with_rule["files"]) == 1
    drift = store.detect_changes(str(with_rule["snapshot_id"]))
    assert drift["state"] == "unchanged"
