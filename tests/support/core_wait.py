"""真实核心进程的等待期限（仅测试用）。

`EditorHost` 的默认期限是 **5 秒**（`interfaces/local/editor_host.py:60`，由
`bootstrap._DEFAULT_WAIT_TIMEOUT_SECONDS` 引用）。对**单文件**运行足够；但本机全量跑
（5011 项、大量真实装配与真实子进程）时机器持续满负荷，核心启动会超过 5 秒，于是报
`WorkspaceInUse(核心启动后在限定时间内不可连接)`。这类**假失败**有实际危害：它会让
「改了代码、旧断言变红」被当成噪声忽略（见 `AGENTS.md` 第 5.5.4 条）。

**只改测试侧，不改生产默认**：5 秒是有界且**如实**的失败（设计文档要求「如实报不可用、
不伪装成功」），合同也没有规定该期限；把生产默认调大等于为了让测试变绿而改变产品语义。

真实起核心的用例显式传该期限：

    endpoint = acquire_endpoint(root, wait_timeout_seconds=REAL_CORE_WAIT_SECONDS)

它远小于 `_wait_seconds` 的 60 秒上限，仍有界——真的起不来的核心不会让测试挂死。
"""

from __future__ import annotations

#: 真实核心启动并可连接的等待期限（秒）：容忍满负荷下的冷启动，又不失有界。
REAL_CORE_WAIT_SECONDS = 30.0

__all__ = ["REAL_CORE_WAIT_SECONDS"]
