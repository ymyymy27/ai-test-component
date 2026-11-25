"""面向 CLI 与扩展桥的轻量级 stderr 实时进度事件输出工具。"""

from __future__ import annotations

import json
import sys
from typing import Any

_PROGRESS_PREFIX = "[aitest-progress] "


def emit_progress(
    phase: str,
    message: str = "",
    *,
    done: int | None = None,
    total: int | None = None,
    stage: str = "",
) -> None:
    """向 stderr 写入单行结构化进度事件，绝不触碰 stdout JSON 信封。"""
    payload: dict[str, Any] = {
        "type": "progress",
        "phase": phase,
    }
    if stage:
        payload["stage"] = stage
    if message:
        payload["message"] = message
    if done is not None:
        payload["done"] = done
    if total is not None:
        payload["total"] = total
    try:
        line = _PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False)
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except OSError:
        # 进度仅作实时状态提示，不能因流关闭影响业务执行。
        pass
