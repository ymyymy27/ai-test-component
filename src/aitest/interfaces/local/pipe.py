"""Windows当前用户及会话命名管道、对端及实例校验；不监听TCP。

一期职责预留，尚未实现，不注册为可用能力。
"""

from __future__ import annotations


class PipeUnavailable(Exception):
    """管道不可用：同名管道被占用、对端未就绪或身份不可信。"""


class PeerRejected(Exception):
    """对端身份校验失败。"""


class NamedPipeServer:
    """命名管道服务端（一期预留，尚未实现）。"""

    def __init__(self, workspace_id: str, *, instance_id: str) -> None:
        raise NotImplementedError("NamedPipeServer 一期预留，尚未实现")

    def start(self) -> None:
        raise NotImplementedError

    def wait_for_client(self) -> None:
        raise NotImplementedError

    def validate_peer(self) -> None:
        raise NotImplementedError

    def read_message(self) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class NamedPipeClient:
    """命名管道客户端（一期预留，尚未实现）。"""

    def __init__(self, workspace_id: str, *, instance_id: str) -> None:
        raise NotImplementedError("NamedPipeClient 一期预留，尚未实现")

    def connect(self, *, timeout_ms: int) -> None:
        raise NotImplementedError


def validate_workspace_id(workspace_id: str) -> None:
    """校验工作空间 ID 合法性，不合法时抛 :class:`PipeUnavailable`。

    一期预留，尚未实现。
    """
    raise NotImplementedError("validate_workspace_id 一期预留，尚未实现")


__all__ = [
    "NamedPipeClient",
    "NamedPipeServer",
    "PeerRejected",
    "PipeUnavailable",
    "validate_workspace_id",
]
