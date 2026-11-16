"""A-01：AB-001 §8.8 三个只读方法冻结 + Secret 语义冻结。

覆盖：
- ``commit_seq`` / ``next_commit_seq`` 未开事务可读、按暂存记录逐条递增、
  提交/回滚后口径正确、跨"重启"（同根新实例）读回；
- ``current_revision`` 同时满足冻结端口的关键字签名与 B 转接头的位置签名；
- 两个具体实现结构上满足 ``application.ports`` 的冻结协议；
- 正式序号访问器直接注入 B 的 ``PortsUnitOfWork``（替代恢复巡检临时接法），
  缺少序号来源时 B 仍按合同抛 ``SubstrateContractError``；
- SecretPort 冻结语义：按用途分别授权、不跨用途顶替、未知用途显式失败、
  正文不进 repr/str、能力探测不回正文、解析不落盘任何工作空间文件。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import runtime_checkable

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.application.planning.substrate import SubstrateContractError
from aitest.application.planning.substrate_adapter import PortsUnitOfWork
from aitest.application.ports import (
    RecordRepository as FrozenRecordRepository,
)
from aitest.application.ports import SecretPort
from aitest.application.ports import (
    WorkspaceUnitOfWork as FrozenWorkspaceUnitOfWork,
)
from aitest.infrastructure.credentials import (
    EnvironmentSecretProvider,
    SecretManager,
    SecretUnavailable,
)
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

# ------------------------------------------------------------ 提交序号冻结


def test_commit_seq_readable_without_open_transaction(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)

    assert unit.current_commit_sequence() == 0
    assert unit.commit_seq() == "0"
    # 未暂存任何记录时，下一条记录提交后得到序号 1。
    assert unit.next_commit_seq() == "1"


def test_next_commit_seq_advances_per_staged_record(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    unit.begin("req-seq", "project-1")

    assert unit.next_commit_seq() == "1"
    revision_1 = unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1", "summary": "first"},
    )
    assert revision_1 == 1
    assert unit.commit_seq() == "0"
    assert unit.next_commit_seq() == "2"

    revision_2 = unit.stage_record(
        aggregate_kind="case",
        record_id="case-2",
        expected_revision=None,
        payload={"project_id": "project-1", "summary": "second"},
    )
    assert revision_2 == 1
    assert unit.next_commit_seq() == "3"

    result = unit.commit("req-seq")
    # 序号按记录递增：两条记录的提交落在序号 2，下一条将是 3。
    assert result["commit_sequence"] == 2
    assert unit.commit_seq() == "2"
    assert unit.next_commit_seq() == "3"


def test_commit_seq_persists_across_restart(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    unit.begin("req-1", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1"},
    )
    unit.commit("req-1")

    # 等价于核心进程重启：同根新建工作单元与仓储，读已持久化的计数。
    restarted = FileUnitOfWork(tmp_path)
    assert restarted.commit_seq() == "1"
    assert FileRecordRepository(tmp_path).current_commit_sequence() == 1


def test_next_commit_seq_resets_after_rollback(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    assert unit.next_commit_seq() == "1"
    unit.begin("req-rollback", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1"},
    )
    assert unit.next_commit_seq() == "2"
    unit.rollback("req-rollback")

    assert unit.commit_seq() == "0"
    assert unit.next_commit_seq() == "1"
    # 回滚不产生任何修订事实。
    assert FileRecordRepository(tmp_path).current_revision("case", "case-1") == 0


# ------------------------------------------------------------ current_revision


def test_current_revision_supports_keyword_and_positional(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    unit.begin("req-rev", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1", "v": 1},
    )
    unit.commit("req-rev")

    repo = FileRecordRepository(tmp_path)
    # B 转接头按位置调用。
    assert repo.current_revision("case", "case-1") == 1
    # 冻结端口（AB-001 §8.8）按关键字调用。
    assert repo.current_revision(aggregate_kind="case", record_id="case-1") == 1
    assert repo.current_revision(aggregate_kind="case", record_id="missing") == 0


def test_concrete_impls_satisfy_frozen_protocols(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    repo = FileRecordRepository(tmp_path)

    # runtime_checkable 只校验协议成员齐备；签名一致性由 mypy 静态门保证。
    assert isinstance(unit, runtime_checkable(FrozenWorkspaceUnitOfWork))
    assert isinstance(repo, runtime_checkable(FrozenRecordRepository))
    for member in ("commit_seq", "next_commit_seq", "stage_record", "commit"):
        assert callable(getattr(unit, member))
    for member in ("read", "query", "current_revision"):
        assert callable(getattr(repo, member))


# ------------------------------------------ B 转接头经正式访问器取序号（A-02 预接线）


def test_b_adapter_uses_formal_sequence_accessor(tmp_path: Path) -> None:
    raw = FileUnitOfWork(tmp_path)
    # 正式接法：把工作单元自身作为 CommitSequenceSource 注入，
    # 不再依赖 RecoveryOrchestrator.inspect 的临时包装。
    adapter = PortsUnitOfWork(raw, repository=raw.repo, sequence=raw)

    # B 的阻塞/复用分支不开事务也要读当前序号。
    assert adapter.commit_seq() == "0"
    assert adapter.next_commit_seq() == "1"

    adapter.open("project-1")
    staged = adapter.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1"},
    )
    assert staged.revision == 1
    assert adapter.commit_seq() == "0"
    assert adapter.next_commit_seq() == "2"
    result = adapter.commit()
    assert result.commit_seq == "1"

    assert raw.commit_seq() == "1"


def test_b_adapter_without_sequence_source_still_reports_contract_gap(
    tmp_path: Path,
) -> None:
    raw = FileUnitOfWork(tmp_path)
    adapter = PortsUnitOfWork(raw, repository=raw.repo)

    with pytest.raises(SubstrateContractError):
        adapter.commit_seq()
    with pytest.raises(SubstrateContractError):
        adapter.next_commit_seq()


# ---------------------------------------------------------------- Secret 语义


class _RecordingProvider:
    """测试用来源：记录解析调用，按用途回不同值。"""

    name = "recording"
    available = True

    def __init__(self, values: dict[tuple[str, str], str]) -> None:
        self._values = dict(values)
        self.calls: list[tuple[str, str]] = []

    def resolve(self, *, purpose: str, reference: str) -> str:
        self.calls.append((purpose, reference))
        try:
            return self._values[(purpose, reference)]
        except KeyError as error:
            raise SecretUnavailable(f"missing: {purpose}/{reference}") from error


def test_secret_resolve_is_scoped_per_purpose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A01_MODEL_TOKEN", "model-secret-value")
    monkeypatch.setenv("A01_HTTP_TOKEN", "http-secret-value")
    manager = SecretManager(
        (
            EnvironmentSecretProvider(
                {
                    ("model", "primary"): "A01_MODEL_TOKEN",
                    ("http", "primary"): "A01_HTTP_TOKEN",
                }
            ),
        )
    )

    model_secret = manager.resolve("primary", purpose="model")
    http_secret = manager.resolve("primary", purpose="http")
    assert model_secret.reveal() == "model-secret-value"
    assert http_secret.reveal() == "http-secret-value"
    assert model_secret.purpose == "model"
    # 同引用不同用途是两次独立授权，不得互相顶替。
    assert model_secret.reveal() != http_secret.reveal()


def test_secret_unknown_purpose_fails_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A01_MODEL_TOKEN", "model-secret-value")
    manager = SecretManager(
        (EnvironmentSecretProvider({("model", "primary"): "A01_MODEL_TOKEN"}),)
    )

    # 即使 model 用途下凭据存在，未知用途也必须显式失败，不得回退顶替。
    with pytest.raises(SecretUnavailable, match="未知凭据用途"):
        manager.resolve("primary", purpose="verification_database")
    assert not manager.has_secret("primary", purpose="github")


def test_secret_missing_in_one_purpose_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A01_MODEL_TOKEN", "model-secret-value")
    provider = EnvironmentSecretProvider(
        {("model", "primary"): "A01_MODEL_TOKEN"}
    )
    manager = SecretManager((provider,))

    with pytest.raises(SecretUnavailable) as excinfo:
        manager.resolve("primary", purpose="http")
    # 错误信息可以说明用途/引用，但不得带出任何已登记的明文值。
    message = str(excinfo.value)
    assert "model-secret-value" not in message


def test_secret_repr_str_and_clear_never_leak_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A01_HTTP_TOKEN", "http-secret-value")
    manager = SecretManager(
        (EnvironmentSecretProvider({("http", "primary"): "A01_HTTP_TOKEN"}),)
    )

    secret = manager.resolve("primary", purpose="http")
    assert "http-secret-value" not in str(secret)
    assert "http-secret-value" not in repr(secret)
    secret.clear()
    assert secret.reveal() == ""


def test_has_secret_reports_only_boolean_and_clears_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A01_MODEL_TOKEN", "model-secret-value")
    manager = SecretManager(
        (EnvironmentSecretProvider({("model", "primary"): "A01_MODEL_TOKEN"}),)
    )

    assert manager.has_secret("primary", purpose="model") is True
    monkeypatch.delenv("A01_MODEL_TOKEN")
    assert manager.has_secret("primary", purpose="model") is False
    assert manager.has_secret("other", purpose="model") is False


def test_secret_manager_satisfies_frozen_port_and_writes_no_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("A01_MODEL_TOKEN", "model-secret-value")
    manager = SecretManager(
        (EnvironmentSecretProvider({("model", "primary"): "A01_MODEL_TOKEN"}),)
    )
    assert isinstance(manager, runtime_checkable(SecretPort))

    secret = manager.resolve("primary", purpose="model")
    secret.clear()
    # 凭据解析无文件回退：工作空间目录不得出现任何落盘材料。
    assert list(tmp_path.iterdir()) == []
