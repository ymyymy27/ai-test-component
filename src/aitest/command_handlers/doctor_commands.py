"""环境诊断与修复命令处理器：doctor。"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aitest import __version__
from aitest.command_handlers._common import (
    Payload,
    _as_bool,
    _config_path,
    _find_secret_field,
    _probe_writable,
    _strip_secret_fields,
    _text,
    cmd_facade_attr,
)
from aitest.config import model_key_source
from aitest.errors import AiTestError
from aitest.model_config import build_client, build_user_client
from aitest.security import redact
from aitest.storage.files import read_json, write_json
from aitest.storage.workspace import Workspace, iso_now


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
            encoding="utf-8",
            errors="replace",
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
                encoding="utf-8",
                errors="replace",
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
    raw_configured = {
        name: os.environ[name] for name in names if os.environ.get(name, "").strip()
    }
    safe_configured = {
        name: redact(value, []) for name, value in raw_configured.items()
    }
    for name, value in raw_configured.items():
        if name.lower() == "no_proxy":
            continue
        parsed = urlsplit(value if "://" in value else f"http://{value}")
        if not parsed.hostname:
            return {
                "ok": False,
                "configured": safe_configured,
                "error": f"{name} 不是合法地址",
            }
    return {"ok": True, "configured": safe_configured}


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
        cmd_facade_attr("build_client", build_client)(ws, projects[0].project_id)
        if projects
        else cmd_facade_attr("build_user_client", build_user_client)()
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
        not _as_bool(payload.get("skip_explain"), False)
        and (
            not all(
                bool(facts[key]["ok"])
                for key in ("workspace", "python", "package", "model", "github", "proxy")
            )
            or _as_bool(payload.get("explain"))
        )
        and client is not None
        and model_fact.get("ok")
    ):
        try:
            explanation = client.explain_environment(facts)
        except AiTestError:
            explanation = None
        if explanation is not None:
            facts["explanation"] = explanation
    return facts
