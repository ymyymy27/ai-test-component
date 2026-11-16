import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.interfaces.local.pipe import (
    NamedPipeServer,
    PeerRejected,
    check_peer_identity,
    current_user_sid,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="Windows named pipe identity only"
)


def test_check_peer_identity_rejects_empty_sids() -> None:
    # 两个空 SID 不再因“相等”而被接受（A-18 探针形态）。
    with pytest.raises(PeerRejected, match="空用户 SID"):
        check_peer_identity(
            client_session_id=1,
            expected_session_id=1,
            client_user_sid="",
            expected_user_sid="",
        )
    with pytest.raises(PeerRejected, match="空用户 SID"):
        check_peer_identity(
            client_session_id=1,
            expected_session_id=1,
            client_user_sid="S-1-5-x",
            expected_user_sid="",
        )


def test_check_peer_identity_rejects_cross_session_and_cross_user() -> None:
    with pytest.raises(PeerRejected, match="跨会话"):
        check_peer_identity(
            client_session_id=1,
            expected_session_id=2,
            client_user_sid="S-1-5-21-a",
            expected_user_sid="S-1-5-21-a",
        )
    with pytest.raises(PeerRejected, match="跨用户"):
        check_peer_identity(
            client_session_id=1,
            expected_session_id=1,
            client_user_sid="S-1-5-21-a",
            expected_user_sid="S-1-5-21-b",
        )


def test_check_peer_identity_accepts_same_session_and_user() -> None:
    check_peer_identity(
        client_session_id=1,
        expected_session_id=1,
        client_user_sid="S-1-5-21-same",
        expected_user_sid="S-1-5-21-same",
    )


def test_server_sid_lookup_for_own_process_matches_current_user() -> None:
    server = NamedPipeServer("a18-audit-workspace", instance_id="audit-instance")
    import os

    expected = current_user_sid()
    observed = server._process_user_sid(os.getpid())
    assert expected
    assert observed == expected


def test_server_sid_lookup_failure_raises_peer_rejected() -> None:
    # 不存在的进程：OpenProcess 必然失败，必须拒绝而非返回空串。
    server = NamedPipeServer("a18-audit-workspace-2", instance_id="audit-instance")
    with pytest.raises(PeerRejected, match="无法核实客户端进程用户身份"):
        server._process_user_sid(0xFFFFFFFE)
