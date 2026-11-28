"""清单、依赖、框架、测试目录与符号映射读取。"""

from __future__ import annotations

import ast
import json
import os
import re
import tomllib
from contextlib import suppress
from pathlib import Path
from typing import Any

from aitest.core.repo_index import (
    DEFAULT_SKIP_DIRS,
    detect_package_runner,
    discover_monorepo_packages,
)
from aitest.security import is_ignored_path, load_ignore_patterns

_FRAMEWORK_ORDER = ("pytest", "vitest", "jest", "playwright", "fastapi", "flask", "django")

_DEPENDENCY_FRAMEWORKS = {
    "pytest": "pytest",
    "vitest": "vitest",
    "jest": "jest",
    "playwright": "playwright",
    "@playwright/test": "playwright",
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
}

_FRAMEWORK_FILES = {
    "pytest": ("pytest.ini", ".pytest.ini", "conftest.py"),
    "vitest": ("vitest.config.ts", "vitest.config.js", "vitest.config.mts"),
    "jest": (
        "jest.config.js",
        "jest.config.ts",
        "jest.config.mjs",
        "jest.config.cjs",
    ),
    "playwright": (
        "playwright.config.ts",
        "playwright.config.js",
        "playwright.config.mjs",
    ),
    "django": ("manage.py",),
}

_README_COMMAND = re.compile(
    r"^(?:"
    r"(?:python3?|py)\s+-m\s+(?:pytest|uvicorn|flask|django-admin)\b[^\n]*"
    r"|pytest\b[^\n]*"
    r"|(?:npm|yarn|pnpm)\s+(?:test|start|run\s+[\w:.-]+)\b[^\n]*"
    r"|npx\s+[\w@./-]+\b[^\n]*"
    r"|uvicorn\s+\S+[^\n]*"
    r"|flask\s+run\b[^\n]*"
    r"|django-admin\s+\S+[^\n]*"
    r"|(?:go|cargo)\s+(?:test|run)\b[^\n]*"
    r"|make\s+[\w.-]+"
    r"|docker\s+compose\s+up\b[^\n]*"
    r"|(?:mvn|gradlew?|gradle)\s+\S+[^\n]*"
    r")",
    re.IGNORECASE,
)

_PACKAGE_SCRIPT_NAMES = ("test", "dev", "start", "build")
_TEST_DEPENDENCY_GROUPS = frozenset({"test", "tests", "testing", "dev", "development"})
_NODE_ONLY_DEPENDENCIES = frozenset({"bun", "npm", "node", "yarn", "pnpm"})
_TEST_DIR_NAMES = frozenset({"tests", "test", "testing"})
_SKIP_WALK_DIRS = DEFAULT_SKIP_DIRS
_MAX_TEST_DIRS = 60
_MAX_TEST_DIR_DEPTH = 5


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _requirement_names(value: object) -> set[str]:
    if isinstance(value, str):
        entries = [value]
    elif isinstance(value, list):
        entries = [item for item in value if isinstance(item, str)]
    else:
        return set()
    names: set[str] = set()
    for entry in entries:
        base = entry.split(";", 1)[0].strip()
        name = re.split(r"[<>=!~\[\s]", base, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def _requirement_specs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    specs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        spec = item.split("#", 1)[0].strip()
        if " @ " in spec:
            spec = spec.split(" @ ", 1)[0].strip()
        spec = spec.split(";", 1)[0].strip()
        if not spec or spec.startswith(("-", ".", "/", "git+", "http")):
            continue
        specs.append(spec)
    return specs


def _unique(values: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = " ".join(value.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if limit is not None and len(result) >= limit:
            break
    return result


def test_dependency_specs(root: Path) -> list[str]:
    """Requirements declared for tests; used before suggesting an install."""
    pyproject = _read_toml(root / "pyproject.toml")
    specs: list[str] = []
    project = pyproject.get("project")
    if isinstance(project, dict):
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for key, value in optional.items():
                if str(key).lower() in _TEST_DEPENDENCY_GROUPS:
                    specs.extend(_requirement_specs(value))
    groups = pyproject.get("dependency-groups")
    if isinstance(groups, dict):
        for key, value in groups.items():
            if str(key).lower() in _TEST_DEPENDENCY_GROUPS:
                specs.extend(_requirement_specs(value))
    patterns = ("requirements-dev*.txt", "requirements-test*.txt", "requirements-tests*.txt")
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            specs.extend(_requirement_specs(lines))
    return _unique(specs, 40)


def package_name(spec: str) -> str:
    return re.split(r"[<>=!~\[; ]", spec.strip(), maxsplit=1)[0].lower()


def python_dependencies(values: list[str]) -> list[str]:
    """Keep Python requirements; Node tools must be installed by their own manager."""
    result: list[str] = []
    for value in values:
        name = re.split(r"[<>=!~\[; ]", value.strip(), maxsplit=1)[0].lower()
        if not name or name in _NODE_ONLY_DEPENDENCIES or value.startswith("@"):
            continue
        if value not in result:
            result.append(value)
    return result


def _pyproject_dependencies(data: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    project = data.get("project")
    if isinstance(project, dict):
        names.update(_requirement_names(project.get("dependencies")))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for value in optional.values():
                names.update(_requirement_names(value))
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for value in groups.values():
            names.update(_requirement_names(value))
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            dependencies = poetry.get("dependencies")
            if isinstance(dependencies, dict):
                names.update(str(key).lower() for key in dependencies)
    return names


def _package_dependencies(data: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        dependencies = data.get(key)
        if isinstance(dependencies, dict):
            names.update(str(name).lower() for name in dependencies)
    return names


def _has_test_module(path: Path) -> bool:
    patterns = (
        "test_*.py",
        "*_test.py",
        "test_*.ts",
        "test_*.tsx",
        "test_*.js",
        "test_*.jsx",
        "*.spec.ts",
        "*.spec.tsx",
        "*.spec.js",
        "*.spec.jsx",
    )
    return any(next(path.rglob(pattern), None) is not None for pattern in patterns)


def _component_manifests(root: Path) -> list[tuple[Path, dict[str, Any], dict[str, Any]]]:
    """Root plus workspace / subproject packages discovered via repo_index."""
    pkg_dirs = discover_monorepo_packages(root)
    if not pkg_dirs:
        return [(root, _read_toml(root / "pyproject.toml"), _read_json(root / "package.json"))]
    return [
        (pkg_dir, _read_toml(pkg_dir / "pyproject.toml"), _read_json(pkg_dir / "package.json"))
        for pkg_dir in pkg_dirs
    ]


def _test_dirs(root: Path) -> list[str]:
    """Find test directories in nested layouts without walking dependency trees."""
    found: list[str] = []
    for name in ("tests", "test", "testing"):
        if (root / name).is_dir():
            found.append(f"{name}/")
    for pattern in ("src/**/tests", "src/**/testing"):
        for path in sorted(root.glob(pattern)):
            if not path.is_dir():
                continue
            relative = path.relative_to(root).as_posix()
            marker = f"{relative}/"
            if marker not in found:
                found.append(marker)

    ignore_patterns = load_ignore_patterns(root)
    for current, dirnames, _files in os.walk(root):
        base = Path(current)
        depth = len(base.relative_to(root).parts)
        if depth >= _MAX_TEST_DIR_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name.lower() not in _SKIP_WALK_DIRS
            and not name.startswith(".")
            and not is_ignored_path(root, base / name, ignore_patterns)
        )
        for name in list(dirnames):
            if name not in _TEST_DIR_NAMES:
                continue
            path = base / name
            if not _has_test_module(path):
                continue
            relative = path.relative_to(root).as_posix()
            marker = f"{relative}/"
            if marker not in found:
                found.append(marker)
            dirnames.remove(name)
        if len(found) >= _MAX_TEST_DIRS:
            break

    return found[:_MAX_TEST_DIRS]


def _frameworks(root: Path, dependencies: set[str], pyproject: dict[str, Any]) -> list[str]:
    found = {
        framework
        for dependency, framework in _DEPENDENCY_FRAMEWORKS.items()
        if dependency in dependencies
    }
    tool = pyproject.get("tool")
    if isinstance(tool, dict) and isinstance(tool.get("pytest"), dict):
        found.add("pytest")
    for framework, names in _FRAMEWORK_FILES.items():
        if any((root / name).is_file() for name in names):
            found.add(framework)
    return [framework for framework in _FRAMEWORK_ORDER if framework in found]


def _collect_command(value: object, commands: list[str]) -> None:
    if isinstance(value, str):
        if value.strip():
            commands.append(value.strip())
        return
    if not isinstance(value, dict):
        return
    for key in ("command", "cmd", "test", "run", "start"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            commands.append(item.strip())


def _is_placeholder_test_script(value: str) -> bool:
    lowered = value.lower()
    return "no test specified" in lowered or not value.strip()


def _test_commands(
    config: dict[str, Any],
    pyproject: dict[str, Any],
    package: dict[str, Any],
    frameworks: list[str],
    test_dirs: list[str],
    root: Path,
) -> list[str]:
    commands: list[str] = []
    runners = config.get("runners")
    if isinstance(runners, dict):
        for value in runners.values():
            _collect_command(value, commands)
    l1 = config.get("l1")
    if isinstance(l1, dict):
        build = l1.get("build")
        if isinstance(build, str) and build.strip():
            commands.append(build.strip())
    if (root / "runtests.py").is_file():
        commands.append("python runtests.py")
    elif (root / "manage.py").is_file() and "django" in frameworks:
        commands.append("python manage.py test")
    tool = pyproject.get("tool")
    has_pytest_config = isinstance(tool, dict) and isinstance(tool.get("pytest"), dict)
    if has_pytest_config or ("pytest" in frameworks and test_dirs):
        commands.append("python -m pytest")
    scripts = package.get("scripts")
    if isinstance(scripts, dict):
        test_script = scripts.get("test")
        if isinstance(test_script, str) and not _is_placeholder_test_script(test_script):
            commands.append("npm test")
    return _unique(commands)


def _configured_smoke_command(config: dict[str, Any]) -> str:
    l1 = config.get("l1")
    if not isinstance(l1, dict):
        return ""
    value = l1.get("smoke_command")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _smallest_test_file(root: Path, test_dirs: list[str]) -> str:
    """Pick the smallest non-empty test module for a fast smoke run."""
    candidates: list[tuple[int, str]] = []
    for directory in test_dirs:
        base = root / directory.rstrip("/")
        if not base.is_dir():
            continue
        for pattern in ("test_*.py", "*_test.py"):
            for path in base.rglob(pattern):
                if not path.is_file() or path.name == "conftest.py":
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size <= 0:
                    continue
                candidates.append((size, path.relative_to(root).as_posix()))
    if not candidates:
        return ""
    candidates.sort()
    return candidates[0][1]


def _test_modules(root: Path, test_dirs: list[str], *, limit: int = 60) -> list[str]:
    """Dotted module names for test files, so callers do not invent labels."""
    modules: list[str] = []
    for directory in test_dirs:
        base = root / directory.rstrip("/")
        if not base.is_dir():
            continue
        paths = sorted(
            {
                path
                for pattern in ("test_*.py", "*_test.py")
                for path in base.rglob(pattern)
                if path.is_file() and "__pycache__" not in path.parts
            }
        )
        for path in paths:
            relative = path.relative_to(root).with_suffix("")
            module = ".".join(relative.parts)
            if not all(part.isidentifier() for part in relative.parts):
                continue
            if module not in modules:
                modules.append(module)
            if len(modules) >= limit:
                return modules
    return modules


def _playwright_tests(root: Path, *, limit: int = 80) -> dict[str, list[str]]:
    """Test titles from Playwright spec files for `--grep` targeting."""
    found: dict[str, list[str]] = {}
    bases = [root / name for name in ("tests", "test", "e2e")]
    with suppress(OSError):
        bases.extend(
            child / name
            for child in sorted(root.iterdir())
            if child.is_dir() and (child / "package.json").is_file()
            for name in ("tests", "test", "e2e")
        )
    for base in bases:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.spec.ts")):
            if not path.is_file() or "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            titles = re.findall(r"\btest\(\s*[\"'`]([^\"'`]+)[\"'`]", text)
            if titles:
                found[path.relative_to(root).as_posix()] = titles[:20]
            if len(found) >= limit:
                return found
    return found


def _component_commands(
    project_root: Path, component: Path, package: dict[str, Any]
) -> list[str]:
    """Concrete build and Playwright commands for a nested frontend package."""
    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return []
    relative = component.relative_to(project_root).as_posix()
    prefix = f"cd {relative} && " if relative != "." else ""
    pkg_runner = detect_package_runner(component, project_root)
    runner_map = {
        "bun": "bun run",
        "pnpm": "pnpm",
        "yarn": "yarn",
        "npm": "npm run",
    }
    runner = runner_map.get(pkg_runner, "npm run")
    commands: list[str] = []
    if isinstance(scripts.get("build"), str) and scripts["build"].strip():
        commands.append(f"{prefix}{runner} build")
    unit_tests = sorted((component / "unit").glob("*.test.ts"))[:5]
    for test_file in unit_tests:
        commands.append(f"{prefix}bun test unit/{test_file.name}")
    tests = component / "tests"
    if (component / "playwright.config.ts").is_file() and tests.is_dir():
        playwright = "bunx playwright" if runner == "bun run" else "npx playwright"
        specs = sorted(tests.glob("*.spec.ts"))[:4]
        titles_by_file = _playwright_tests(project_root)
        for spec in specs:
            relative_spec = (
                f"{relative}/tests/{spec.name}"
                if relative != "."
                else f"tests/{spec.name}"
            )
            for title in titles_by_file.get(relative_spec, [])[:3]:
                safe_title = title.replace('"', '\\"')
                commands.append(
                    f'{prefix}{playwright} test tests/{spec.name} --grep "{safe_title}"'
                )
            commands.append(f"{prefix}{playwright} test tests/{spec.name}")
    return commands


def _bun_tests(root: Path, *, limit: int = 80) -> dict[str, list[str]]:
    """Test titles from Bun unit test files."""
    found: dict[str, list[str]] = {}
    try:
        components = [root] + [
            child
            for child in sorted(root.iterdir())
            if child.is_dir() and (child / "package.json").is_file()
        ]
    except OSError:
        components = [root]
    for component in components:
        for path in sorted((component / "unit").glob("*.test.ts")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            titles = re.findall(r"\btest\(\s*[\"'`]([^\"'`]+)[\"'`]", text)
            if titles:
                found[path.relative_to(root).as_posix()] = titles[:20]
            if len(found) >= limit:
                return found
    return found


def _test_function_map(
    root: Path, test_dirs: list[str], *, limit: int = 80
) -> dict[str, list[str]]:
    """Real test function names per file, so plans can target one assertion."""
    found: dict[str, list[str]] = {}
    for directory in test_dirs:
        base = root / directory.rstrip("/")
        if not base.is_dir():
            continue
        paths = sorted(
            {
                path
                for pattern in ("test_*.py", "*_test.py")
                for path in base.rglob(pattern)
                if path.is_file() and "__pycache__" not in path.parts
            }
        )
        for path in paths:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            names: list[str] = []
            for node in tree.body:
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name.startswith("test_"):
                    names.append(node.name)
                elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    for child in node.body:
                        if isinstance(
                            child, (ast.FunctionDef, ast.AsyncFunctionDef)
                        ) and child.name.startswith("test_"):
                            names.append(f"{node.name}::{child.name}")
            if names:
                found[path.relative_to(root).as_posix()] = names[:20]
            if len(found) >= limit:
                return found
    return found


def _summarize_test_node(
    name: str, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> dict[str, Any]:
    """提取测试函数的文档、断言与实际输入构造，辅助准确测试映射。"""
    raw_doc = ast.get_docstring(node) or ""
    doc = raw_doc.splitlines()[0].strip()[:120] if raw_doc.splitlines() else ""
    asserts: list[str] = []
    for inner in ast.walk(node):
        if isinstance(inner, ast.Assert):
            with suppress(Exception):
                asserts.append(ast.unparse(inner.test)[:160])
        elif (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "raises"
        ):
            with suppress(Exception):
                asserts.append(ast.unparse(inner)[:160])
        if len(asserts) >= 2:
            break
    inputs: list[str] = []
    # 只摘取实际构造请求/参数的语句；让映射助手看到“测试到底发了什么字段”，
    # 而不是只看到断言，避免把未构造越权字段的测试配给边界验收项。
    for statement in node.body:
        if isinstance(statement, ast.Assert):
            continue
        with suppress(Exception):
            text = ast.unparse(statement)[:300]
            lowered = text.lower()
            if any(
                hint in lowered
                for hint in (
                    "request(",
                    ".post(",
                    ".get(",
                    ".put(",
                    ".patch(",
                    ".delete(",
                    "httpx.",
                    "requests.",
                )
            ):
                inputs.append(text)
        if len(inputs) >= 3:
            break
    return {"name": name, "doc": doc, "asserts": asserts, "inputs": inputs}


def _test_function_summaries(
    root: Path, test_dirs: list[str], *, limit: int = 80
) -> dict[str, list[dict[str, Any]]]:
    """提取测试函数的断言语义摘要，便于中文验收项精确匹配现有测试。"""
    found: dict[str, list[dict[str, Any]]] = {}
    for directory in test_dirs:
        base = root / directory.rstrip("/")
        if not base.is_dir():
            continue
        paths = sorted(
            {
                path
                for pattern in ("test_*.py", "*_test.py")
                for path in base.rglob(pattern)
                if path.is_file() and "__pycache__" not in path.parts
            }
        )
        for path in paths:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            entries: list[dict[str, Any]] = []
            for node in tree.body:
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name.startswith("test_"):
                    entries.append(_summarize_test_node(node.name, node))
                elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    for child in node.body:
                        if isinstance(
                            child, (ast.FunctionDef, ast.AsyncFunctionDef)
                        ) and child.name.startswith("test_"):
                            entries.append(
                                _summarize_test_node(f"{node.name}::{child.name}", child)
                            )
            if entries:
                found[path.relative_to(root).as_posix()] = entries[:20]
            if len(found) >= limit:
                return found
    return found


def _specific_test_commands(
    root: Path, test_dirs: list[str], *, limit: int = 8
) -> list[str]:
    """Concrete pytest file commands so business cases never need a whole suite."""
    commands: list[str] = []
    smallest = _smallest_test_file(root, test_dirs)
    if smallest:
        commands.append(f"python -m pytest {smallest}")
    for relative, names in _test_function_map(root, test_dirs).items():
        for name in names[:2]:
            command = f"python -m pytest {relative}::{name}"
            if command not in commands:
                commands.append(command)
            if len(commands) >= limit:
                return commands
    for module in _test_modules(root, test_dirs):
        relative = module.replace(".", "/") + ".py"
        if relative == smallest or not (root / relative).is_file():
            continue
        command = f"python -m pytest {relative}"
        if command not in commands:
            commands.append(command)
        if len(commands) >= limit:
            break
    return commands


def _test_example(
    root: Path, test_dirs: list[str], *, max_lines: int = 80
) -> dict[str, object] | None:
    """A short real-usage excerpt so generated scripts do not guess the API."""
    relative = _smallest_test_file(root, test_dirs)
    if not relative:
        return None
    try:
        lines = (root / relative).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    return {
        "path": relative,
        "content": "\n".join(lines[:max_lines]),
        "truncated": len(lines) > max_lines,
    }


def _symbol_signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        return "class"
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({ast.unparse(node.args)}){returns}"


def _symbol_map(
    root: Path, *, max_modules: int = 60, max_files: int = 120
) -> dict[str, dict[str, str]]:
    """Public symbols per module with their signatures."""
    skip_dirs = {
        "tests",
        "testing",
        ".venv",
        "venv",
        "build",
        "dist",
        "__pycache__",
        "node_modules",
        ".git",
    }
    symbols: dict[str, dict[str, str]] = {}
    py_files: list[Path] = []
    ignore_patterns = load_ignore_patterns(root)
    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in skip_dirs
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            if name.endswith(".py"):
                file_path = base_path / name
                if is_ignored_path(root, file_path, ignore_patterns):
                    continue
                py_files.append(file_path)
                if len(py_files) >= max_files:
                    break
        if len(py_files) >= max_files:
            break
    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        if not parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        module = ".".join(parts)
        if not module:
            continue
        entries: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if node.name.startswith("_"):
                    continue
                entries[node.name] = "class"
                has_init = False
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == "__init__":
                            has_init = True
                            entries[f"{node.name}.__init__"] = _symbol_signature(child)
                            continue
                        if child.name.startswith("_"):
                            continue
                        entries[f"{node.name}.{child.name}"] = _symbol_signature(child)
                if not has_init:
                    entries[f"{node.name}.__init__"] = "(inherited; signature unknown)"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                entries[node.name] = _symbol_signature(node)
        if entries:
            symbols[module] = entries
        if len(symbols) >= max_modules:
            break
    return symbols


def _readme_commands(text: str) -> list[str]:
    commands: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for prefix in ("$ ", "> ", "PS> "):
            if line.startswith(prefix):
                line = line[len(prefix) :].strip()
                break
        if _README_COMMAND.match(line):
            commands.append(line)
    return commands


def _package_commands(package: dict[str, Any]) -> list[str]:
    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return []
    commands: list[str] = []
    for name in _PACKAGE_SCRIPT_NAMES:
        value = scripts.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        if name == "test":
            if not _is_placeholder_test_script(value):
                commands.append("npm test")
        else:
            commands.append(f"npm run {name}")
    return commands


def _existing_commands(root: Path, package: dict[str, Any]) -> list[str]:
    readme_text = ""
    for name in ("README.md", "README.rst"):
        path = root / name
        if not path.is_file():
            continue
        try:
            readme_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        break
    commands = _readme_commands(readme_text) + _package_commands(package)
    return _unique(commands, limit=5)


def _script_examples(root: Path) -> dict[str, str]:
    """Excerpt project scripts so generated callers can satisfy their inputs."""
    examples: dict[str, str] = {}
    scripts = root / "scripts"
    if not scripts.is_dir():
        return examples
    for path in sorted(scripts.glob("*.py"))[:3]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        examples[f"scripts/{path.name}"] = text[:2000]
    return examples
