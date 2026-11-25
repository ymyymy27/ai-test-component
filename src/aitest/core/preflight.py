"""开测前环境安检门（Preflight Gate）。

在启动被测服务与执行用例之前，确定性地完成多服务拓扑排序、本地端口冲突检查、
依赖工具可用性校验与必填环境变量键检查（绝不记录任何凭据明文）。
"""

from __future__ import annotations

import contextlib
import heapq
import http.client
import os
import re
import shlex
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

_DB_HINTS = (
    "db",
    "database",
    "postgres",
    "postgresql",
    "mysql",
    "mariadb",
    "sqlite",
    "mongo",
    "mongodb",
    "redis",
    "cache",
    "mailpit",
    "mailhog",
    "smtp",
    "mq",
    "rabbitmq",
    "kafka",
    "broker",
    "minio",
)
_FRONTEND_HINTS = (
    "frontend",
    "front",
    "ui",
    "client",
    "spa",
    "vite",
    "next",
    "nuxt",
)
_LOCAL_PROBE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})
_SHELL_BUILTINS = frozenset(
    {"cd", "pushd", "popd", "set", "export", "echo", "true", "false", "call"}
)
_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_CHAIN_PREFIX_RE = re.compile(r"^(?:(?:cd|pushd)\s+[^&;]+\s*(?:&&|;)\s*)+", re.IGNORECASE)


def _service_tier(name: str) -> int:
    """按常见架构角色推导默认启动层级：0=数据库/中间件，1=后端/默认，2=前端。"""
    lowered = name.strip().lower()
    if not lowered:
        return 1
    tokens = [part for part in re.split(r"[^a-z0-9]+", lowered) if part]
    if any(
        token in _DB_HINTS
        or any(
            hint in token for hint in ("postgres", "redis", "mongo", "mysql", "mailpit")
        )
        for token in tokens
    ):
        return 0
    if any(token in _FRONTEND_HINTS for token in tokens):
        return 2
    return 1


def _parse_depends_on(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]
    return []


def order_services(
    service_configs: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """根据显式 depends_on 与角色层级对服务进行稳定拓扑排序（DB -> 后端 -> 前端）。"""
    if len(service_configs) <= 1:
        return list(service_configs)
    by_name: dict[str, int] = {name: idx for idx, (name, _cfg) in enumerate(service_configs)}
    indegree = [0] * len(service_configs)
    dependents: list[list[int]] = [[] for _ in service_configs]
    tiers = [_service_tier(name) for name, _cfg in service_configs]

    for idx, (_name, cfg) in enumerate(service_configs):
        deps = _parse_depends_on(cfg.get("depends_on"))
        seen_deps: set[int] = set()
        for dep_name in deps:
            dep_idx = by_name.get(dep_name)
            if dep_idx is None or dep_idx == idx or dep_idx in seen_deps:
                continue
            seen_deps.add(dep_idx)
            dependents[dep_idx].append(idx)
            indegree[idx] += 1

    heap: list[tuple[int, int]] = []
    for idx, deg in enumerate(indegree):
        if deg == 0:
            heapq.heappush(heap, (tiers[idx], idx))

    ordered_indices: list[int] = []
    visited: set[int] = set()
    while heap:
        _tier, idx = heapq.heappop(heap)
        if idx in visited:
            continue
        visited.add(idx)
        ordered_indices.append(idx)
        for nxt in dependents[idx]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(heap, (tiers[nxt], nxt))

    if len(ordered_indices) < len(service_configs):
        remaining = sorted(
            (idx for idx in range(len(service_configs)) if idx not in visited),
            key=lambda idx: (tiers[idx], idx),
        )
        ordered_indices.extend(remaining)
    return [service_configs[idx] for idx in ordered_indices]


def _default_healthy(url: str) -> bool | None:
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            status = int(response.status)
            return status < 400 or status in {401, 403}
    except urllib.error.HTTPError as exc:
        return exc.code < 400 or exc.code in {401, 403}
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _service_target_url(cfg: dict[str, Any]) -> str:
    for key in ("health", "health_url", "base_url", "url"):
        val = cfg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _local_host_port(url: str) -> tuple[str, int] | None:
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().strip(".")
    if host not in _LOCAL_PROBE_HOSTS:
        return None
    port = parsed.port
    if port is None:
        if parsed.scheme.lower() == "https":
            port = 443
        elif parsed.scheme.lower() == "http":
            port = 80
    if port is None:
        return None
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    return probe_host, port


def _port_listening(host: str, port: int, *, scheme: str = "http") -> bool:
    """使用标准库 HTTPConnection.connect 仅做 TCP 建连探测，精准区分端口占用与空闲。"""
    del scheme
    conn = http.client.HTTPConnection(host, port, timeout=3.0)
    try:
        conn.connect()
        return True
    except OSError:
        return False
    finally:
        conn.close()


def check_service_ports(
    service_configs: list[tuple[str, dict[str, Any]]],
    *,
    healthy_fn: Callable[[str], bool | None] = _default_healthy,
) -> dict[str, Any]:
    """检查各服务目标端口状态：区分空闲、已有健康服务复用与被异常占用的冲突。"""
    checks: list[dict[str, Any]] = []
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    conflicted_services: list[str] = []
    reused_services: list[str] = []

    for name, cfg in service_configs:
        label = name or "default"
        url = _service_target_url(cfg)
        health_url = str(cfg.get("health") or cfg.get("health_url") or url).strip()
        start_cmd = str(cfg.get("start") or "").strip()
        host_port = _local_host_port(url)
        if host_port is None:
            checks.append(
                {
                    "service": label,
                    "url": url,
                    "port": None,
                    "status": "skipped",
                    "detail": "未配置本地 HTTP 端口或非本机地址",
                }
            )
            continue
        host, port = host_port
        scheme = urllib.parse.urlsplit(url).scheme.lower() or "http"
        in_use = _port_listening(host, port, scheme=scheme)
        if not in_use:
            checks.append(
                {
                    "service": label,
                    "url": url,
                    "port": port,
                    "status": "free",
                    "detail": f"端口 {port} 空闲，待启动",
                }
            )
            continue
        health_ok = healthy_fn(health_url) if health_url else None
        if health_ok is True:
            reused_services.append(label)
            checks.append(
                {
                    "service": label,
                    "url": url,
                    "port": port,
                    "status": "healthy_reuse",
                    "detail": f"端口 {port} 已有健康服务就绪，将直接复用",
                }
            )
        elif start_cmd:
            conflicted_services.append(label)
            reason = (
                f"服务 {label} 端口 {port} 已被其他进程占用"
                f"且健康检查未通过（{health_url or url}）；"
                "请先释放该端口或调整服务端口后再执行"
            )
            blocking_reasons.append(reason)
            checks.append(
                {
                    "service": label,
                    "url": url,
                    "port": port,
                    "status": "port_conflict",
                    "detail": reason,
                }
            )
        else:
            warning = f"服务 {label} 端口 {port} 已有监听进程但健康检查未通过，且未配置 start 命令"
            warnings.append(warning)
            checks.append(
                {
                    "service": label,
                    "url": url,
                    "port": port,
                    "status": "unhealthy_external",
                    "detail": warning,
                }
            )

    return {
        "checks": checks,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "conflicted_services": conflicted_services,
        "reused_services": reused_services,
    }


def _env_key_names(path: Path) -> dict[str, bool]:
    """提取 .env* 文件中的键名及其是否具有非空默认值（绝不返回键值原文）。"""
    keys: dict[str, bool] = {}
    try:
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            return keys
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return keys
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(stripped)
        if match:
            key = match.group(1)
            raw_val = match.group(2).strip()
            has_default = bool(raw_val and raw_val not in {'""', "''"})
            keys[key] = has_default
    return keys


def _discover_env_files(workdir: Path) -> tuple[set[str], list[tuple[str, str]]]:
    """发现工作区根目录与一层子目录的 .env 键及 .env.example 无默认值必填项。"""
    present_keys: set[str] = set()
    example_required: list[tuple[str, str]] = []
    candidate_dirs = [workdir]
    with contextlib.suppress(OSError):
        candidate_dirs.extend(
            child
            for child in sorted(workdir.iterdir())
            if child.is_dir() and not child.name.startswith(".") and child.name != "node_modules"
        )
    for directory in candidate_dirs[:12]:
        dotenv = directory / ".env"
        if dotenv.is_file():
            present_keys.update(_env_key_names(dotenv).keys())
        for example_name in (".env.example", ".env.template"):
            example_path = directory / example_name
            if example_path.is_file():
                rel = example_path.relative_to(workdir).as_posix()
                for key, has_default in _env_key_names(example_path).items():
                    if not has_default:
                        example_required.append((key, rel))
    return present_keys, example_required


def check_required_env(
    workdir: Path,
    config: dict[str, Any],
    run_env: dict[str, str],
) -> dict[str, Any]:
    """校验项目配置的 required_env 与 .env.example 必填键是否齐备（不回传明文值）。"""
    present_in_dotenv, example_required = _discover_env_files(workdir)
    checks: list[dict[str, str]] = []
    blocking_reasons: list[str] = []
    warnings: list[str] = []

    raw_required = config.get("required_env")
    if not isinstance(raw_required, list):
        env_cfg = config.get("env")
        raw_required = env_cfg.get("required") if isinstance(env_cfg, dict) else []
    configured_keys = [
        str(item).strip()
        for item in (raw_required if isinstance(raw_required, list) else [])
        if isinstance(item, str) and item.strip()
    ]

    for key in configured_keys:
        if bool(run_env.get(key)) or key in present_in_dotenv:
            checks.append({"key": key, "source": "config.required_env", "status": "ok"})
        else:
            msg = f"缺少必填环境变量 {key}（来自项目配置 required_env）"
            blocking_reasons.append(msg)
            checks.append({"key": key, "source": "config.required_env", "status": "missing"})

    seen_example: set[str] = set(configured_keys)
    for key, rel in example_required:
        if key in seen_example:
            continue
        seen_example.add(key)
        if bool(run_env.get(key)) or key in present_in_dotenv:
            checks.append({"key": key, "source": rel, "status": "ok"})
        else:
            msg = f"环境变量 {key} 在 {rel} 中未设默认值，且当前环境与 .env 未配置"
            warnings.append(msg)
            checks.append({"key": key, "source": rel, "status": "missing_optional"})

    return {
        "checks": checks,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }


def _extract_command_executable(command: str) -> str:
    text = _CHAIN_PREFIX_RE.sub("", command.strip())
    if not text:
        return ""
    try:
        tokens = shlex.split(text, posix=(os.name != "nt"))
    except ValueError:
        tokens = text.split()
    for token in tokens:
        cleaned = token.strip().strip('"').strip("'")
        if not cleaned:
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", cleaned):
            continue
        lowered = cleaned.lower()
        if lowered in _SHELL_BUILTINS or lowered in {"&&", "||", ";"}:
            continue
        return cleaned
    return ""


def check_service_tools(
    service_configs: list[tuple[str, dict[str, Any]]],
    workdir: Path,
    run_env: dict[str, str],
    *,
    reused_services: set[str] | None = None,
) -> dict[str, Any]:
    """检查待启动服务的 CLI 工具/解释器在当前 PATH 或工作区中是否存在。"""
    checks: list[dict[str, str]] = []
    blocking_reasons: list[str] = []
    reused = reused_services or set()
    search_path = run_env.get("PATH") or os.environ.get("PATH")

    for name, cfg in service_configs:
        label = name or "default"
        start_cmd = str(cfg.get("start") or "").strip()
        if not start_cmd or label in reused:
            continue
        exe = _extract_command_executable(start_cmd)
        if not exe:
            continue
        path_candidate = Path(exe)
        exists = False
        if path_candidate.is_absolute():
            exists = path_candidate.is_file()
        elif "/" in exe or "\\" in exe:
            exists = (workdir / path_candidate).is_file()
        else:
            exists = shutil.which(exe, path=search_path) is not None
        if exists:
            checks.append({"service": label, "tool": exe, "status": "ok"})
        else:
            msg = f"服务 {label} 启动命令所需工具 '{exe}' 不在 PATH 中"
            blocking_reasons.append(msg)
            checks.append({"service": label, "tool": exe, "status": "missing"})
    return {"checks": checks, "blocking_reasons": blocking_reasons}


def run_preflight(
    workdir: Path,
    config: dict[str, Any],
    service_configs: list[tuple[str, dict[str, Any]]],
    run_env: dict[str, str],
    *,
    healthy_fn: Callable[[str], bool | None] = _default_healthy,
) -> dict[str, Any]:
    """执行开测前完整环境安检门，汇总服务启动顺序、端口冲突、环境变量与依赖工具检查。"""
    ordered = order_services(service_configs)
    port_report = check_service_ports(ordered, healthy_fn=healthy_fn)
    env_report = check_required_env(workdir, config, run_env)
    tool_report = check_service_tools(
        ordered,
        workdir,
        run_env,
        reused_services=set(port_report["reused_services"]),
    )
    blocking_reasons = [
        *port_report["blocking_reasons"],
        *env_report["blocking_reasons"],
        *tool_report["blocking_reasons"],
    ]
    warnings = [
        *port_report["warnings"],
        *env_report["warnings"],
    ]
    return {
        "ok": not blocking_reasons,
        "ordered_services": [name or "default" for name, _cfg in ordered],
        "ordered_service_keys": [name for name, _cfg in ordered],
        "conflicted_services": list(port_report["conflicted_services"]),
        "reused_services": list(port_report["reused_services"]),
        "port_checks": port_report["checks"],
        "env_checks": env_report["checks"],
        "tool_checks": tool_report["checks"],
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
    }
