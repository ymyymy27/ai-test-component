"""来源核验事实（P1-FR08 / P1-AC13）：装配层 provider 必须**非空转**。

本轮采用 `SourceCheckType.SYNTAX`（原因见交接说明 §6：`LOAD`／`MINIMAL_START` 需要合同尚未
定义的「登记模块可导入名」）。因此本测试锁定两件必须成立的事：

1. **真实观测**：`observed_source_digest` 取自 `verify_pinned` 重新核对后的身份——因此当
   固定记录的身份与冻结期望不一致时，必须判非 `VERIFIED`（否则就是把冻结值抄一遍）；
2. **入口必须在固定 workdir 内**：没有可用入口路径时直接拒绝，不能凭空通过。

`require_verified_source` 对非 `VERIFIED` 一律阻塞，故"判非 VERIFIED"即可理解为"不得启动"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aitest.application.execution.start_source_binding import SourceBindingUnverified
from aitest.bootstrap import _source_verification_provider
from aitest.domain.execution.sources import SourceVerificationState

IDENTITY = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


class _Snapshots:
    """最小 `SourceSnapshotPort` 替身：只实现 provider 用到的 `verify_pinned`。"""

    def __init__(self, identity: str) -> None:
        self._identity = identity

    def verify_pinned(self, snapshot_id: str) -> dict[str, object]:
        return {"content_identity": self._identity}


def _prepared(identity: str) -> dict[str, object]:
    return {
        "project_id": "project-a",
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


def _workdir(tmp_path: Path) -> tuple[str, dict[str, object]]:
    workdir = tmp_path / "fixed-workdir"
    workdir.mkdir()
    entry = workdir / "entry.py"
    entry.write_text("value = 1\n", encoding="utf-8")
    admitted: dict[str, object] = {
        "paths": [{"actual_path": str(entry), "sha256": "sha256:entry", "size": 10}]
    }
    return str(workdir), admitted


def test_matching_identity_and_entry_are_verified(tmp_path: Path) -> None:
    workdir, admitted = _workdir(tmp_path)
    provider = _source_verification_provider(_Snapshots(IDENTITY))

    verification = provider(workdir, admitted, _prepared(IDENTITY))

    assert verification.state is SourceVerificationState.VERIFIED
    assert verification.gap_ids == ()
    assert verification.observed_source_digest == IDENTITY
    assert verification.observed_interpreter_ref == sys.executable


def test_a_different_pinned_identity_is_not_verified(tmp_path: Path) -> None:
    """若 provider 只是把冻结值抄一遍，这条会失败——它必须真的用观测值。"""

    workdir, admitted = _workdir(tmp_path)
    provider = _source_verification_provider(_Snapshots(OTHER))

    verification = provider(workdir, admitted, _prepared(IDENTITY))

    assert verification.state is not SourceVerificationState.VERIFIED
    assert "source_digest_mismatch" in verification.gap_ids


def test_an_entry_outside_the_fixed_workdir_is_refused(tmp_path: Path) -> None:
    workdir, _ = _workdir(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("value = 2\n", encoding="utf-8")
    provider = _source_verification_provider(_Snapshots(IDENTITY))

    with pytest.raises(SourceBindingUnverified):
        provider(
            workdir,
            {"paths": [{"actual_path": str(outside), "sha256": "sha256:x", "size": 10}]},
            _prepared(IDENTITY),
        )


def test_an_empty_path_mapping_is_refused(tmp_path: Path) -> None:
    workdir, _ = _workdir(tmp_path)
    provider = _source_verification_provider(_Snapshots(IDENTITY))

    with pytest.raises(SourceBindingUnverified):
        provider(workdir, {"paths": []}, _prepared(IDENTITY))
