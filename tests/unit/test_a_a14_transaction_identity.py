import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.contracts.commands import Command
from aitest.interfaces.local.api import (
    BEGIN_REQUEST_ID_PARAM,
    EntryKind,
    LocalAPI,
    Session,
)

_SESSION = Session(session_id="s-1", entry_kind=EntryKind.INTERACTIVE_CLI)


class FakeTransactionPort:
    """记录每个阶段实际收到的 request_id，模拟真实 UOW 归属语义。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._owner: str | None = None

    def begin(self, *, request_id: str, project_id, intent_id, workspace_id):
        self.calls.append(("begin", request_id))
        if self._owner is not None:
            raise RuntimeError("transaction already open")
        self._owner = request_id
        return {"state": "started", "begin_request_id": request_id}

    def commit(self, *, request_id: str, workspace_id):
        self.calls.append(("commit", request_id))
        if request_id != self._owner:
            raise RuntimeError("request_id does not own transaction")
        self._owner = None
        return {"state": "committed"}

    def rollback(self, *, request_id: str, workspace_id):
        self.calls.append(("rollback", request_id))
        if request_id != self._owner:
            raise RuntimeError("request_id does not own transaction")
        self._owner = None
        return {"state": "rolled_back"}

    def recover(self, *, request_id: str, workspace_id):
        self.calls.append(("recover", request_id))
        return {"state": "idle"}


def _command(
    action: str,
    *,
    request_id: str,
    project_id: str | None = "proj",
    parameters: dict | None = None,
) -> Command:
    return Command(
        request_id=request_id,
        action=action,
        project_id=project_id,
        intent_id=None if action in {"begin", "commit", "rollback", "recover"} else "i",
        expected_revision=None
        if action in {"begin", "commit", "rollback", "recover"}
        else 0,
        parameters=parameters or {},
    )


def _api() -> tuple[LocalAPI, FakeTransactionPort]:
    port = FakeTransactionPort()
    return (
        LocalAPI("instance-1", workspace_id="ws-1", transaction_port=port),
        port,
    )


def test_begin_commit_closed_loop_with_distinct_transport_ids() -> None:
    api, port = _api()
    begin = api.dispatch(_command("begin", request_id="tx-begin"), _SESSION)
    assert begin.error is None
    assert begin.result[BEGIN_REQUEST_ID_PARAM] == "tx-begin"

    # commit 的传输 request_id 不同，但通过 begin 句柄归属到同一事务。
    commit = api.dispatch(
        _command(
            "commit",
            request_id="tx-commit",
            parameters={BEGIN_REQUEST_ID_PARAM: "tx-begin"},
        ),
        _SESSION,
    )
    assert commit.error is None
    assert commit.result["state"] == "committed"
    assert port.calls == [
        ("begin", "tx-begin"),
        ("commit", "tx-begin"),
    ]


def test_foreign_commit_without_active_transaction_rejected() -> None:
    api, port = _api()
    # 从未 begin，直接拿异号 commit 必须拒绝，不能路由到事务端口。
    response = api.dispatch(_command("commit", request_id="stranger"), _SESSION)
    assert response.error is not None
    assert response.error.code == "NO_ACTIVE_TRANSACTION"
    assert port.calls == []


def test_wrong_begin_handle_rejected() -> None:
    api, port = _api()
    api.dispatch(_command("begin", request_id="tx-begin"), _SESSION)
    response = api.dispatch(
        _command(
            "commit",
            request_id="tx-commit",
            parameters={BEGIN_REQUEST_ID_PARAM: "other-begin"},
        ),
        _SESSION,
    )
    assert response.error is not None
    assert response.error.code == "TRANSACTION_NOT_OWNED"
    assert port.calls == [("begin", "tx-begin")]


def test_second_begin_while_active_rejected() -> None:
    api, _port = _api()
    api.dispatch(_command("begin", request_id="tx-begin"), _SESSION)
    response = api.dispatch(_command("begin", request_id="tx-begin-2"), _SESSION)
    assert response.error is not None
    assert response.error.code == "TRANSACTION_ALREADY_ACTIVE"


def test_commit_after_close_is_rejected_but_retransmit_is_idempotent() -> None:
    api, _port = _api()
    api.dispatch(_command("begin", request_id="tx-begin"), _SESSION)
    first = api.dispatch(_command("commit", request_id="tx-commit"), _SESSION)
    assert first.error is None

    # 同传输 request_id + 同输入重传：幂等返回首次结果。
    retry = api.dispatch(_command("commit", request_id="tx-commit"), _SESSION)
    assert retry.error is None
    assert retry.result["state"] == "committed"

    # 新传输 request_id 的 commit 不得作用于已关闭事务。
    stranger = api.dispatch(_command("commit", request_id="tx-commit-2"), _SESSION)
    assert stranger.error is not None
    assert stranger.error.code == "NO_ACTIVE_TRANSACTION"


def test_same_request_id_begin_then_commit_conflicts_on_fingerprint() -> None:
    api, _port = _api()
    api.dispatch(_command("begin", request_id="shared"), _SESSION)
    # 同一 request_id 改作 commit：传输层幂等指纹冲突。
    response = api.dispatch(_command("commit", request_id="shared"), _SESSION)
    assert response.error is not None
    assert response.error.code == "REQUEST_CONFLICT"


def test_rollback_releases_ownership_and_recover_always_allowed() -> None:
    api, port = _api()
    api.dispatch(_command("begin", request_id="tx-begin"), _SESSION)
    rolled = api.dispatch(
        _command("rollback", request_id="tx-rollback"), _SESSION
    )
    assert rolled.error is None
    assert rolled.result["state"] == "rolled_back"

    after = api.dispatch(
        _command("rollback", request_id="tx-rollback-2"), _SESSION
    )
    assert after.error is not None
    assert after.error.code == "NO_ACTIVE_TRANSACTION"

    recovered = api.dispatch(
        _command("recover", request_id="read-only-recover"), _SESSION
    )
    assert recovered.error is None
    assert recovered.result["state"] == "idle"


def test_project_mismatch_between_begin_and_commit_rejected() -> None:
    api, _port = _api()
    api.dispatch(
        _command("begin", request_id="tx-begin", project_id="project-a"), _SESSION
    )
    response = api.dispatch(
        _command("commit", request_id="tx-commit", project_id="project-b"),
        _SESSION,
    )
    assert response.error is not None
    assert response.error.code == "TRANSACTION_PROJECT_MISMATCH"
