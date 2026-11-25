"""4 大可扩展插槽体系与内置插件注册中心。

统一抽取并标准化 4 个核心扩展插槽，全部面向核心零绑定工作空间：
1. ContextProvider：按技术栈提取上下文事实；
2. DeterministicProbe：免 AI 执行的确定性体检探针；
3. ScriptLinter：按脚本语言分发的跑前静态安检器；
4. Isolator：运行前/后轻量数据隔离与本地文件快照还原器。
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Any, Protocol

from aitest.core.context.contracts import (
    _api_routes,
    _frontend_api_calls,
    _frontend_assets,
    _openapi_routes,
)
from aitest.core.context.manifests import (
    _bun_tests,
    _component_manifests,
    _frameworks,
    _playwright_tests,
    _pyproject_dependencies,
    _symbol_map,
    _test_dirs,
    _test_function_map,
    _test_function_summaries,
)
from aitest.core.context.services import _services_with_health, detect_services
from aitest.core.evidence import data_prefix
from aitest.core.live_page_probe import LivePageProbe
from aitest.core.probes import BUILTIN_PROBES, DeterministicProbe
from aitest.core.script_linter import (
    JsTsScriptLinter,
    LintIssue,
    PythonScriptLinter,
    ScriptLinter,
)
from aitest.security import ensure_within


class ContextProvider(Protocol):
    """项目画像上下文提取插槽协议。"""

    provider_id: str

    def supports(self, root: Path) -> bool:
        """判断当前仓库是否适用本上下文提取器。"""
        ...

    def extract(self, root: Path, config: dict[str, Any]) -> dict[str, Any]:
        """提取结构化上下文片段并合并入总项目上下文。"""
        ...


class Isolator(Protocol):
    """测试运行数据隔离器插槽协议。"""

    isolator_id: str

    def prepare(
        self, workdir: Path, run_id: str, run_dir: Path, config: dict[str, Any]
    ) -> dict[str, Any]:
        """运行开始前创建数据隔离环境并返回待清理状态字典。"""
        ...

    def cleanup(self, state: dict[str, Any]) -> None:
        """运行结束（含失败/取消）后安全清理或还原隔离状态。"""
        ...


class PythonBackendContextProvider:
    """提取 Python 后端框架、测试函数、路由及符号索引的内置上下文提供者。"""

    provider_id = "python_backend"

    def supports(self, root: Path) -> bool:
        return (
            (root / "pyproject.toml").is_file()
            or (root / "requirements.txt").is_file()
            or any(root.glob("*/pyproject.toml"))
            or any(root.glob("*.py"))
        )

    def extract(self, root: Path, config: dict[str, Any]) -> dict[str, Any]:
        del config
        components = _component_manifests(root)
        frameworks: list[str] = []
        for component, pyproj, _pkg in components:
            deps = _pyproject_dependencies(pyproj)
            for fw in _frameworks(component, deps, pyproj):
                if fw not in frameworks:
                    frameworks.append(fw)
        test_dirs = _test_dirs(root)
        return {
            "framework": frameworks,
            "api_routes": _api_routes(root),
            "symbols": _symbol_map(root),
            "test_functions": _test_function_map(root, test_dirs),
            "test_summaries": _test_function_summaries(root, test_dirs),
        }


class FrontendWebContextProvider:
    """提取前端页面、API 调用、静态资产以及 Playwright/Bun E2E 的内置提供者。"""

    provider_id = "frontend_web"

    def supports(self, root: Path) -> bool:
        if (root / "package.json").is_file() or any(root.glob("*/package.json")):
            return True
        return any(root.glob("*.html")) or any(root.glob("*/*.html"))

    def extract(self, root: Path, config: dict[str, Any]) -> dict[str, Any]:
        services = _services_with_health(config.get("services"))
        if not services:
            services = _services_with_health(detect_services(root))
        return {
            "frontend_api_calls": _frontend_api_calls(root),
            "frontend_assets": _frontend_assets(root),
            "playwright_tests": _playwright_tests(root),
            "bun_tests": _bun_tests(root),
            "services": services,
        }


class OpenApiContextProvider:
    """从仓库内 OpenAPI / Swagger 描述文档补充契约路由的内置提供者。"""

    provider_id = "openapi_spec"

    def supports(self, root: Path) -> bool:
        for name in ("openapi.json", "swagger.json"):
            if (root / name).is_file() or any(root.glob(f"*/{name}")):
                return True
        return False

    def extract(self, root: Path, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"openapi_routes": _openapi_routes(root)}


class PrefixIsolator:
    """为单次运行分配独立测试数据前缀的轻量隔离器。"""

    isolator_id = "prefix_isolator"

    def prepare(
        self, workdir: Path, run_id: str, run_dir: Path, config: dict[str, Any]
    ) -> dict[str, Any]:
        del workdir, run_dir, config
        return {"data_prefix": data_prefix(run_id)}

    def cleanup(self, state: dict[str, Any]) -> None:
        del state


class LocalFileSnapshotIsolator:
    """在测试运行前后对配置声明的本地数据文件进行物理备份与还原。"""

    isolator_id = "local_file_snapshot"

    def prepare(
        self, workdir: Path, run_id: str, run_dir: Path, config: dict[str, Any]
    ) -> dict[str, Any]:
        del run_id
        data_cfg = config.get("data")
        if not isinstance(data_cfg, dict):
            return {"restored": [], "snapshots": []}
        raw_files = data_cfg.get("snapshot_files")
        if not isinstance(raw_files, list) or not raw_files:
            return {"restored": [], "snapshots": []}

        backup_root = run_dir / "_snapshots"
        snapshots: list[dict[str, str]] = []
        missing_before: list[str] = []
        root_resolved = workdir.resolve()
        for idx, item in enumerate(raw_files[:30]):
            if not isinstance(item, str) or not item.strip():
                continue
            rel = item.strip().replace("\\", "/")
            candidate = Path(rel)
            if candidate.is_absolute() or ".." in candidate.parts:
                continue
            try:
                target = ensure_within(root_resolved, root_resolved / rel)
            except ValueError:
                continue
            if target.is_file():
                backup_root.mkdir(parents=True, exist_ok=True)
                snap = backup_root / f"{idx}_{target.name}"
                with contextlib.suppress(OSError):
                    shutil.copy2(target, snap)
                    snapshots.append({"target": str(target), "snapshot": str(snap)})
            elif not target.exists():
                missing_before.append(str(target))
        return {
            "snapshots": snapshots,
            "missing_before": missing_before,
            "backup_root": str(backup_root),
        }

    def cleanup(self, state: dict[str, Any]) -> None:
        for entry in state.get("snapshots") or []:
            if not isinstance(entry, dict):
                continue
            snap = Path(str(entry.get("snapshot") or ""))
            target = Path(str(entry.get("target") or ""))
            if snap.is_file() and str(target):
                target.parent.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(OSError):
                    shutil.copy2(snap, target)
        for missing_path in state.get("missing_before") or []:
            target = Path(str(missing_path))
            if target.is_file():
                with contextlib.suppress(OSError):
                    target.unlink()
        backup_raw = str(state.get("backup_root") or "")
        if backup_raw:
            backup_root = Path(backup_raw)
            if backup_root.is_dir():
                with contextlib.suppress(OSError):
                    shutil.rmtree(backup_root, ignore_errors=True)


class PluginRegistry:
    """管理 4 大插槽注册与分发的扩展注册表。"""

    def __init__(self) -> None:
        self._context_providers: list[ContextProvider] = []
        self._probes: list[DeterministicProbe] = []
        self._linters: list[ScriptLinter] = []
        self._isolators: list[Isolator] = []

    @classmethod
    def with_builtins(cls) -> PluginRegistry:
        """创建并预装全部内置 4 类扩展的默认注册表。"""
        reg = cls()
        reg.register_context_provider(PythonBackendContextProvider())
        reg.register_context_provider(FrontendWebContextProvider())
        reg.register_context_provider(OpenApiContextProvider())
        for probe in BUILTIN_PROBES:
            reg.register_probe(probe)
        reg.register_probe(LivePageProbe())
        reg.register_linter(PythonScriptLinter())
        reg.register_linter(JsTsScriptLinter())
        reg.register_isolator(PrefixIsolator())
        reg.register_isolator(LocalFileSnapshotIsolator())
        return reg

    def register_context_provider(self, provider: ContextProvider) -> None:
        self._context_providers = [
            item
            for item in self._context_providers
            if item.provider_id != provider.provider_id
        ]
        self._context_providers.append(provider)

    def register_probe(self, probe: DeterministicProbe) -> None:
        self._probes = [item for item in self._probes if item.probe_id != probe.probe_id]
        self._probes.append(probe)

    def register_linter(self, linter: ScriptLinter) -> None:
        self._linters.append(linter)

    def register_isolator(self, isolator: Isolator) -> None:
        self._isolators = [
            item for item in self._isolators if item.isolator_id != isolator.isolator_id
        ]
        self._isolators.append(isolator)

    def context_providers(self) -> list[ContextProvider]:
        return list(self._context_providers)

    def probes(self) -> list[DeterministicProbe]:
        return list(self._probes)

    def linters(self) -> list[ScriptLinter]:
        return list(self._linters)

    def isolators(self) -> list[Isolator]:
        return list(self._isolators)

    def collect_context(self, root: Path, config: dict[str, Any]) -> dict[str, Any]:
        """依次运行匹配当前仓库的 ContextProvider，汇总结构化上下文。"""
        merged: dict[str, Any] = {"providers": []}
        for provider in self._context_providers:
            if not provider.supports(root):
                continue
            merged["providers"].append(provider.provider_id)
            fragment = provider.extract(root, config)
            for key, value in fragment.items():
                if key not in merged:
                    merged[key] = value
        return merged

    def lint(self, name: str, content: str) -> list[LintIssue]:
        """按脚本扩展名运行注册的 ScriptLinter。"""
        suffix = Path(name.strip()).suffix.lower() if name.strip() else ".py"
        issues: list[LintIssue] = []
        for linter in self._linters:
            if suffix in linter.extensions:
                issues.extend(linter.lint(content))
        return issues

    def prepare_isolators(
        self, workdir: Path, run_id: str, run_dir: Path, config: dict[str, Any]
    ) -> list[tuple[Isolator, dict[str, Any]]]:
        """在执行用例前依次准备全部隔离器并收集状态句柄。"""
        states: list[tuple[Isolator, dict[str, Any]]] = []
        for isolator in self._isolators:
            state = isolator.prepare(workdir, run_id, run_dir, config)
            states.append((isolator, state))
        return states

    def cleanup_isolators(self, states: list[tuple[Isolator, dict[str, Any]]]) -> None:
        """逆序回收隔离器状态并还原本地文件快照。"""
        for isolator, state in reversed(states):
            with contextlib.suppress(Exception):
                isolator.cleanup(state)


DEFAULT_REGISTRY = PluginRegistry.with_builtins()
