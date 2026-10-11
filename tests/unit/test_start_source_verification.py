"""来源核验事实（P1-FR08 / P1-AC13）：装配层 provider 必须**非空转**。

覆盖三件事：

1. **真实观测**：`observed_source_digest` 取自 `verify_pinned` 重新核对后的身份，因此固定记录
   身份与冻结期望不一致时必须判非 `VERIFIED`（空转实现会失败）；
2. **入口必须在固定 workdir 内**：没有可用入口路径时直接拒绝；
3. **登记模块的实际加载来源**：从项目登记的 `source_paths` 按探针自己的 sys.path（物化根）
   推导可导入名并**真的导入**，`observed_import_ref` 落在物化根内；推导不出时退回 `SYNTAX`，
   不伪造结论。推导规则本身是纯函数，另有专门用例。

`require_verified_source` 对非 `VERIFIED` 一律阻塞，故"判非 VERIFIED"即"不得启动"。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from aitest.application.execution.start_source_binding import SourceBindingUnverified
from aitest.application.project.serialization import project_to_payload
from aitest.bootstrap import (
    _pattern_relative_dir,
    _source_verification_provider,
)
from aitest.domain.execution.sources import SourceCheckType, SourceVerificationState
from aitest.domain.project.context import LocalProject

IDENTITY = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
PROJECT_ID = "project-a"


class _Snapshots:
    """最小 `SourceSnapshotPort` 替身：只实现 provider 用到的 `verify_pinned`。"""

    def __init__(self, identity: str) -> None:
        self._identity = identity

    def verify_pinned(self, snapshot_id: str) -> dict[str, object]:
        return {"content_identity": self._identity}


@dataclass(frozen=True, slots=True)
class _Record:
    """合规的仓储信封：`require_scoped_record` 会核对这三项与载荷类型。"""

    aggregate_kind: str
    record_id: str
    revision: int
    payload: dict[str, object]


class _Reader:
    """最小 `RecordReader` 替身：只按项目聚合返回一条记录。"""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> _Record:
        return _Record(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=revision,
            payload=self._payload,
        )


def _project(*source_paths: str) -> LocalProject:
    return LocalProject(
        local_project_id=PROJECT_ID,
        workspace_id="ws-1",
        name="example",
        goal="exercise verification",
        created_at_commit="commit-1",
        modules=(
            # `Module` 由 project_to_payload/from_payload 往返；此处只关心 source_paths。
            __import__(
                "aitest.domain.project.context", fromlist=["Module"]
            ).Module(
                module_id="module-1",
                project_id=PROJECT_ID,
                name="module one",
                responsibility="r",
                interface_note="i",
                source_paths=source_paths,
            ),
        ),
    )


def _prepared(identity: str) -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "project_revision": 1,
        "snapshot": {"snapshot_id": "snap-1", "content_identity": identity},
        "environment": {
            "environment_id": "env-1",
            "revision": 1,
            "resolution": {"executable_path": sys.executable},
        },
        "plan_revision": {"revision_id": "plan-1", "revision_no": 1, "digest": "sha256:plan"},
        "execution_source": {"adapter_versions": {"python": "python-checks/1.0"}},
        "rule_versions": [{"rule_id": "rule-1", "revision": 1}],
    }


def _workdir(tmp_path: Path, *, with_module: bool = True) -> tuple[str, dict[str, object]]:
    workdir = tmp_path / "fixed-workdir"
    workdir.mkdir()
    entry = workdir / "entry.py"
    entry.write_text("value = 1\n", encoding="utf-8")
    if with_module:
        package = workdir / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    admitted: dict[str, object] = {
        "paths": [{"actual_path": str(entry), "sha256": "sha256:entry", "size": 10}]
    }
    return str(workdir), admitted


def _provider(identity: str, *source_paths: str):
    return _source_verification_provider(
        _Snapshots(identity), _Reader(project_to_payload(_project(*source_paths)))
    )


def test_matching_identity_and_entry_are_verified(tmp_path: Path) -> None:
    workdir, admitted = _workdir(tmp_path)

    verification = _provider(IDENTITY, "pkg")(workdir, admitted, _prepared(IDENTITY))

    assert verification.state is SourceVerificationState.VERIFIED
    assert verification.gap_ids == ()
    assert verification.observed_source_digest == IDENTITY
    assert verification.observed_interpreter_ref == sys.executable


def test_a_registered_module_is_actually_imported_from_the_materialized_copy(
    tmp_path: Path,
) -> None:
    workdir, admitted = _workdir(tmp_path)

    verification = _provider(IDENTITY, "pkg")(workdir, admitted, _prepared(IDENTITY))

    assert verification.state is SourceVerificationState.VERIFIED
    # 真的导入了登记模块，且实际加载路径落在固定 workdir 内。
    assert verification.observed_import_ref is not None
    assert Path(verification.observed_import_ref).is_relative_to(Path(workdir).resolve())


def test_without_a_derivable_module_the_check_stays_on_syntax(tmp_path: Path) -> None:
    """推导不出可导入名时不伪造「加载来源已核对」，退回只覆盖语法的 SYNTAX。"""

    workdir, admitted = _workdir(tmp_path, with_module=False)

    verification = _provider(IDENTITY, "src/nowhere/**")(workdir, admitted, _prepared(IDENTITY))

    assert verification.state is SourceVerificationState.VERIFIED
    assert verification.observed_import_ref is None


def test_a_different_pinned_identity_is_not_verified(tmp_path: Path) -> None:
    """若 provider 只是把冻结值抄一遍，这条会失败——它必须真的用观测值。"""

    workdir, admitted = _workdir(tmp_path)

    verification = _provider(OTHER, "pkg")(workdir, admitted, _prepared(IDENTITY))

    assert verification.state is not SourceVerificationState.VERIFIED
    assert "source_digest_mismatch" in verification.gap_ids


def test_an_entry_outside_the_fixed_workdir_is_refused(tmp_path: Path) -> None:
    workdir, _ = _workdir(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(SourceBindingUnverified):
        _provider(IDENTITY, "pkg")(
            workdir,
            {"paths": [{"actual_path": str(outside), "sha256": "sha256:x", "size": 10}]},
            _prepared(IDENTITY),
        )


def test_an_empty_path_mapping_is_refused(tmp_path: Path) -> None:
    workdir, _ = _workdir(tmp_path)

    with pytest.raises(SourceBindingUnverified):
        _provider(IDENTITY, "pkg")(workdir, {"paths": []}, _prepared(IDENTITY))


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("src/ticket/**", "src/ticket"),
        ("src/ticket", "src/ticket"),
        ("tests/ticket/*", "tests/ticket"),
        ("pkg", "pkg"),
        ("src/../etc", None),
        ("/abs/path", None),
        ("src/*/ticket", None),
        ("", None),
        ("   ", None),
    ],
)
def test_source_path_patterns_fold_to_relative_directories(
    pattern: str, expected: str | None
) -> None:
    """`source_paths` 是「变更→模块」的模式，不是模块名；只接受可确定相对目录的形态。"""

    folded = _pattern_relative_dir(pattern)

    assert (None if folded is None else folded.as_posix()) == expected


def test_check_type_is_load_and_the_constant_is_exported() -> None:
    assert SourceCheckType.LOAD.value == "load"
