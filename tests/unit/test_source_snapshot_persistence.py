"""源码快照记录：实际来源 → 正式快照的落盘那一跳（检查文档 B-04）。

依据：`AB-001` 第 11.1（形式互斥）、11.2（字段）、11.3（内容身份）、11.4（端口语义）；
`docs/文档-feix-a/B包/02-B包施工检查项清单.md` 第 41 节。

本文件锁定四件事：

1. **往返**：`git` 与 `plain` 两种形态都能按准确修订落盘并读回；
2. **形式互斥落到字节上**：`git` 的 payload 里**没有** `plain_manifest_digest` 键，
   `plain` 的 payload 里**没有** git 键（不是写 `None`）；
3. **身份随记录落盘并被核对**：记录里的 `content_identity` 与按 payload 重算的结果一致；
   不一致时读回**报错**，不退化成"读到了但内容对不上"；
4. **用途只有两个**：`analysis` / `prepare`，其他值拒绝。

不使用 `tmp_path`：受限执行环境拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from aitest.application.planning.substrate import transaction
from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.project.persistence import (
    load_source_snapshot,
    save_source_snapshot,
)
from aitest.domain.project.context import (
    SourceFileDigest,
    SourceForm,
    SourceManifest,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

PROJECT_ID = "project-source"
OTHER_PROJECT = "project-other"


@dataclass(frozen=True, slots=True)
class _Stack:
    unit_of_work: PortsUnitOfWork
    reader: PortsRecordReader


def _start(root: Path) -> _Stack:
    raw = FileUnitOfWork(root)
    repository = raw.repo
    return _Stack(
        unit_of_work=PortsUnitOfWork(raw, repository=repository),
        reader=PortsRecordReader(repository),
    )


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) / f"aitest-snapshot-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _file(relative_path: str = "src/app.py", **overrides: object) -> SourceFileDigest:
    base: dict[str, object] = {
        "relative_path": relative_path,
        "size": 12,
        "content_digest": "sha256:abc",
    }
    return SourceFileDigest(**(base | overrides))  # type: ignore[arg-type]


def _plain(**overrides: object) -> SourceManifest:
    base: dict[str, object] = {
        "source_scope": "选定模块目录",
        "source_form": SourceForm.PLAIN,
        "manifest_digest": "sha256:manifest",
        "files": (_file(),),
    }
    return SourceManifest(**(base | overrides))  # type: ignore[arg-type]


def _git(**overrides: object) -> SourceManifest:
    base: dict[str, object] = {
        "source_scope": "选定模块目录",
        "source_form": SourceForm.GIT,
        "git_base_commit": "abc123",
        "git_diff_digest": "sha256:diff",
        "files": (_file(),),
    }
    return SourceManifest(**(base | overrides))  # type: ignore[arg-type]


# ------------------------------------------------------------------ 往返


def test_a_plain_snapshot_survives_a_restart(workspace_root: Path) -> None:
    first = _start(workspace_root)
    save_source_snapshot(
        _plain(),
        project_id=PROJECT_ID,
        snapshot_id="snap-1",
        purpose="prepare",
        unit_of_work=first.unit_of_work,
    )
    restarted = _start(workspace_root)
    loaded = load_source_snapshot(
        restarted.reader, project_id=PROJECT_ID, snapshot_id="snap-1", revision=1
    )
    assert loaded == _plain()


def test_a_git_snapshot_survives_a_restart(workspace_root: Path) -> None:
    first = _start(workspace_root)
    save_source_snapshot(
        _git(),
        project_id=PROJECT_ID,
        snapshot_id="snap-git",
        purpose="analysis",
        unit_of_work=first.unit_of_work,
    )
    restarted = _start(workspace_root)
    loaded = load_source_snapshot(
        restarted.reader, project_id=PROJECT_ID, snapshot_id="snap-git", revision=1
    )
    assert loaded == _git()
    assert loaded.git_base_commit == "abc123"


def test_the_record_carries_the_content_identity(workspace_root: Path) -> None:
    """身份随记录落盘：读取方据此核对，不必重新扫描目录（§11.4）。"""
    stack = _start(workspace_root)
    save_source_snapshot(
        _plain(),
        project_id=PROJECT_ID,
        snapshot_id="snap-1",
        purpose="prepare",
        unit_of_work=stack.unit_of_work,
    )
    record = stack.reader.read(
        aggregate_kind="source_snapshot", record_id="snap-1", revision=1
    )
    assert str(record.payload["content_identity"]).startswith("sha256:")
    assert record.payload["purpose"] == "prepare"


def test_files_are_stored_in_path_order(workspace_root: Path) -> None:
    """清单按路径升序落盘：身份与存储都不依赖目录遍历顺序。"""
    stack = _start(workspace_root)
    save_source_snapshot(
        _plain(files=(_file("b.py"), _file("a.py"))),
        project_id=PROJECT_ID,
        snapshot_id="snap-1",
        purpose="prepare",
        unit_of_work=stack.unit_of_work,
    )
    record = stack.reader.read(
        aggregate_kind="source_snapshot", record_id="snap-1", revision=1
    )
    paths = [item["relative_path"] for item in record.payload["files"]]
    assert paths == ["a.py", "b.py"]


# ------------------------------------------------------------------ 形式互斥落到字节


def test_the_git_payload_truly_omits_the_plain_digest(workspace_root: Path) -> None:
    """`git` 形态**真正省略**另一个形态的键（不是写 `None`）。"""
    stack = _start(workspace_root)
    save_source_snapshot(
        _git(),
        project_id=PROJECT_ID,
        snapshot_id="snap-git",
        purpose="prepare",
        unit_of_work=stack.unit_of_work,
    )
    payload = stack.reader.read(
        aggregate_kind="source_snapshot", record_id="snap-git", revision=1
    ).payload
    assert "plain_manifest_digest" not in payload
    assert payload["git_base_commit"] == "abc123"
    assert payload["git_diff_digest"] == "sha256:diff"


def test_the_plain_payload_truly_omits_the_git_keys(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    save_source_snapshot(
        _plain(),
        project_id=PROJECT_ID,
        snapshot_id="snap-1",
        purpose="prepare",
        unit_of_work=stack.unit_of_work,
    )
    payload = stack.reader.read(
        aggregate_kind="source_snapshot", record_id="snap-1", revision=1
    ).payload
    assert "git_base_commit" not in payload
    assert "git_diff_digest" not in payload
    assert payload["plain_manifest_digest"] == "sha256:manifest"


# ------------------------------------------------------------------ 身份核对


def test_a_stored_identity_that_does_not_match_the_bytes_is_refused(
    workspace_root: Path,
) -> None:
    """记录里被改过的 `content_identity` 必须让读回**报错**。

    直接写入一条身份与字节不符的记录（模拟被外部改动），
    读回时必须拒绝——否则调用方会拿一份自相矛盾的依据去做核对。
    """
    stack = _start(workspace_root)
    with transaction(stack.unit_of_work, PROJECT_ID) as tx:
        tx.stage_record(
            aggregate_kind="source_snapshot",
            record_id="snap-bad",
            expected_revision=None,
            payload={
                "project_id": PROJECT_ID,
                "snapshot_id": "snap-bad",
                "purpose": "prepare",
                "source_scope": "选定模块目录",
                "source_form": "plain",
                "content_identity": "sha256:tampered",
                "files": [
                    {
                        "relative_path": "src/app.py",
                        "size": 12,
                        "content_digest": "sha256:abc",
                    }
                ],
                "exclusion_rules": [],
                "refetch_dependencies": [],
                "refetch_scope": None,
                "plain_manifest_digest": "sha256:manifest",
            },
        )
        tx.commit()

    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="does not match its bytes"):
        load_source_snapshot(
            restarted.reader, project_id=PROJECT_ID, snapshot_id="snap-bad", revision=1
        )


def test_a_record_form_that_contradicts_its_keys_is_refused(
    workspace_root: Path,
) -> None:
    """声明 `git` 却带清单摘要：形态互斥在**读回**也要守。"""
    stack = _start(workspace_root)
    with transaction(stack.unit_of_work, PROJECT_ID) as tx:
        tx.stage_record(
            aggregate_kind="source_snapshot",
            record_id="snap-mixed",
            expected_revision=None,
            payload={
                "project_id": PROJECT_ID,
                "snapshot_id": "snap-mixed",
                "purpose": "prepare",
                "source_scope": "选定模块目录",
                "source_form": "git",
                "content_identity": "sha256:x",
                "files": [],
                "exclusion_rules": [],
                "refetch_dependencies": [],
                "refetch_scope": None,
                "git_base_commit": "abc123",
                "git_diff_digest": "sha256:diff",
                "plain_manifest_digest": "sha256:manifest",
            },
        )
        tx.commit()

    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="must omit plain_manifest_digest"):
        load_source_snapshot(
            restarted.reader,
            project_id=PROJECT_ID,
            snapshot_id="snap-mixed",
            revision=1,
        )


# ------------------------------------------------------------------ 写入校验


def test_only_the_two_contract_purposes_are_accepted(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    for purpose in ("analysis", "prepare"):
        save_source_snapshot(
            _plain(),
            project_id=PROJECT_ID,
            snapshot_id=f"snap-{purpose}",
            purpose=purpose,
            unit_of_work=stack.unit_of_work,
        )
    with pytest.raises(ValueError, match="unknown snapshot purpose"):
        save_source_snapshot(
            _plain(),
            project_id=PROJECT_ID,
            snapshot_id="snap-bad-purpose",
            purpose="regression",
            unit_of_work=stack.unit_of_work,
        )


def test_an_empty_snapshot_id_is_refused(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    with pytest.raises(ValueError, match="snapshot_id"):
        save_source_snapshot(
            _plain(),
            project_id=PROJECT_ID,
            snapshot_id="  ",
            purpose="prepare",
            unit_of_work=stack.unit_of_work,
        )


def test_a_cross_project_snapshot_read_is_refused(workspace_root: Path) -> None:
    """与其余记录一致：跨项目读回被拒（B-12 的同一口径）。"""
    stack = _start(workspace_root)
    save_source_snapshot(
        _plain(),
        project_id=PROJECT_ID,
        snapshot_id="snap-1",
        purpose="prepare",
        unit_of_work=stack.unit_of_work,
    )
    restarted = _start(workspace_root)
    with pytest.raises(ValueError, match="another project"):
        load_source_snapshot(
            restarted.reader,
            project_id=OTHER_PROJECT,
            snapshot_id="snap-1",
            revision=1,
        )


def test_the_same_snapshot_id_cannot_be_overwritten(workspace_root: Path) -> None:
    """同一 id 再存一次是修订冲突，不是静默覆盖。"""
    from aitest.application.planning.substrate import ConcurrentEditError

    stack = _start(workspace_root)
    save_source_snapshot(
        _plain(),
        project_id=PROJECT_ID,
        snapshot_id="snap-1",
        purpose="prepare",
        unit_of_work=stack.unit_of_work,
    )
    with pytest.raises(ConcurrentEditError):
        save_source_snapshot(
            _plain(manifest_digest="sha256:other"),
            project_id=PROJECT_ID,
            snapshot_id="snap-1",
            purpose="prepare",
            unit_of_work=stack.unit_of_work,
        )
