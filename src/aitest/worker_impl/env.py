"""Worker 拆分模块：env。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, TypeVar, cast

import aitest
from aitest.core.evidence import (
    DEFAULT_FILE_CAP_BYTES,
    data_prefix,
    save_evidence,
    settings_from_config,
)
from aitest.core.shared_config import load_merged_project_config
from aitest.errors import AiTestError
from aitest.models import Delivery, Run
from aitest.security import (
    is_secret_field,
)
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

T = TypeVar("T")


def worker_attr(name: str, default: T) -> T:
    """从 aitest.worker 门面动态解析可被测试 monkeypatch 替换的符号。"""
    facade = sys.modules.get("aitest.worker")
    return cast(T, getattr(facade, name, default)) if facade is not None else default

MAX_FILE_BYTES = DEFAULT_FILE_CAP_BYTES



SERVICES_FILE = "project.config.json"



DEFAULT_REQUEST_ID_HEADER = "X-Request-Id"



DELIVERY_INCOMPLETE_MESSAGE = "交付信息不完整：缺运行方式或版本"



_SINGLE_SERVICE_KEYS = frozenset({"start", "health", "stop"})



_SCREENSHOT_RE = re.compile(
    r"page\.screenshot\(\s*[^)]*?[\"']?path[\"']?\s*[=:]\s*[\"']([^\"']+)[\"']", re.DOTALL
)



_LOCK = threading.Lock()



EvidenceSettings = tuple[int, int, bool]



def _save(run_path: Path, run: Run) -> None:
    with _LOCK:
        write_model(run_path, run)



def _touch(run_path: Path) -> None:
    with _LOCK:
        current = read_model(run_path, Run)
        current.heartbeat_at = iso_now()
        write_model(run_path, current)



def _save_evidence(
    run_dir: Path,
    name: str,
    data: bytes,
    result: str,
    settings: EvidenceSettings,
    secrets: list[str],
    *,
    current_bytes: int | None = None,
) -> str | None:
    budget, file_cap, allow_video = settings
    return save_evidence(
        run_dir,
        name,
        data,
        result=result,
        budget_bytes=budget,
        allow_video=allow_video,
        file_cap_bytes=file_cap,
        secrets=secrets,
        current_bytes=current_bytes,
    )



_SUBSERVICE_DIRS = ("api", "app", "backend", "server")


def _venv_python(root: Path) -> Path | None:
    search_bases: list[Path] = [root]
    for name in _SUBSERVICE_DIRS:
        sub = root / name
        if sub.is_dir():
            search_bases.append(sub)
    search_bases.extend([root.parent, root.parent.parent])
    for base in search_bases:
        for candidate in (
            base / ".venv" / "Scripts" / "python.exe",
            base / ".venv" / "bin" / "python",
        ):
            if candidate.is_file():
                return candidate
    return None



def _read_env_dir(directory: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in (".env", ".env.local", ".env.test"):
        path = directory / name
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if entry.startswith("export "):
                entry = entry[len("export ") :].strip()
            key, separator, raw = entry.partition("=")
            key = key.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue
            value = raw.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
    return values


def _dotenv_values(root: Path) -> dict[str, str]:
    """读取项目及一级服务子目录中的 .env 配置，不做执行或记录日志。"""

    values = _read_env_dir(root)
    if not values:
        for name in _SUBSERVICE_DIRS:
            sub = root / name
            if not sub.is_dir():
                continue
            values = _read_env_dir(sub)
            if values:
                break
    for key, value in list(values.items()):
        values[key] = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda match: values.get(match.group(1), os.environ.get(match.group(1), "")),
            value,
        )
    return values


def _run_env(root: Path, python: Path | None, run_id: str) -> dict[str, str]:
    env: dict[str, str] = {}
    package_root = Path(aitest.__file__).resolve().parents[1]
    # src-layout projects (e.g. src/pkg) need their source root on PYTHONPATH;
    # plain layouts need the project root. Keep the host package out of it.
    extra_paths: list[str] = []
    for candidate in (root / "src", root):
        if candidate.is_dir() and candidate.resolve() != package_root:
            value = str(candidate.resolve())
            if value not in extra_paths:
                extra_paths.append(value)
    inherited_pythonpath: list[str] = []
    for key, value in os.environ.items():
        if is_secret_field(key):
            continue
        if key == "PYTHONPATH":
            inherited_pythonpath = [
                item
                for item in value.split(os.pathsep)
                if item and Path(item).resolve() != package_root
            ]
            continue
        env[key] = value
    for key, value in _dotenv_values(root).items():
        if key.startswith("AITEST_"):
            continue
        env.setdefault(key, value)
    # Chinese Windows defaults to GBK; modern projects store source and templates
    # as UTF-8. Keep the child on UTF-8 so locale decoding cannot create a false
    # product failure. A project .env may still override these values above.
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Playwright's HTML reporter otherwise keeps a report server alive after a
    # failed run, which looks like a hung test command.
    env.setdefault("PLAYWRIGHT_HTML_OPEN", "never")
    pythonpath: list[str] = []
    for item in [*extra_paths, *inherited_pythonpath]:
        if item and item not in pythonpath:
            pythonpath.append(item)
    if pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    effective_python = python or Path(sys.executable)
    # Always put the interpreter we actually use first. A project without its
    # own .venv would otherwise let a bare `python` in a command resolve to an
    # unrelated installation from the parent PATH.
    env["PATH"] = str(effective_python.parent) + os.pathsep + env.get("PATH", "")
    if python is not None:
        env["VIRTUAL_ENV"] = str(python.parent.parent)
    else:
        venv_root = effective_python.parent.parent
        if (venv_root / "pyvenv.cfg").is_file():
            env["VIRTUAL_ENV"] = str(venv_root)
    # A parent interpreter's PYTHONHOME poisons the project interpreter and
    # makes its stdlib mix with a different Python version.
    env.pop("PYTHONHOME", None)
    env["AITEST_RUN_ID"] = run_id
    env["AITEST_DATA_PREFIX"] = data_prefix(run_id)
    env["AITEST_PROJECT_ROOT"] = str(root.resolve())
    return env



def _environment_fingerprint(python: Path, workdir: Path) -> dict[str, str]:
    """Record the interpreter and platform that actually produced the result."""

    info: dict[str, str] = {"python": str(python), "platform": sys.platform}
    try:
        completed = subprocess.run(
            [str(python), "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if completed.returncode == 0:
            info["python_version"] = completed.stdout.decode(
                "utf-8", errors="replace"
            ).strip()
    except (OSError, subprocess.SubprocessError):
        pass
    venv_root = python.parent.parent
    info["venv"] = str(venv_root) if (venv_root / "pyvenv.cfg").is_file() else ""
    project_node = _project_tool(workdir, "node")
    node = project_node or shutil.which("node")
    if node:
        info["node_path"] = str(node)
        try:
            completed = subprocess.run(
                [node, "--version"], capture_output=True, timeout=10, check=False
            )
            if completed.returncode == 0:
                info["node"] = completed.stdout.decode(
                    "utf-8", errors="replace"
                ).strip()
        except (OSError, subprocess.SubprocessError):
            pass
    for lock in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock"):
        if (workdir / lock).is_file():
            info["node_lock"] = lock
            break
    info["cwd"] = str(workdir)
    return info



def _project_tool(workdir: Path, name: str) -> str:
    """Prefer the project's own toolchain before the host PATH."""
    for folder in (workdir / "node_modules" / ".bin", workdir / ".bin"):
        for candidate in (folder / name, folder / f"{name}.cmd", folder / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
    return ""



def _project_config(ws: Workspace, project_id: str) -> dict[str, object]:
    path = ws.project_dir(project_id) / SERVICES_FILE
    local: dict[str, object] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AiTestError(
                "CONFIG_INVALID",
                "项目运行配置无法读取",
                "修正 project.config.json 或重新生成配置后再运行",
            ) from exc
        if isinstance(data, dict):
            local = data
    return cast(dict[str, object], load_merged_project_config(ws, project_id, local))



def _delivery_incomplete(ws: Workspace, project_id: str, task_id: str) -> bool:
    path = ws.project_dir(project_id) / "deliveries" / f"{task_id}.json"
    if not path.is_file():
        return False
    try:
        delivery = read_model(path, Delivery)
    except AiTestError:
        return False
    return not delivery.complete



def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default



def _evidence_settings(config: dict[str, object]) -> EvidenceSettings:
    return settings_from_config(config)



def _request_id_header(config: dict[str, object]) -> str:
    value = config.get("link")
    link = value if isinstance(value, dict) else {}
    header = link.get("request_id_header", DEFAULT_REQUEST_ID_HEADER)
    return header if isinstance(header, str) else DEFAULT_REQUEST_ID_HEADER



def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)



def _write_action(ws: Workspace, entry: dict[str, Any]) -> None:
    path = ws.root / "actions" / f"{iso_now()[:7]}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")



def _heartbeat(run_path: Path, stop: threading.Event) -> None:
    while not stop.wait(3.0):
        try:
            _touch(run_path)
        except Exception:
            return



