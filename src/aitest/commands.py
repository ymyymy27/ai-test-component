"""Command handlers for the lightweight CLI. Each handler validates input and calls storage."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from aitest import __version__
from aitest.config import known_secret_values, model_key_source
from aitest.core.delivery import build_context as build_delivery_context
from aitest.core.modules import build_context as build_module_context
from aitest.core.modules import invalid_dependencies
from aitest.core.project_context import build_project_context
from aitest.core.templates import apply_template, list_templates, load_template
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import build_client, build_user_client, load_project_config, model_settings
from aitest.models import (
    AcceptanceItem,
    Delivery,
    Dependency,
    Issue,
    Module,
    Plan,
    Project,
    RuleVersion,
    Run,
    Task,
    new_id,
)
from aitest.security import is_secret_field, looks_like_secret_value, redact, redact_tree
from aitest.storage.files import read_json, read_model, write_json, write_model
from aitest.storage.workspace import Workspace, iso_now

Payload = dict[str, Any]
Handler = Callable[[Workspace, Payload, bool], dict[str, Any]]


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


def _project(ws: Workspace, payload: Payload) -> Project:
    return ws.load_project(_text({"project_id": _require(payload, "project_id")}, "project_id"))


def _project_secrets(ws: Workspace, project_id: str) -> list[str]:
    return known_secret_values([load_project_config(ws, project_id)])


def _task_path(ws: Workspace, project_id: str, task_id: str) -> Path:
    return ws.project_dir(project_id) / "tasks" / f"{task_id}.json"


def _project_dir_for_task(ws: Workspace, task: Task) -> Path:
    return ws.project_dir(task.project_id)


# -- task intent and acceptance-item observability --------------------------

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


# -- doctor -----------------------------------------------------------------


def _probe_writable(root: Path) -> tuple[bool, str]:
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def _doctor_log(ws: Workspace, entry: dict[str, Any]) -> None:
    path = ws.root / "actions" / "doctor.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _github_fact(gh_path: str | None, repo: str) -> dict[str, Any]:
    if gh_path is None:
        return {"ok": False, "path": None, "error": "未安装 gh"}
    try:
        completed = subprocess.run(
            [gh_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "path": gh_path, "error": str(exc)}
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return {"ok": False, "path": gh_path, "error": "未登录 gh，需要人执行 gh auth login"}
    account = ""
    host = ""
    match = re.search(r"Logged in to ([^\s]+) account ([^\s]+)", output, re.IGNORECASE)
    if match:
        host, account = match.group(1), match.group(2)
    fact: dict[str, Any] = {"ok": True, "path": gh_path, "account": account, "host": host}
    if repo:
        try:
            view = subprocess.run(
                [gh_path, "repo", "view", repo, "--json", "nameWithOwner"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            fact.update({"ok": False, "repo": repo, "error": str(exc)})
            return fact
        if view.returncode != 0:
            fact.update({"ok": False, "repo": repo, "error": "无法读取目标仓库"})
        else:
            fact["repo"] = repo
    return fact


def _proxy_fact() -> dict[str, Any]:
    names = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
    configured = {
        name: os.environ[name] for name in names if os.environ.get(name, "").strip()
    }
    for name, value in configured.items():
        if name.lower() == "no_proxy":
            continue
        parsed = urlsplit(value if "://" in value else f"http://{value}")
        if not parsed.hostname:
            return {"ok": False, "configured": configured, "error": f"{name} 不是合法地址"}
    return {"ok": True, "configured": configured}


def _config_problems(ws: Workspace) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for project in ws.list_projects():
        path = _config_path(ws, project.project_id)
        if not path.is_file():
            continue
        try:
            data = read_json(path)
        except AiTestError as exc:
            problems.append(
                {
                    "project_id": project.project_id,
                    "kind": "invalid_json",
                    "detail": exc.info.message,
                }
            )
            continue
        secret = _find_secret_field(data)
        if secret is not None:
            problems.append(
                {
                    "project_id": project.project_id,
                    "kind": "secret_field",
                    "detail": secret,
                }
            )
    return problems


def _reference_problems(ws: Workspace) -> list[str]:
    missing: set[str] = set()
    for project in ws.list_projects():
        runs_dir = ws.project_dir(project.project_id) / "runs"
        if not runs_dir.is_dir():
            continue
        for path in sorted(runs_dir.glob("*/run.json")):
            try:
                data = read_json(path)
            except AiTestError:
                continue
            refs = data.get("evidence_refs")
            run_refs: list[str] = (
                [item for item in refs if isinstance(item, str)]
                if isinstance(refs, list)
                else []
            )
            cases = data.get("cases")
            for case in cases if isinstance(cases, list) else []:
                if isinstance(case, dict):
                    case_refs = case.get("evidence_refs")
                    if isinstance(case_refs, list):
                        run_refs.extend(
                            item for item in case_refs if isinstance(item, str)
                        )
            for ref in run_refs:
                if ref and not (path.parent / ref).is_file():
                    missing.add(f"{path.parent.name}:{ref}")
    return sorted(missing)


def cmd_doctor(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    init_error = ""
    try:
        meta = ws.init()
    except AiTestError as exc:
        meta = None
        init_error = exc.info.message
    writable, writable_error = _probe_writable(ws.root)
    if init_error:
        writable = False
        writable_error = init_error
    python_ok = sys.version_info >= (3, 11)
    gh_path = shutil.which("gh")
    repo = _text(payload, "repo")
    key_source = model_key_source()
    model_fact: dict[str, Any] = {"ok": False, "source": key_source}
    projects = ws.list_projects()
    client = (
        build_client(ws, projects[0].project_id) if projects else build_user_client()
    )
    if key_source == "none":
        model_fact["error"] = "未配置模型密钥"
    elif client is None:
        model_fact["error"] = "项目未建立，无法解析模型设置"
    else:
        connection = client.check_connection()
        model_fact.update(connection)
    github = _github_fact(gh_path, repo)
    proxy = _proxy_fact()
    config_problems = _config_problems(ws)
    reference_problems = _reference_problems(ws)
    repairs: list[dict[str, Any]] = []
    if not writable:
        repairs.append(
            {
                "risk": "high",
                "command": "修正工作空间目录权限",
                "auto": False,
                "reason": writable_error or "目录不可写",
            }
        )
    if not python_ok:
        repairs.append(
            {"risk": "high", "command": "安装 Python 3.11 或更高版本", "auto": False}
        )
    if key_source == "none":
        repairs.append(
            {
                "risk": "high",
                "command": "设置 AITEST_MODEL_KEY 或在用户配置写入 model.api_key",
                "auto": False,
            }
        )
    elif not model_fact.get("ok"):
        repairs.append(
            {
                "risk": "high",
                "command": "检查 model.base_url、代理与网络后重试",
                "auto": False,
                "reason": model_fact.get("error", ""),
            }
        )
    if gh_path is None:
        repairs.append(
            {"risk": "high", "command": "winget install --id GitHub.cli", "auto": False}
        )
    elif not github.get("ok"):
        repairs.append(
            {"risk": "high", "command": "gh auth login", "auto": False}
        )
    if config_problems:
        repairs.append(
            {
                "risk": "low",
                "command": "移除项目配置中的密钥字段",
                "auto": True,
            }
        )
    for project in projects:
        root = Path(project.root)
        if (root / "requirements.txt").is_file():
            repairs.append(
                {
                    "risk": "medium",
                    "command": "python -m pip install -r requirements.txt",
                    "auto": False,
                    "project_id": project.project_id,
                }
            )
        if (root / "package-lock.json").is_file():
            repairs.append(
                {
                    "risk": "medium",
                    "command": "npm ci",
                    "auto": False,
                    "project_id": project.project_id,
                }
            )
    done: list[str] = []
    if _as_bool(payload.get("repair")):
        with ws.lock():
            for name in ("projects", "requests", "backups", "actions"):
                directory = ws.root / name
                directory.mkdir(parents=True, exist_ok=True)
                done.append(f"ensure:{name}")
                _doctor_log(ws, {"at": iso_now(), "action": f"ensure:{name}", "ok": True})
            cache = ws.root / ".cache"
            if cache.is_dir():
                shutil.rmtree(cache, ignore_errors=True)
                done.append("clear:cache")
                _doctor_log(ws, {"at": iso_now(), "action": "clear:cache", "ok": True})
            for problem in config_problems:
                if problem["kind"] != "secret_field":
                    continue
                path = _config_path(ws, problem["project_id"])
                try:
                    data = _strip_secret_fields(read_json(path))
                    write_json(path, data)
                except (AiTestError, OSError):
                    done.append("strip:secrets:failed")
                    _doctor_log(
                        ws,
                        {
                            "at": iso_now(),
                            "action": "strip:secrets",
                            "project_id": problem["project_id"],
                            "ok": False,
                        },
                    )
                else:
                    done.append("strip:secrets")
                    _doctor_log(
                        ws,
                        {
                            "at": iso_now(),
                            "action": "strip:secrets",
                            "project_id": problem["project_id"],
                            "ok": True,
                        },
                    )

    facts: dict[str, Any] = {
        "workspace": {
            "ok": writable,
            "path": str(ws.root),
            "workspace_id": meta.workspace_id if meta is not None else "",
        },
        "python": {
            "ok": python_ok,
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "package": {"ok": True, "version": __version__},
        "model": model_fact,
        "github": github,
        "proxy": proxy,
        "project_config": {
            "ok": not config_problems,
            "problems": config_problems,
        },
        "references": {
            "ok": not reference_problems,
            "missing": reference_problems,
        },
        "repairs": repairs,
        "repairs_done": done,
    }
    if (
        not all(
            bool(facts[key]["ok"])
            for key in ("workspace", "python", "package", "model", "github", "proxy")
        )
        or _as_bool(payload.get("explain"))
    ) and client is not None and model_fact.get("ok"):
        try:
            explanation = client.explain_environment(facts)
        except AiTestError:
            explanation = None
        if explanation is not None:
            facts["explanation"] = explanation
    return facts


# -- project ----------------------------------------------------------------


def cmd_project_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    root = Path(_text({"root": _require(payload, "root")}, "root")).expanduser().resolve()
    if not root.is_dir():
        raise invalid_path(f"目录不存在：{root}")
    writable, writable_error = _probe_writable(root)
    if not writable:
        raise invalid_path(f"目录不可写：{root}；{writable_error}")
    existing = ws.find_by_root(str(root))
    if existing is not None:
        return {
            "project_id": existing.project_id,
            "revision": existing.revision,
            "modules": [module.model_dump(mode="json") for module in existing.modules],
            "existing": True,
        }
    overlap = ws.find_overlapping(str(root))
    if overlap:
        raise invalid_request(
            "根目录与已登记项目重叠：" + "、".join(item.project_id for item in overlap),
            "明确选择要登记的项目根目录",
        )
    secrets = known_secret_values([])
    project = Project(
        project_id=new_id("proj"),
        name=redact(_text(payload, "name") or root.name, secrets),
        root=str(root),
        goal=redact(_text(payload, "goal"), secrets),
    )
    ws.save_project(project)
    return {
        "project_id": project.project_id,
        "revision": project.revision,
        "modules": [],
        "existing": False,
    }


def cmd_project_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del payload, confirm
    return {
        "projects": [
            {
                "project_id": p.project_id,
                "name": p.name,
                "root": p.root,
                "revision": p.revision,
                "updated_at": p.updated_at,
            }
            for p in ws.list_projects()
        ]
    }


def cmd_project_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    return {
        "project": project.model_dump(mode="json"),
        "modules": [module.model_dump(mode="json") for module in project.modules],
        "binding": {"root": project.root},
        "gaps": [],
    }


def cmd_project_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    expected = payload.get("expected_revision")
    if expected is not None and expected != project.revision:
        raise AiTestError("RUN_STATE_CONFLICT", "项目修订号不匹配", "刷新后重试")
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        raise invalid_request("patch 必须是对象")
    data = project.model_dump(mode="json")
    secrets = _project_secrets(ws, project.project_id)
    for key in ("name", "goal"):
        if key in patch:
            value = patch[key]
            if not isinstance(value, str):
                raise invalid_request(f"字段必须是字符串：{key}")
            data[key] = redact(value, secrets)
    project = Project.model_validate(data)
    project.revision += 1
    ws.save_project(project)
    return {"project_id": project.project_id, "revision": project.revision}


# -- project config ---------------------------------------------------------

CONFIG_SECTIONS: tuple[str, ...] = (
    "model",
    "services",
    "link",
    "runners",
    "evidence",
    "l1",
    "data",
)
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _config_path(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "project.config.json"


def _read_project_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return read_json(path)


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


def _merge_config(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge_config(current, value)
        else:
            merged[key] = value
    return merged


def _whitelisted_config(data: dict[str, Any]) -> dict[str, Any]:
    return {section: data[section] for section in CONFIG_SECTIONS if section in data}


def _validate_config_numbers(merged: dict[str, Any]) -> None:
    evidence = merged.get("evidence")
    if isinstance(evidence, dict):
        for key in ("singleFileMb", "mediaMb", "single_file_mb", "media_budget_mb"):
            value = evidence.get(key)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0
            ):
                raise invalid_request(
                    f"evidence.{key} 必须是大于 0 的数字",
                    "证据上限按 MB 填写",
                )
    services = merged.get("services")
    if isinstance(services, dict):
        port = services.get("port")
        if port is not None and (
            not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
        ):
            raise invalid_request("services.port 必须是 1–65535 的整数", "端口范围 1–65535")
        for key in ("timeoutSeconds", "timeout_seconds"):
            value = services.get(key)
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0
            ):
                raise invalid_request(f"services.{key} 必须是大于 0 的秒数", "超时按秒填写")


def cmd_project_config(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Read or merge project.config.json without ever returning credential bodies."""
    del confirm
    project = _project(ws, payload)
    action = _text(payload, "action", "get")
    path = _config_path(ws, project.project_id)
    raw = _read_project_config(path)
    existing = _strip_secret_fields(raw)
    if action == "get":
        return {"config": _whitelisted_config(existing)}
    if action != "update":
        raise invalid_request(
            f"不支持的动作：{action}", "动作只能是 get 或 update"
        )
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        raise invalid_request("patch 必须是对象", "把要修改的配置项放进 patch")
    unknown = [str(key) for key in patch if key not in CONFIG_SECTIONS]
    if unknown:
        raise invalid_request(
            "项目配置不支持这些字段：" + "、".join(unknown),
            "只允许 " + "、".join(CONFIG_SECTIONS),
        )
    secret = _find_secret_field(patch)
    if secret is not None:
        raise AiTestError(
            "INVALID_REQUEST",
            f"项目配置不允许包含密钥字段：{secret}",
            "把密钥放到用户配置或环境变量中；项目配置只保存非敏感设置",
        )
    for section, value in patch.items():
        if value is not None and not isinstance(value, dict):
            raise invalid_request(
                f"{section} 必须是对象", f"把 {section} 写成 JSON 对象后重试"
            )
    merged = dict(existing)
    for section, value in patch.items():
        if value is None:
            merged.pop(section, None)
            continue
        current = merged.get(section)
        merged[section] = _merge_config(
            current if isinstance(current, dict) else {}, value
        )
    merged["schema_version"] = 1
    l1_config = merged.get("l1")
    if isinstance(l1_config, dict) and "entry" in l1_config:
        entries = l1_config.get("entry")
        if not isinstance(entries, list) or any(
            not isinstance(item, str) or not _MODULE_NAME.match(item)
            for item in entries
        ):
            raise invalid_request(
                'l1.entry 必须是模块名数组，例如 ["iniconfig"]',
                "填可导入的模块名，不要填目录路径或文件路径",
            )
    _validate_config_numbers(merged)
    write_json(path, merged)
    return {"config": _whitelisted_config(_strip_secret_fields(merged))}


# -- module -----------------------------------------------------------------


def cmd_module_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    deps = [
        Dependency.model_validate(item)
        for item in payload.get("deps", [])
        if isinstance(item, dict)
    ]
    module = Module(
        name=_text({"name": _require(payload, "name")}, "name"),
        responsibility=_text(payload, "responsibility"),
        owner=_text(payload, "owner"),
        status=_text(payload, "status", "开发中"),
        deps=deps,
    )
    project.modules.append(module)
    project.revision += 1
    ws.save_project(project)
    return {"module_id": module.module_id, "revision": project.revision}


def cmd_module_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    module_id = _text({"module_id": _require(payload, "module_id")}, "module_id")
    for index, module in enumerate(project.modules):
        if module.module_id == module_id:
            data = module.model_dump(mode="json")
            patch = payload.get("patch", {})
            if not isinstance(patch, dict):
                raise invalid_request("patch 必须是对象")
            data.update({k: v for k, v in patch.items() if k in data})
            project.modules[index] = Module.model_validate(data)
            project.revision += 1
            ws.save_project(project)
            return {"module_id": module_id, "revision": project.revision}
    raise AiTestError("INVALID_REQUEST", f"模块不存在：{module_id}", "检查模块 ID")


def cmd_module_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    return {"modules": [m.model_dump(mode="json") for m in project.modules]}


def cmd_module_suggest(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Return AI module suggestions without writing anything."""
    del confirm
    project = _project(ws, payload)
    client = build_client(ws, project.project_id)
    if client is None:
        raise AiTestError(
            "MODEL_UNAVAILABLE",
            "未配置模型密钥，无法建议模块",
            "设置 AITEST_MODEL_KEY 或在用户配置写入 model.api_key",
        )
    suggestions = client.suggest_modules(build_module_context(project))
    registered = {module.name for module in project.modules}
    invalid = invalid_dependencies(suggestions, registered)
    if invalid:
        raise AiTestError(
            "MODEL_OUTPUT_INVALID",
            "模块依赖指向未建议且未登记的模块：" + "、".join(invalid),
            "重试建议，或先登记被引用的模块",
        )
    return {"modules": suggestions, "registered": sorted(registered)}


# -- task -------------------------------------------------------------------


def _task_signal_context(ws: Workspace, project: Project) -> str:
    """Collect the five target signals: changes, issues, modules, docs, history."""
    root = Path(project.root)
    parts = [f"项目：{project.name}\n目标：{project.goal or '未填写'}"]
    modules = [module.name for module in project.modules if module.name.strip()]
    deps = [
        f"{module.name}->{dependency.to}"
        for module in project.modules
        for dependency in module.deps
    ]
    parts.append("模块：" + ("、".join(modules) if modules else "未登记"))
    if deps:
        parts.append("模块依赖：" + "、".join(deps))
    docs = ""
    for name in ("README.md", "README.rst", "pyproject.toml", "package.json", "requirements.txt"):
        path = root / name
        if not path.is_file():
            continue
        try:
            docs += f"\n--- {name} ---\n" + path.read_text(
                encoding="utf-8", errors="replace"
            )[:1200]
        except OSError:
            continue
    if docs:
        parts.append("项目文档与依赖声明：" + docs)
    tasks_dir = ws.project_dir(project.project_id) / "tasks"
    history: list[str] = []
    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.glob("*.json"))[-5:]:
            try:
                task = read_model(path, Task)
            except AiTestError:
                continue
            history.append(f"{task.title}（{task.revision}）")
    if history:
        parts.append("历史任务：" + "、".join(history))
    issues_dir = ws.project_dir(project.project_id) / "issues"
    open_issues: list[str] = []
    if issues_dir.is_dir():
        for path in sorted(issues_dir.glob("*.json")):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            if issue.status != "closed":
                open_issues.append(f"{issue.title}（{issue.status}/{issue.severity}）")
    if open_issues:
        parts.append("未关闭问题：" + "；".join(open_issues[:10]))
    since = ""
    runs_dir = ws.project_dir(project.project_id) / "runs"
    if runs_dir.is_dir():
        run_files = sorted(
            runs_dir.glob("*/run.json"), key=lambda item: item.stat().st_mtime
        )
        if run_files:
            try:
                latest = read_model(run_files[-1], Run)
                since = latest.started_at
            except (AiTestError, OSError):
                since = ""
    git_args = ["git", "-C", str(root), "log", "--oneline", "-20"]
    if since:
        git_args = ["git", "-C", str(root), "log", f"--since={since}", "--oneline", "-20"]
    try:
        completed = subprocess.run(
            git_args, capture_output=True, text=True, timeout=10, check=False
        )
        if completed.returncode == 0 and completed.stdout.strip():
            parts.append("最近提交：\n" + completed.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    config = load_project_config(ws, project.project_id)
    secret_list = known_secret_values([config])
    text = "\n\n".join(parts)
    for secret in secret_list:
        text = text.replace(secret, "***")
    return text


def _project_map_path(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "project-map.json"


def _git_head(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _map_config_hash(config: dict[str, Any]) -> str:
    safe = redact_tree(config, known_secret_values([config]))
    raw = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_stamp(root: Path) -> str:
    """Cheap non-git source fingerprint: newest mtime under the project."""
    newest = 0
    seen = 0
    skip = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
    try:
        for path in root.rglob("*"):
            if any(part in skip for part in path.parts):
                continue
            if not path.is_file():
                continue
            try:
                newest = max(newest, int(path.stat().st_mtime_ns))
            except OSError:
                continue
            seen += 1
            if seen >= 5000:
                break
    except OSError:
        return ""
    return str(newest)


def _map_stale(ws: Workspace, project: Project, record: dict[str, Any]) -> bool:
    config = load_project_config(ws, project.project_id)
    git_head = _git_head(Path(project.root))
    source_changed = (
        record.get("source_commit") != git_head
        if git_head
        else record.get("source_stamp") != _source_stamp(Path(project.root))
    )
    return (
        record.get("project_revision") != project.revision
        or source_changed
        or record.get("config_hash") != _map_config_hash(config)
    )


def cmd_project_map(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    path = _project_map_path(ws, project.project_id)
    record = read_json(path) if path.is_file() else None
    if not isinstance(record, dict):
        return {"map": None, "stale": False}
    return {"map": record, "stale": _map_stale(ws, project, record)}


def _map_model_name(ws: Workspace, project_id: str) -> str:
    return str(model_settings(ws, project_id)["name"])


def cmd_project_map_refresh(
    ws: Workspace, payload: Payload, confirm: bool
) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    client = build_client(ws, project.project_id)
    if client is None:
        raise AiTestError(
            "MODEL_UNAVAILABLE",
            "未配置模型，无法生成项目测试地图",
            "配置模型密钥后重试",
        )
    config = load_project_config(ws, project.project_id)
    context = _task_signal_context(ws, project)
    context += "\n\n结构化项目事实：\n" + json.dumps(
        build_project_context(Path(project.root), config),
        ensure_ascii=False,
        sort_keys=True,
    )
    result = redact_tree(client.project_map(context), known_secret_values([config]))
    record = {
        "schema_version": 1,
        "project_id": project.project_id,
        "project_revision": project.revision,
        "source_commit": _git_head(Path(project.root)),
        "source_stamp": _source_stamp(Path(project.root)),
        "config_hash": _map_config_hash(config),
        "generated_at": iso_now(),
        "model": _map_model_name(ws, project.project_id),
        **result,
    }
    write_json(_project_map_path(ws, project.project_id), record)
    return {"map": record, "stale": False}


def cmd_task_suggest(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    fallback = "验证项目能否启动并跑通主流程"
    client = build_client(ws, project.project_id)
    if client is None:
        return {
            "candidates": [],
            "fallback": fallback,
            "intent": "smoke",
            "fallback_intent": "smoke",
            "ai_generated": False,
        }
    context = _task_signal_context(ws, project)
    try:
        candidates = client.suggest_targets(context)
    except AiTestError:
        return {
            "candidates": [],
            "fallback": fallback,
            "intent": "smoke",
            "fallback_intent": "smoke",
            "ai_generated": False,
        }
    enriched = [
        {
            **candidate,
            "intent": infer_task_intent(candidate["title"]),
            "source": candidate.get("source") or "ai",
        }
        for candidate in candidates
    ]
    return {
        "candidates": enriched,
        "fallback": fallback,
        "intent": "smoke",
        "fallback_intent": "smoke",
        "ai_generated": bool(enriched),
    }


def _parse_acceptance_items(
    raw_items: list[Any],
    module_ids: set[str] | None = None,
    *,
    ai_extracted: bool = False,
) -> list[AcceptanceItem]:
    items: list[AcceptanceItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise invalid_request("验收项必须是对象")
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        observable = bool(raw.get("observable", True))
        generic = ai_extracted and _is_generic_acceptance_text(text)
        if generic:
            observable = False
        status = raw.get("status")
        if generic:
            normalized_status: Literal["active", "pending"] = "pending"
        elif status == "active":
            normalized_status = "active"
        elif status == "pending":
            normalized_status = "pending"
        else:
            normalized_status = "active" if observable else "pending"
        item_id = raw.get("item_id")
        raw_module = raw.get("module_id")
        if raw_module in (None, ""):
            module_id: str | None = None
        elif not isinstance(raw_module, str):
            raise invalid_request("module_id 必须是字符串")
        else:
            if module_ids is not None and raw_module not in module_ids:
                raise invalid_request(
                    f"验收项引用的模块未登记：{raw_module}",
                    "先登记模块，或移除该验收项的 module_id",
                )
            module_id = raw_module
        items.append(
            AcceptanceItem(
                item_id=item_id
                if isinstance(item_id, str) and item_id.strip()
                else new_id("ai"),
                text=text,
                observable=observable,
                status=normalized_status,
                source=str(raw.get("source", "")),
                module_id=module_id,
            )
        )
    return items


def cmd_task_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    title = _text({"title": _require(payload, "title")}, "title")
    goal = _text(payload, "goal")
    intent = _task_intent(payload, goal)
    raw_items = payload.get("acceptance_items", [])
    if not isinstance(raw_items, list):
        raise invalid_request("acceptance_items 必须是数组")
    ai_extracted = False
    if not raw_items:
        requirement = _text(payload, "requirement")
        client = build_client(ws, project.project_id)
        if requirement and client is not None:
            try:
                raw_items = client.extract_acceptance_items(requirement)
                ai_extracted = True
            except AiTestError:
                raw_items = []
    # No generic fallback item is synthesised: an empty observable set stays empty.
    items = _parse_acceptance_items(
        raw_items,
        {module.module_id for module in project.modules},
        ai_extracted=ai_extracted,
    )
    secrets = _project_secrets(ws, project.project_id)
    for item in items:
        item.text = redact(item.text, secrets)
        item.source = redact(item.source, secrets)
    # ``manual`` stays readable on legacy tasks (models.Task) but new tasks
    # must declare how their scope was obtained.
    origin_value = _text(payload, "origin", "goal")
    if origin_value not in {"goal", "range"}:
        raise invalid_request("origin 只能是 goal 或 range（manual 仅为旧记录兼容）")
    origin = cast(Literal["manual", "goal", "range"], origin_value)
    raw_map_revision = payload.get("source_map_revision", 0)
    try:
        map_revision = int(raw_map_revision)
    except (TypeError, ValueError):
        raise invalid_request("source_map_revision 必须是整数") from None
    task = Task(
        task_id=new_id("task"),
        project_id=project.project_id,
        title=redact(title, secrets),
        goal=redact(goal, secrets),
        intent=intent,
        origin=origin,
        source_map_revision=max(0, map_revision),
        scope=redact(_text(payload, "scope"), secrets),
        inputs=redact(_text(payload, "inputs"), secrets),
        outputs=redact(_text(payload, "outputs"), secrets),
        preconditions=[redact(str(x), secrets) for x in payload.get("preconditions", [])],
        executor=redact(_text(payload, "executor"), secrets),
        reviewer=redact(_text(payload, "reviewer"), secrets),
        acceptance_items=items,
    )
    write_model(_task_path(ws, project.project_id, task.task_id), task)
    return {
        "task_id": task.task_id,
        "revision": task.revision,
        "intent": task.intent,
        "origin": task.origin,
        "acceptance_items": [
            {
                "item_id": i.item_id,
                "status": i.status,
                "text": i.text,
                "observable": i.observable,
                "module_id": i.module_id,
            }
            for i in task.acceptance_items
        ],
    }


def _task(ws: Workspace, payload: Payload) -> Task:
    project = _project(ws, payload)
    task_id = _text({"task_id": _require(payload, "task_id")}, "task_id")
    path = _task_path(ws, project.project_id, task_id)
    if not path.is_file():
        raise AiTestError("TASK_NOT_FOUND", f"任务不存在：{task_id}", "先创建任务")
    return read_model(path, Task)


def cmd_task_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    directory = ws.project_dir(project.project_id) / "tasks"
    tasks: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            task = read_model(path, Task)
            tasks.append({"task_id": task.task_id, "title": task.title, "revision": task.revision})
    return {"tasks": tasks}


def cmd_task_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    plans_dir = ws.project_dir(task.project_id) / "plans"
    plans: list[str] = []
    if plans_dir.is_dir():
        for path in sorted(plans_dir.glob("*.json")):
            try:
                plan = read_model(path, Plan)
            except AiTestError:
                continue
            if plan.task_id == task.task_id:
                plans.append(plan.plan_id)
    return {"task": task.model_dump(mode="json"), "plans": plans}


def cmd_task_delete(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Permanently delete a task and every record derived from it."""
    del confirm
    project = _project(ws, payload)
    task = _task(ws, payload)
    root = ws.project_dir(project.project_id)

    plan_ids: set[str] = set()
    plans_dir = root / "plans"
    if plans_dir.is_dir():
        for path in plans_dir.glob("*.json"):
            try:
                plan = read_model(path, Plan)
            except AiTestError:
                continue
            if plan.task_id == task.task_id:
                plan_ids.add(plan.plan_id)

    run_ids: set[str] = set()
    run_dirs: list[Path] = []
    runs_dir = root / "runs"
    if runs_dir.is_dir():
        for run_dir in runs_dir.iterdir():
            run_path = run_dir / "run.json"
            if not run_path.is_file():
                continue
            try:
                run = read_model(run_path, Run)
            except AiTestError:
                continue
            if run.plan_id in plan_ids:
                run_ids.add(run.run_id)
                run_dirs.append(run_dir)

    removed_reports = 0
    reports_dir = root / "reports"
    if reports_dir.is_dir():
        for path in reports_dir.glob("*.json"):
            try:
                data = read_json(path)
            except (OSError, ValueError):
                continue
            if data.get("run_id") in run_ids:
                path.unlink(missing_ok=True)
                removed_reports += 1

    removed_issues = 0
    issues_dir = root / "issues"
    if issues_dir.is_dir():
        for path in issues_dir.glob("*.json"):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            if any(occurrence.run_id in run_ids for occurrence in issue.occurrences):
                path.unlink(missing_ok=True)
                removed_issues += 1

    for plan_id in plan_ids:
        (plans_dir / f"{plan_id}.json").unlink(missing_ok=True)
    for run_dir in run_dirs:
        shutil.rmtree(run_dir, ignore_errors=True)
    (root / "deliveries" / f"{task.task_id}.json").unlink(missing_ok=True)
    shutil.rmtree(root / "tasks" / "revisions" / task.task_id, ignore_errors=True)
    _task_path(ws, project.project_id, task.task_id).unlink(missing_ok=True)

    return {
        "task_id": task.task_id,
        "deleted": True,
        "plans": len(plan_ids),
        "runs": len(run_ids),
        "reports": removed_reports,
        "issues": removed_issues,
    }


def cmd_task_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    task = _task(ws, payload)
    expected = payload.get("expected_revision")
    if expected is not None and expected != task.revision:
        raise AiTestError("RUN_STATE_CONFLICT", "任务修订号不匹配", "刷新后重试")
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        raise invalid_request("patch 必须是对象")
    revision_path = (
        ws.project_dir(project.project_id) / "tasks" / "revisions" / task.task_id
    ) / f"{task.revision}.json"
    write_model(revision_path, task)
    data = task.model_dump(mode="json")
    for key in ("title", "goal", "scope", "inputs", "outputs", "executor", "reviewer"):
        if key not in patch:
            continue
        value = patch[key]
        if not isinstance(value, str):
            raise invalid_request(f"字段必须是字符串：{key}")
        if key == "title" and not value.strip():
            raise invalid_request("标题不能为空")
        data[key] = value
    if "intent" in patch:
        value = patch["intent"]
        if value not in {"smoke", "business"}:
            raise invalid_request("intent 只能是 smoke 或 business")
        data["intent"] = value
    if "preconditions" in patch:
        raw_preconditions = patch["preconditions"]
        if not isinstance(raw_preconditions, list):
            raise invalid_request("preconditions 必须是数组")
        data["preconditions"] = [str(item) for item in raw_preconditions]
    if "acceptance_items" in patch:
        raw_items = patch["acceptance_items"]
        if not isinstance(raw_items, list):
            raise invalid_request("acceptance_items 必须是数组")
        data["acceptance_items"] = [
            item.model_dump(mode="json")
            for item in _parse_acceptance_items(
                raw_items, {module.module_id for module in project.modules}
            )
        ]
    updated = Task.model_validate(data)
    secrets = _project_secrets(ws, project.project_id)
    for key in ("title", "goal", "scope", "inputs", "outputs", "executor", "reviewer"):
        setattr(updated, key, redact(getattr(updated, key), secrets))
    updated.preconditions = [redact(item, secrets) for item in updated.preconditions]
    for item in updated.acceptance_items:
        item.text = redact(item.text, secrets)
        item.source = redact(item.source, secrets)
    updated.revision += 1
    write_model(_task_path(ws, project.project_id, updated.task_id), updated)
    return {
        "task_id": updated.task_id,
        "revision": updated.revision,
        "previous_revision": task.revision,
        "intent": updated.intent,
        "acceptance_items": [
            {
                "item_id": item.item_id,
                "status": item.status,
                "text": item.text,
                "module_id": item.module_id,
            }
            for item in updated.acceptance_items
        ],
    }



# -- delivery ---------------------------------------------------------------


def cmd_delivery_save(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    delivery = Delivery(
        delivery_id=new_id("dlv"),
        task_id=task.task_id,
        done=[str(x) for x in payload.get("done", [])],
        not_done=[str(x) for x in payload.get("not_done", [])],
        changed_modules=[str(x) for x in payload.get("changed_modules", [])],
        interface_changes=[str(x) for x in payload.get("interface_changes", [])],
        run_commands=[str(x) for x in payload.get("run_commands", [])],
        test_data=_text(payload, "test_data"),
        dependencies=[str(x) for x in payload.get("dependencies", [])],
        mocks=[str(x) for x in payload.get("mocks", [])],
        self_test_evidence=[str(x) for x in payload.get("self_test_evidence", [])],
        commit=_text(payload, "commit"),
    )
    delivery.complete = bool(delivery.run_commands and delivery.commit)
    secrets = _project_secrets(ws, task.project_id)
    delivery = Delivery.model_validate(
        redact_tree(delivery.model_dump(mode="json"), secrets)
    )
    path = ws.project_dir(task.project_id) / "deliveries" / f"{task.task_id}.json"
    write_model(path, delivery)
    return {
        "delivery_id": delivery.delivery_id,
        "revision": delivery.revision,
        "complete": delivery.complete,
    }


def cmd_delivery_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    path = ws.project_dir(task.project_id) / "deliveries" / f"{task.task_id}.json"
    if not path.is_file():
        return {"delivery": None}
    return {"delivery": read_model(path, Delivery).model_dump(mode="json")}


def cmd_delivery_draft(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Draft a delivery note from read-only project facts; never persist it."""
    del confirm
    project = _project(ws, payload)
    task = _task(ws, payload)
    commit = _text(payload, "commit").strip()
    client = build_client(ws, project.project_id)
    if client is None:
        return {
            "draft": None,
            "sources": {"commit": commit},
            "status": "not_generated",
            "reason": "model_missing",
            "message": "未配置模型密钥，交付说明未生成；可手工填写后 delivery.save",
        }
    context = build_delivery_context(project, task, commit)
    try:
        draft = client.draft_delivery(context)
    except Exception:
        return {
            "draft": None,
            "sources": {"commit": commit},
            "status": "not_generated",
            "reason": "model_error",
            "message": "模型调用失败，交付说明未生成；可手工填写后 delivery.save",
        }
    files = context["files"]
    return {
        "draft": draft,
        "sources": {
            "readme": files["readme"]["path"],
            "manifest": files["manifest"]["path"],
            "commit_stat": bool(files["commit_stat"]),
        },
    }


# -- templates --------------------------------------------------------------


def _task_templates_dir(ws: Workspace, project_id: str, task_id: str) -> Path:
    return ws.project_dir(project_id) / "tasks" / task_id / "templates"


def cmd_template_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del ws, payload, confirm
    return {"templates": list_templates()}


def cmd_template_apply(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Dispose template items with the model and store the result as advice only."""
    del confirm
    project = _project(ws, payload)
    task = _task(ws, payload)
    name = _text({"template_name": _require(payload, "template_name")}, "template_name")
    plan_id = _text(payload, "plan_id").strip()
    plan_path: Path | None = None
    plan: Plan | None = None
    if plan_id:
        plan_path = ws.project_dir(project.project_id) / "plans" / f"{plan_id}.json"
        if not plan_path.is_file():
            raise AiTestError("INVALID_REQUEST", f"计划不存在：{plan_id}", "先 plan.generate")
        plan = read_model(plan_path, Plan)
        if plan.task_id != task.task_id:
            raise invalid_request("计划不属于该任务", "改用同一任务下的计划 ID")
    template = load_template(name)
    client = build_client(ws, project.project_id)
    if client is None:
        raise AiTestError(
            "MODEL_UNAVAILABLE",
            "未配置模型密钥，无法处置模板",
            "设置 AITEST_MODEL_KEY 或在用户配置写入 model.api_key",
        )
    result = apply_template(client, template, project, task)
    secrets = _project_secrets(ws, project.project_id)
    result = redact_tree(result, secrets)
    target = _task_templates_dir(ws, project.project_id, task.task_id) / (
        f"{result['template_id']}.json"
    )
    write_json(target, {"schema_version": 1, **result})
    revision = None
    if plan is not None and plan_path is not None:
        kept = [
            entry
            for entry in plan.templates
            if entry.get("template_id") != result["template_id"]
        ]
        plan.templates = [*kept, result]
        plan.revision += 1
        write_model(plan_path, plan)
        revision = plan.revision
    return {
        "template": result,
        "path": str(target.relative_to(ws.root)).replace("\\", "/"),
        "plan_id": plan_id,
        "plan_revision": revision,
    }


# -- rules ------------------------------------------------------------------


def cmd_rules_save(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    secrets = _project_secrets(ws, project.project_id)
    rule = RuleVersion(
        rules_version_id=new_id("rule"),
        project_id=project.project_id,
        name=redact(_text({"name": _require(payload, "name")}, "name"), secrets),
        version=str(_require(payload, "version")),
        steps_md=redact(_text(payload, "steps_md"), secrets),
        ai_rules=[redact(str(x), secrets) for x in payload.get("ai_rules", [])],
        evidence_requirements=[
            redact(str(x), secrets) for x in payload.get("evidence_requirements", [])
        ],
    )
    write_model(
        ws.project_dir(project.project_id) / "rules" / f"{rule.rules_version_id}.json", rule
    )
    return {"rules_version_id": rule.rules_version_id, "version": rule.version}


def cmd_rules_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    directory = ws.project_dir(project.project_id) / "rules"
    rules: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            rule = read_model(path, RuleVersion)
            rules.append(
                {
                    "rules_version_id": rule.rules_version_id,
                    "name": rule.name,
                    "version": rule.version,
                }
            )
    return {"rules": rules}


def _rule(ws: Workspace, project: Project, rules_version_id: str) -> RuleVersion:
    path = ws.project_dir(project.project_id) / "rules" / f"{rules_version_id}.json"
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"规则版本不存在：{rules_version_id}", "先保存规则")
    return read_model(path, RuleVersion)


def cmd_rules_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    rule = _rule(ws, project, _text({"id": _require(payload, "rules_version_id")}, "id"))
    return {"rule": rule.model_dump(mode="json")}


def cmd_rules_bind(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    rule = _rule(ws, project, _text({"id": _require(payload, "rules_version_id")}, "id"))
    project.rules_version_id = rule.rules_version_id
    project.revision += 1
    ws.save_project(project)
    return {"project_id": project.project_id, "rules_version_id": rule.rules_version_id}


def cmd_rules_export(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    rule = _rule(ws, project, _text({"id": _require(payload, "rules_version_id")}, "id"))
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rule.steps_md or f"# {rule.name}\n", encoding="utf-8")
    return {"path": str(target), "bytes": target.stat().st_size}


def cmd_rules_import(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    source = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"文件不存在：{source}")
    secrets = _project_secrets(ws, project.project_id)
    rule = RuleVersion(
        rules_version_id=new_id("rule"),
        project_id=project.project_id,
        name=redact(_text({"name": _require(payload, "name")}, "name"), secrets),
        version=str(_require(payload, "version")),
        steps_md=redact(source.read_text(encoding="utf-8"), secrets),
    )
    write_model(
        ws.project_dir(project.project_id) / "rules" / f"{rule.rules_version_id}.json", rule
    )
    return {"rules_version_id": rule.rules_version_id, "version": rule.version}


REGISTRY: dict[str, Handler] = {
    "doctor": cmd_doctor,
    "project.add": cmd_project_add,
    "project.list": cmd_project_list,
    "project.show": cmd_project_show,
    "project.update": cmd_project_update,
    "project.config": cmd_project_config,
    "module.add": cmd_module_add,
    "module.update": cmd_module_update,
    "module.list": cmd_module_list,
    "module.suggest": cmd_module_suggest,
    "task.suggest": cmd_task_suggest,
    "project.map": cmd_project_map,
    "project.map.refresh": cmd_project_map_refresh,
    "task.add": cmd_task_add,
    "task.update": cmd_task_update,
    "task.delete": cmd_task_delete,
    "task.list": cmd_task_list,
    "task.show": cmd_task_show,
    "delivery.save": cmd_delivery_save,
    "delivery.show": cmd_delivery_show,
    "delivery.draft": cmd_delivery_draft,
    "template.list": cmd_template_list,
    "template.apply": cmd_template_apply,
    "rules.save": cmd_rules_save,
    "rules.list": cmd_rules_list,
    "rules.show": cmd_rules_show,
    "rules.bind": cmd_rules_bind,
    "rules.import": cmd_rules_import,
    "rules.export": cmd_rules_export,
}

MUTATING = {
    "project.add",
    "project.update",
    "project.config",
    "module.add",
    "module.update",
    "task.add",
    "task.update",
    "task.delete",
    "delivery.save",
    "template.apply",
    "rules.save",
    "rules.bind",
    "rules.import",
    "rules.export",
}


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
