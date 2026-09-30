"""长生命周期核心子进程入口，由 :class:`SystemProcessLauncher` 启动。

接受 :class:`NamedPipeServer` 一个客户端连接并核对对端身份，然后阻塞读取
客户端消息直到对端断开。真正的业务 dispatch（解析 Command、调用 UseCase、
返回 Response）由后续接入；本入口只承担"实例身份可见、唯一核心可连接"
的语义，让 :func:`aitest.bootstrap.acquire_endpoint` 能完成核对连接。
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="aitest-core-worker")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument(
        "--max-clients",
        type=int,
        default=1,
        help="处理多少次连接后退出；默认 1（单次连接语义）",
    )
    args = parser.parse_args()

    if not sys.platform.startswith("win"):
        print("命名管道仅 Windows", file=sys.stderr)
        return 2

    # 延迟导入：非 Windows 平台无需触发 kernel32 加载
    from aitest.interfaces.local.pipe import (
        NamedPipeServer,
        PeerRejected,
        PipeUnavailable,
    )

    served = 0
    while served < args.max_clients:
        try:
            server = NamedPipeServer(
                args.workspace_id, instance_id=args.instance_id
            )
            server.start()
        except PipeUnavailable:
            # 同名管道仍被前一连接持有或工作空间存在活动核心
            return 3
        try:
            server.wait_for_client()
            server.validate_peer()
            # 阻塞读消息，直到对端断开（ReadFile 返回 0）；
            # 真正业务 dispatch 留作后续接入。
            try:
                while True:
                    server.read_message()
            except PipeUnavailable:
                pass
            served += 1
        except PeerRejected:
            # 拒绝对端不致命，继续接受下一连接
            continue
        except PipeUnavailable:
            server.close()
            return 4
        finally:
            server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
