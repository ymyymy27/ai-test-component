"""命令处理器公共原语、鉴权检查、任务意图推断与请求幂等缓存。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from aitest.config import known_secret_values
from aitest.errors import AiTestError, invalid_request
from aitest.model_config import load_project_config
from aitest.models import Project, Run, Task
from aitest.procutil import pid_alive
from aitest.security import is_secret_field, looks_like_secret_value
from aitest.storage.files import read_json, read_model, write_json
from aitest.storage.workspace import Workspace

Payload = dict[str, Any]
Handler = Callable[[Workspace, Payload, bool], dict[str, Any]]


def cmd_facade_attr(name: str, default: Any) -> Any:
    """从 aitest.commands 门面动态解析可被测试 monkeypatch 替换的符号。"""
    facade = sys.modules.get("aitest.commands")
    return getattr(facade, name, default) if facade is not None else default


def _require(payload: Payload, key: str) -> Any:
    value = payload.get(key)
    if value in (None, "", [], {}):
        raise invalid_request(f"缺少字段：{key}")
    return value


def _text(payload: Payload, key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise invalid_request(f"字段必须是字符串：{key}")
    return value


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _safe_id(value: str, label: str = "ID") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise invalid_request(f"{label} 不合法：{value}")
    return value


def _heartbeat_stale(run: Run, seconds: float = 120.0) -> bool:
    if not run.heartbeat_at:
        return True
    try:
        then = datetime.fromisoformat(run.heartbeat_at).timestamp()
    except ValueError:
        return True
    age = time.time() - then
    if run.pid:
        if pid_alive(run.pid, run.pid_created_at):
            return False
        return age > 5
    return age > seconds


def _project(ws: Workspace, payload: Payload) -> Project:
    project_id = _safe_id(
        _text({"project_id": _require(payload, "project_id")}, "project_id"),
        "project_id",
    )
    return ws.load_project(project_id)


def _project_secrets(ws: Workspace, project_id: str) -> list[str]:
    return known_secret_values([load_project_config(ws, project_id)])


def _task_path(ws: Workspace, project_id: str, task_id: str) -> Path:
    safe_task_id = _safe_id(task_id, "task_id")
    return ws.project_dir(project_id) / "tasks" / f"{safe_task_id}.json"


def _task(ws: Workspace, payload: Payload) -> Task:
    project = _project(ws, payload)
    task_id = _text({"task_id": _require(payload, "task_id")}, "task_id")
    path = _task_path(ws, project.project_id, task_id)
    if not path.is_file():
        raise AiTestError("TASK_NOT_FOUND", f"任务不存在：{task_id}", "先创建任务")
    return read_model(path, Task)


def _project_map_path(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "project-map.json"


def _config_path(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "project.config.json"


def _probe_writable(root: Path) -> tuple[bool, str]:
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def _find_secret_field(value: Any, prefix: str = "") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            path = f"{prefix}{name}"
            if is_secret_field(name):
                return path
            found = _find_secret_field(item, f"{path}.")
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_secret_field(item, f"{prefix}{index}.")
            if found is not None:
                return found
    elif isinstance(value, str) and looks_like_secret_value(value):
        return f"{prefix}<value>"
    return None


def _strip_secret_fields(value: Any) -> Any:
    """Drop credential-named keys and redact credential-looking values."""
    if isinstance(value, dict):
        return {
            key: _strip_secret_fields(item)
            for key, item in value.items()
            if not is_secret_field(str(key))
        }
    if isinstance(value, list):
        return [_strip_secret_fields(item) for item in value]
    if isinstance(value, str) and looks_like_secret_value(value):
        return "***"
    return value


_SMOKE_GOAL_PHRASES = (
    "能否启动",
    "能否正常启动",
    "能够启动",
    "能够正常启动",
    "可以启动",
    "可以正常启动",
    "项目能启动",
    "跑通主流程",
    "主流程能够跑通",
    "主流程可以跑通",
    "主流程能跑通",
)

_BUSINESS_WORDS = (
    "下单",
    "订单",
    "支付",
    "退款",
    "库存",
    "购物车",
    "登录",
    "注册",
    "注销",
    "用户",
    "账号",
    "账户",
    "权限",
    "搜索",
    "查询",
    "创建",
    "新增",
    "编辑",
    "修改",
    "删除",
    "上传",
    "下载",
    "导出",
    "导入",
    "审批",
    "结算",
    "账单",
    "消息",
    "通知",
)

_GENERIC_ITEM_PATTERNS = (
    re.compile(r"项目(?:能否|能够|可以|能|是否)?(?:正常)?启动"),
    re.compile(r"主流程(?:能否|能够|可以|能|是否)?(?:正常)?跑通"),
    re.compile(r"(?:跑通|走通)主流程"),
    re.compile(r"项目(?:能否|能够|可以|能)?(?:正常)?运行"),
    re.compile(r"系统(?:能否|能够|可以|能)?(?:正常)?运行"),
)


def infer_task_intent(goal: str) -> Literal["smoke", "business"]:
    """A pure smoke goal mentions startup/flow but no concrete business action."""
    text = goal.strip()
    if not text:
        return "business"
    mentions_smoke = any(phrase in text for phrase in _SMOKE_GOAL_PHRASES)
    mentions_business = any(word in text for word in _BUSINESS_WORDS)
    if mentions_smoke and not mentions_business:
        return "smoke"
    return "business"


def _task_intent(payload: Payload, goal: str) -> Literal["smoke", "business"]:
    raw = payload.get("intent")
    if raw in (None, ""):
        return infer_task_intent(goal)
    if raw not in {"smoke", "business"}:
        raise invalid_request("intent 只能是 smoke 或 business")
    return cast(Literal["smoke", "business"], raw)


def _is_generic_acceptance_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in _GENERIC_ITEM_PATTERNS)


def request_cache_path(
    ws: Workspace, request_id: str, command: str = "", payload: object = None
) -> Path:
    material = json.dumps(
        {"request_id": request_id, "command": command, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return ws.root / "requests" / f"{digest}.json"


def cached_result(
    ws: Workspace, request_id: str, command: str = "", payload: object = None
) -> dict[str, Any] | None:
    path = request_cache_path(ws, request_id, command, payload)
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (AiTestError, OSError):
        return None


def store_result(
    ws: Workspace,
    request_id: str,
    result: dict[str, Any],
    command: str = "",
    payload: object = None,
) -> None:
    write_json(request_cache_path(ws, request_id, command, payload), result)
