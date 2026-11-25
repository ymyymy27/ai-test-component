"""Worker 拆分模块：deps_and_l1。"""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from aitest.core.project_context import (
    package_name,
    test_dependency_specs,
)
from aitest.model_config import build_client
from aitest.runners.base import RunnerResult
from aitest.runners.command_runner import run_command
from aitest.runners.package_install import install_packages
from aitest.storage.workspace import Workspace, iso_now
from aitest.worker_impl.env import (
    EvidenceSettings,
    _as_float,
    _save_evidence,
    _write_action,
    worker_attr,
)


def _missing_dependencies(
    python: Path,
    dependencies: list[str],
    env: dict[str, str] | None,
) -> list[str] | None:
    """Return requirements not importable by the project interpreter.

    ``None`` means the interpreter could not be probed; callers must not treat
    that as "all installed".
    """
    specs = sorted({item.strip() for item in dependencies if item.strip()})
    if not specs:
        return []
    probe = (
        "import importlib.metadata as m, sys\n"
        "try:\n"
        "    from packaging.requirements import Requirement\n"
        "except Exception:\n"
        "    Requirement = None\n"
        "missing = []\n"
        "for spec in sys.argv[1:]:\n"
        "    req = Requirement(spec) if Requirement is not None else None\n"
        "    name = req.name if req is not None else spec\n"
        "    try:\n"
        "        version = m.version(name)\n"
        "    except Exception:\n"
        "        missing.append(spec)\n"
        "        continue\n"
        "    if req is not None and req.specifier and version not in req.specifier:\n"
        "        missing.append(spec)\n"
        "print('\\n'.join(missing))\n"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", probe, *specs],
            capture_output=True,
            timeout=60,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    output = (completed.stdout or b"").decode("utf-8", errors="replace")
    return [line.strip() for line in output.splitlines() if line.strip()]



def _install_dependencies(
    ws: Workspace,
    run_dir: Path,
    python: Path,
    dependencies: list[str],
    settings: EvidenceSettings,
    secrets: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[bool, str | None]:
    if not dependencies:
        return True, None
    candidate = "evidence/deps-install.log"
    missing = _missing_dependencies(python, dependencies, env)
    if missing == []:
        note = "依赖已可导入，跳过 pip 安装：".encode() + ", ".join(
            dependencies
        ).encode()
        _save_evidence(
            run_dir, "deps-install.log", note, "passed", settings, secrets
        )
        ref: str | None = candidate if (run_dir / candidate).is_file() else None
        _write_action(
            ws,
            {
                "at": iso_now(),
                "action": "install_dependencies",
                "dependencies": dependencies,
                "exit_code": 0,
                "ok": True,
                "skipped": True,
            },
        )
        return True, ref
    shared_host_env = False
    if cwd is not None:
        with contextlib.suppress(OSError):
            py_res = python.resolve()
            cwd_res = cwd.resolve()
            sys_res = Path(sys.executable).resolve()
            if py_res == sys_res and not py_res.is_relative_to(cwd_res):
                shared_host_env = True

    installed = install_packages(
        python,
        dependencies,
        cwd or python.parent,
        env,
        timeout_seconds=600,
    )
    output = installed.output.decode("utf-8", errors="replace")
    if shared_host_env:
        output = (
            f"[environment] notice: target project lacks an isolated .venv; "
            f"installed into shared host interpreter: {python}\n"
            + output
        )
    ok = installed.ok
    exit_code = installed.exit_code
    _save_evidence(
        run_dir,
        "deps-install.log",
        output.encode("utf-8", errors="replace"),
        "passed" if ok else "failed",
        settings,
        secrets,
    )
    candidate = "evidence/deps-install.log"
    ref = candidate if (run_dir / candidate).is_file() else None
    _write_action(
        ws,
        {
            "at": iso_now(),
            "action": "install_dependencies",
            "dependencies": dependencies,
            "exit_code": exit_code,
            "ok": ok,
            "installer": installed.installer,
            "attempts": installed.attempts,
            "shared_host_env": shared_host_env,
        },
    )
    return ok, ref



def _l1_output(result: RunnerResult) -> bytes:
    data = result.stdout + result.stderr
    if not data and result.error:
        data = result.error.encode("utf-8", errors="replace")
    return data



def _l1_entry_command(python: Path, module: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module):
        return None
    return f'"{python}" -c "import {module}"'



class L1Build(TypedDict):
    ok: bool
    exit_code: int | None
    evidence_ref: str | None



class L1Entry(TypedDict):
    module: str
    ok: bool
    evidence_ref: str | None



class L1Result(TypedDict):
    build: L1Build
    entries: list[L1Entry]
    cancelled: bool



def _run_l1(
    config: dict[str, object],
    workdir: Path,
    run_dir: Path,
    env: dict[str, str],
    python: Path,
    settings: EvidenceSettings,
    secrets: list[str],
    cancelled: Callable[[], bool] | None = None,
    skip_build: bool = False,
) -> L1Result | None:
    """Run the project-level build and entry imports before any case."""
    value = config.get("l1")
    if not isinstance(value, dict):
        return None
    timeout = _as_float(value.get("timeout_seconds"), 120.0)
    build_command = value.get("build")
    build: L1Build = {"ok": True, "exit_code": None, "evidence_ref": None}
    entries: list[L1Entry] = []
    if not skip_build and isinstance(build_command, str) and build_command.strip():
        if cancelled is not None and cancelled():
            return {"build": build, "entries": entries, "cancelled": True}
        result = worker_attr("run_command", run_command)(
            build_command,
            cwd=workdir,
            env=env,
            timeout_seconds=timeout,
            cancelled=cancelled,
        )
        build = {
            "ok": result.status == "passed",
            "exit_code": result.exit_code,
            "evidence_ref": _save_evidence(
                run_dir,
                "l1-build.log",
                _l1_output(result),
                result.status,
                settings,
                secrets,
            ),
        }
    raw_entries = value.get("entry")
    modules = (
        [item for item in raw_entries if isinstance(item, str) and item.strip()]
        if isinstance(raw_entries, list)
        else []
    )
    for index, module in enumerate(modules):
        if cancelled is not None and cancelled():
            return {"build": build, "entries": entries, "cancelled": True}
        command = _l1_entry_command(python, module)
        if command is None:
            data = f"入口模块名不合法：{module}\n".encode()
            entries.append(
                {
                    "module": module,
                    "ok": False,
                    "evidence_ref": _save_evidence(
                        run_dir,
                        f"l1-entry-{index}.log",
                        data,
                        "failed",
                        settings,
                        secrets,
                    ),
                }
            )
            continue
        result = worker_attr("run_command", run_command)(
            command,
            cwd=workdir,
            env=env,
            timeout_seconds=timeout,
            cancelled=cancelled,
        )
        entries.append(
            {
                "module": module,
                "ok": result.status == "passed",
                "evidence_ref": _save_evidence(
                    run_dir,
                    f"l1-entry-{index}.log",
                    _l1_output(result),
                    result.status,
                    settings,
                    secrets,
                ),
            }
        )
    return {"build": build, "entries": entries, "cancelled": False}



_KNOWN_TOOLS = frozenset(
    {
        "python",
        "python3",
        "pytest",
        "tox",
        "uv",
        "poetry",
        "npm",
        "npx",
        "node",
        "yarn",
        "pnpm",
        "docker",
    }
)



def _missing_command_tool(method: str, env: dict[str, str]) -> str:
    """Return a missing leading tool for simple commands, or an empty string."""

    if not method.startswith("command:"):
        return ""
    command = method.split(":", 1)[1].strip()
    if not command or any(char in command for char in "|&;<>"):
        return ""
    first = command.split()[0].strip("\"'")
    if not first:
        return ""
    candidate = Path(first)
    base = candidate.name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base not in _KNOWN_TOOLS:
        return ""
    if candidate.is_absolute() and candidate.is_file():
        return ""
    if shutil.which(base, path=env.get("PATH")):
        return ""
    return base



_MODULE_PACKAGES = {
    "factory": "factory-boy",
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "jose": "python-jose",
    "psycopg2": "psycopg2-binary",
    "MySQLdb": "mysqlclient",
    "OpenSSL": "pyOpenSSL",
    "rest_framework": "djangorestframework",
    "django": "Django",
}



def _missing_modules_from_output(output: bytes) -> list[str]:
    text = output.decode("utf-8", errors="replace")
    modules: list[str] = []
    for match in _MISSING_MODULE_RE.finditer(text):
        name = match.group(1).split(".", 1)[0]
        if name and name not in modules:
            modules.append(name)
    return modules



def _missing_module_from_output(output: bytes) -> str:
    modules = _missing_modules_from_output(output)
    return modules[0] if modules else ""



def _resolve_dependency_package(
    ws: Workspace, project_id: str, module: str, command: str
) -> str:
    """Map an import module to a pip package, preferring the configured model."""

    mapped = _MODULE_PACKAGES.get(module, "")
    if mapped:
        return mapped
    try:
        client = build_client(ws, project_id)
    except Exception:
        return ""
    if client is None:
        return ""
    try:
        return client.suggest_dependency(
            module, {"command": command, "project_id": project_id}
        )
    except Exception:
        return ""



def _dependency_suggestion(
    ws: Workspace, project_id: str, root: Path, module: str, command: str
) -> dict[str, str]:
    """Build a reviewable install suggestion; never installs by itself."""

    package = _resolve_dependency_package(ws, project_id, module, command)
    if not package:
        return {}
    declared = next(
        (
            spec
            for spec in test_dependency_specs(root)
            if package_name(spec) == package_name(package)
        ),
        "",
    )
    if declared:
        return {
            "module": module,
            "package": declared,
            "source": "declared",
            "reason": "项目测试依赖已声明，直接使用声明的版本范围",
        }
    source = "mapping" if module in _MODULE_PACKAGES else "ai"
    reason = (
        "根据常见模块名映射；版本未锁定"
        if source == "mapping"
        else "由模型根据模块名解析；版本未锁定，安装前需人工确认"
    )
    return {
        "module": module,
        "package": package,
        "source": source,
        "reason": reason,
    }



def _project_top_level_names(root: Path) -> set[str]:
    names: set[str] = set()
    for base in (root, root / "src"):
        if not base.is_dir():
            continue
        for item in base.iterdir():
            if item.is_dir() and (item / "__init__.py").is_file():
                names.add(item.name)
            elif item.is_file() and item.suffix == ".py":
                names.add(item.stem)
    return names



_MISSING_MODULE_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError): No module named ['\"]([^'\"]+)['\"]"
)



def _has_product_failure(output: bytes) -> bool:
    """True when output already proves a real assertion/test failure.

    Environment classification must never override this: a mixed run (real
    failure plus a missing package) stays a product failure.
    """

    text = output.decode("utf-8", errors="replace")
    return bool(
        re.search(r"(?m)^FAIL:", text)
        or re.search(r"(?m)^FAILED", text)
        or re.search(r"(?m)^=+ FAILURES =+", text)
        or "AssertionError" in text
        or re.search(r"(?m)^E\s+assert\b", text)
    )



def _environment_failure(output: bytes, workdir: Path) -> str:
    """Classify command failures caused by the local environment, not the product.

    A missing third-party test dependency or an unavailable database must not be
    reported as a product failure. Missing project-local modules are left as
    failures because they may be a real packaging defect.
    """

    # A mixed run (real assertion failures plus missing dependencies) must stay
    # a failure; otherwise a real defect could be hidden behind an environment
    # problem.
    if _has_product_failure(output):
        return ""
    text = output.decode("utf-8", errors="replace")
    match = _MISSING_MODULE_RE.search(text)
    if match:
        top_level = match.group(1).split(".", 1)[0]
        if top_level not in _project_top_level_names(workdir):
            return f"环境缺少依赖：{top_level}（不计为产品失败）"
    lowered = text.lower()
    if (
        "is not recognized as an internal or external command" in lowered
        or "command not found" in lowered
    ):
        return "环境缺少命令（不计为产品失败）"
    if "executable doesn't exist" in lowered and (
        "playwright" in lowered
        or "chrome-headless" in lowered
        or "chromium" in lowered
    ):
        return "Playwright 浏览器未安装（不计为产品失败）"
    if (
        "connection refused" in lowered
        or "could not connect to server" in lowered
        or "unknown database" in lowered
    ):
        return "环境服务未就绪（不计为产品失败）"
    return ""



def _case_dependencies_ready(
    python: Path,
    dependencies: list[str],
    env: dict[str, str],
    cache: dict[tuple[str, ...], list[str] | None],
) -> tuple[bool, list[str] | None]:
    """Per-case dependency gate.

    A missing package for one case must not block unrelated cases that only
    need packages already available in the interpreter.
    """
    if not dependencies:
        return True, []
    key = tuple(sorted(dependencies))
    if key not in cache:
        cache[key] = _missing_dependencies(python, dependencies, env)
    missing = cache[key]
    if missing is None:
        return False, None
    return not missing, missing



