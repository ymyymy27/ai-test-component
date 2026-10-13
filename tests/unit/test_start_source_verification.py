"""来源核验 provider 的边界（P1-FR08 / P1-AC13）：装配层 provider 必须**非空转**。

本文件只测 provider 的**边界拒绝**与纯函数推导；"真实默认链贯通"与"真实语法反例"由
`tests/unit/test_default_start_source_chain.py` 用真实核心/UOW/固定字节覆盖，逐模块与语法
判定规则由 `tests/unit/test_source_checks.py` 覆盖。

2026-10-12 更正：旧夹具用 `snapshot.snapshot_id` 与"技术清单里带 content_identity"的替身，
两者都不是合同形状（`contracts.prepared_run.SnapshotRef` 是 `source_snapshot_id`，
技术固定清单没有 `content_identity`）。这里改用合同的业务记录形状 + 技术清单形状，
并断言业务 ID **不会**被当成技术 ID 使用（C-15）。

`require_verified_source` 对非 `VERIFIED` 一律阻塞，故"判非 VERIFIED"即"不得启动"。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from aitest.application.execution.start_source_binding import SourceBindingUnverified
from aitest.application.project.serialization import project_to_payload
from aitest.bootstrap import (
    _pattern_relative_dir,
    _source_verification_provider,
)
from aitest.domain.execution.sources import SourceCheckType, SourceVerificationState
from aitest.domain.project.context import (
    SourceFileDigest,
    SourceForm,
    SourceManifest,
    source_content_identity,
)

PROJECT_ID = "project-a"
BUSINESS_ID = "source-business-1"
TECHNICAL_ID = "snap-technical-1"


class _Snapshots:
    """最小 `SourceSnapshotPort` 替身：只实现 provider 用到的 `verify_pinned`。

    `verify_pinned` 返回**技术固定清单**形状（B 合同里没有 `content_identity` 这个键）。
    """

    def __init__(self, files: list[dict[str, object]]) -> None:
        self._files = files
        self.seen: list[str] = []

    def verify_pinned(self, snapshot_id: str) -> dict[str, object]:
        self.seen.append(snapshot_id)
        return {"snapshot_id": snapshot_id, "purpose": "prepare", "files": list(self._files)}


class _SourceAnalysis:
    """最小 `SourceAnalysisService` 替身：只实现 `verify_saved`（业务记录 → 技术身份）。"""

    def __init__(self, saved: dict[str, object]) -> None:
        self._saved = saved
        self.seen: list[tuple[str, str, int]] = []

    def verify_saved(
        self, *, project_id: str, snapshot_id: str, revision: int
    ) -> dict[str, object]:
        self.seen.append((project_id, snapshot_id, revision))
        if snapshot_id != BUSINESS_ID:
            raise ValueError("saved business snapshot identity cannot be verified")
        return dict(self._saved)


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


def _project(*source_paths: str) -> Any:
    from aitest.domain.project.context import LocalProject, Module

    return LocalProject(
        local_project_id=PROJECT_ID,
        workspace_id="ws-1",
        name="example",
        goal="exercise verification",
        created_at_commit="commit-1",
        modules=(
            Module(
                module_id="module-1",
                project_id=PROJECT_ID,
                name="module one",
                responsibility="r",
                interface_note="i",
                source_paths=source_paths,
            ),
        ),
    )


def _identity(files: tuple[dict[str, object], ...]) -> str:
    """按 B 的唯一口径从真实技术清单重算内容身份（与 provider 同一函数）。"""

    return source_content_identity(
        SourceManifest(
            source_scope=".",
            source_form=SourceForm.PLAIN,
            manifest_digest="declared-plain-manifest",
            files=tuple(
                SourceFileDigest(
                    relative_path=str(item["relative_path"]),
                    size=int(str(item["size"])),
                    content_digest="sha256:" + str(item["sha256"]),
                )
                for item in files
            ),
            exclusion_rules=(".git",),
        )
    )


def _file_entry(path: Path, relative: str) -> dict[str, object]:
    """技术固定清单里的一条真实身份（摘要按**实际字节**计算，与探针报告同源）。"""

    import hashlib

    content = path.read_bytes()
    return {
        "relative_path": relative,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _saved_record(identity: str, files: tuple[dict[str, object], ...]) -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "snapshot_id": BUSINESS_ID,
        "purpose": "prepare",
        "source_scope": ".",
        "source_form": "plain",
        "content_identity": identity,
        "files": [
            {
                "relative_path": str(item["relative_path"]),
                "size": int(str(item["size"])),
                "content_digest": "sha256:" + str(item["sha256"]),
            }
            for item in files
        ],
        "exclusion_rules": [".git"],
        "refetch_dependencies": [],
        "refetch_scope": None,
        "plain_manifest_digest": "declared-plain-manifest",
        "pinned_snapshot_id": TECHNICAL_ID,
    }


def _prepared(identity: str) -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "project_revision": 1,
        "snapshot": {
            "source_snapshot_id": BUSINESS_ID,
            "purpose": "prepare",
            "content_identity": identity,
            "record_revision": 1,
        },
        "environment": {
            "environment_id": "env-1",
            "revision": 1,
            "resolution": {"executable_path": sys.executable},
        },
        "plan_revision": {"revision_id": "plan-1", "revision_no": 1, "digest": "sha256:plan"},
        "execution_source": {
            "adapter_versions": {"python": "python-checks/1.0"},
            "entry_arguments": ["entry.py"],
        },
        "rule_versions": [{"rule_id": "rule-1", "revision": 1}],
    }


def _workdir(
    tmp_path: Path, *, with_module: bool = True, entry_source: str = "value = 1\n"
) -> tuple[str, dict[str, object]]:
    workdir = tmp_path / "fixed-workdir"
    workdir.mkdir()
    entry = workdir / "entry.py"
    entry.write_text(entry_source, encoding="utf-8")
    if with_module:
        package = workdir / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    admitted: dict[str, object] = {
        "paths": [
            {
                "relative_path": "entry.py",
                "actual_path": str(entry),
                "sha256": "sha256:entry",
                "size": entry.stat().st_size,
            }
        ]
    }
    return str(workdir), admitted


def _provider(
    tmp_path: Path,
    *,
    identity: str | None = None,
    with_module: bool = True,
    entry_source: str = "value = 1\n",
):
    workdir, admitted = _workdir(
        tmp_path, with_module=with_module, entry_source=entry_source
    )
    files: tuple[dict[str, object], ...] = (
        _file_entry(Path(workdir) / "entry.py", "entry.py"),
    )
    if with_module:
        files = (
            *files,
            _file_entry(Path(workdir) / "pkg" / "__init__.py", "pkg/__init__.py"),
        )
    computed = _identity(files)
    snapshots = _Snapshots(list(files))
    analysis = _SourceAnalysis(_saved_record(computed, files))
    provider = _source_verification_provider(
        snapshots,  # type: ignore[arg-type]
        _Reader(project_to_payload(_project("pkg"))),  # type: ignore[arg-type]
        analysis,  # type: ignore[arg-type]
    )
    prepared = _prepared(identity if identity is not None else computed)
    return provider, workdir, admitted, prepared, snapshots, analysis, computed


def test_the_business_record_is_resolved_to_the_technical_pinned_identity(
    tmp_path: Path,
) -> None:
    provider, workdir, admitted, prepared, snapshots, analysis, computed = _provider(tmp_path)

    verification = provider(workdir, admitted, prepared)

    assert verification.state is SourceVerificationState.VERIFIED
    assert verification.gap_ids == ()
    assert verification.observed_source_digest == computed
    # 业务 ID → 技术 ID 的映射必须真的发生，且物化端口只收到技术 ID（C-15）。
    assert analysis.seen == [(PROJECT_ID, BUSINESS_ID, 1)]
    assert snapshots.seen == [TECHNICAL_ID]


def test_a_registered_module_is_actually_imported_from_the_materialized_copy(
    tmp_path: Path,
) -> None:
    provider, workdir, admitted, prepared, *_ = _provider(tmp_path)

    verification = provider(workdir, admitted, prepared)

    assert verification.state is SourceVerificationState.VERIFIED
    # 真的导入了登记模块，且实际加载路径落在固定 workdir 内。
    assert verification.observed_import_ref is not None
    assert Path(verification.observed_import_ref).is_relative_to(Path(workdir).resolve())


def test_without_a_derivable_module_the_check_is_a_real_syntax_check(
    tmp_path: Path,
) -> None:
    """推导不出可导入名时退回 SYNTAX，但**仍然实际编译**范围内源码。"""

    provider, workdir, admitted, prepared, *_ = _provider(
        tmp_path, with_module=False, entry_source="def broken(:\n"
    )

    verification = provider(workdir, admitted, prepared)

    assert verification.state is SourceVerificationState.MISMATCH
    assert "source_syntax_error:entry.py" in verification.gap_ids


def test_a_different_pinned_identity_is_not_verified(tmp_path: Path) -> None:
    """若 provider 只是把冻结值抄一遍，这条会失败——它必须真的用观测值。"""

    provider, workdir, admitted, prepared, *_ = _provider(
        tmp_path, identity="sha256:" + "b" * 64
    )

    verification = provider(workdir, admitted, prepared)

    assert verification.state is not SourceVerificationState.VERIFIED
    assert "source_digest_mismatch" in verification.gap_ids


def test_an_entry_outside_the_fixed_workdir_is_refused(tmp_path: Path) -> None:
    provider, workdir, _, prepared, *_ = _provider(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(SourceBindingUnverified):
        provider(
            workdir,
            {
                "paths": [
                    {
                        "relative_path": "entry.py",
                        "actual_path": str(outside),
                        "sha256": "sha256:x",
                        "size": 10,
                    }
                ]
            },
            prepared,
        )


def test_an_empty_path_mapping_is_refused(tmp_path: Path) -> None:
    provider, workdir, _, prepared, *_ = _provider(tmp_path)

    with pytest.raises(SourceBindingUnverified):
        provider(workdir, {"paths": []}, prepared)


def test_a_legacy_snapshot_id_only_shape_is_refused(tmp_path: Path) -> None:
    """旧夹具形状（只有 `snapshot_id`）不是合同形状：必须拒绝，不得"就近取信"。"""

    provider, workdir, admitted, prepared, *_ = _provider(tmp_path)
    legacy = dict(prepared)
    legacy["snapshot"] = {
        "snapshot_id": TECHNICAL_ID,
        "content_identity": prepared["snapshot"]["content_identity"],  # type: ignore[index]
    }

    with pytest.raises(SourceBindingUnverified):
        provider(workdir, admitted, legacy)


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
