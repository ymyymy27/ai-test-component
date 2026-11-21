"""Infer project test commands, frameworks and capabilities from declared facts.

Only manifests, configuration files and files that actually exist are reported.
Nothing here executes project commands, and README examples are surfaced as
command candidates only; they are never treated as tested endpoints.
"""

from __future__ import annotations

import ast
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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

_FRONTEND_DEPENDENCIES = {
    "react",
    "react-dom",
    "vue",
    "@angular/core",
    "svelte",
    "next",
    "nuxt",
    "vite",
    "solid-js",
    "preact",
    "ember-source",
}

_FRONTEND_FILES = (
    "index.html",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
    "angular.json",
    "svelte.config.js",
    "nuxt.config.ts",
)

_FRONTEND_SCRIPT_HINTS = (
    "vite",
    "next ",
    "react-scripts",
    "nuxt",
    "ng serve",
    "vue-cli-service",
    "svelte",
)

_DATABASE_DEPENDENCIES = {
    "sqlalchemy",
    "alembic",
    "psycopg2",
    "psycopg2-binary",
    "psycopg",
    "asyncpg",
    "aiosqlite",
    "pymysql",
    "mysqlclient",
    "pymongo",
    "motor",
    "redis",
    "peewee",
    "tortoise-orm",
    "prisma",
    "@prisma/client",
    "sequelize",
    "typeorm",
    "knex",
    "mongoose",
    "pg",
    "mysql2",
    "sqlite3",
    "better-sqlite3",
}

_DATABASE_FILES = ("alembic.ini", "schema.prisma")

_SINGLE_SERVICE_KEYS = frozenset({"start", "health", "stop"})

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


_TEST_DEPENDENCY_GROUPS = frozenset({"test", "tests", "testing", "dev", "development"})


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


_TEST_DIR_NAMES = frozenset({"tests", "test", "testing"})
_SKIP_WALK_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "build",
        "dist",
        "site-packages",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".yarn",
        ".cache",
    }
)
_MAX_TEST_DIRS = 30
_MAX_TEST_DIR_DEPTH = 5


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
    """Root plus immediate subprojects that declare their own dependencies."""

    components: list[tuple[Path, dict[str, Any], dict[str, Any]]] = [
        (root, _read_toml(root / "pyproject.toml"), _read_json(root / "package.json"))
    ]
    try:
        children = sorted(root.iterdir())
    except OSError:
        return components
    for child in children:
        if (
            not child.is_dir()
            or child.name.startswith(".")
            or child.name.lower() in _SKIP_WALK_DIRS
        ):
            continue
        pyproject_path = child / "pyproject.toml"
        package_path = child / "package.json"
        requirements = any(child.glob("requirements*.txt"))
        if not (pyproject_path.is_file() or package_path.is_file() or requirements):
            continue
        components.append(
            (
                child,
                _read_toml(pyproject_path),
                _read_json(package_path),
            )
        )
    return components



def _test_dirs(root: Path) -> list[str]:
    """Find test directories in nested layouts without walking dependency trees.

    Large projects often keep tests beside the package (``pkg/tests``) instead of
    a root ``tests/`` directory. Walking with a depth cap and pruning vendored
    directories keeps this bounded on monorepos.
    """

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

    for current, dirnames, _files in os.walk(root):
        base = Path(current)
        depth = len(base.relative_to(root).parts)
        if depth >= _MAX_TEST_DIR_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name.lower() not in _SKIP_WALK_DIRS and not name.startswith(".")
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
            # A discovered test directory is enough signal; do not descend into it.
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
        for path in sorted(base.rglob("test_*.py")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).with_suffix("")
            module = ".".join(relative.parts)
            if not all(part.isidentifier() for part in relative.parts):
                continue
            if module not in modules:
                modules.append(module)
            if len(modules) >= limit:
                return modules
    return modules


def _component_commands(
    project_root: Path, component: Path, package: dict[str, Any]
) -> list[str]:
    """Concrete build and Playwright commands for a nested frontend package."""

    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return []
    relative = component.relative_to(project_root).as_posix()
    prefix = f"cd {relative} && " if relative != "." else ""
    if any(
        (base / name).is_file()
        for base in (component, project_root)
        for name in ("bun.lock", "bun.lockb")
    ):
        runner = "bun run"
    elif (component / "pnpm-lock.yaml").is_file() or (
        project_root / "pnpm-lock.yaml"
    ).is_file():
        runner = "pnpm"
    elif (component / "yarn.lock").is_file() or (
        project_root / "yarn.lock"
    ).is_file():
        runner = "yarn"
    else:
        runner = "npm run"
    commands: list[str] = []
    if isinstance(scripts.get("build"), str) and scripts["build"].strip():
        commands.append(f"{prefix}{runner} build")
    tests = component / "tests"
    if (component / "playwright.config.ts").is_file() and tests.is_dir():
        playwright = "bunx playwright" if runner == "bun run" else "npx playwright"
        specs = sorted(tests.glob("*.spec.ts"))[:4]
        for spec in specs:
            commands.append(f"{prefix}{playwright} test tests/{spec.name}")
    return commands


def _specific_test_commands(
    root: Path, test_dirs: list[str], *, limit: int = 8
) -> list[str]:
    """Concrete pytest file commands so business cases never need a whole suite."""

    commands: list[str] = []
    smallest = _smallest_test_file(root, test_dirs)
    if smallest:
        commands.append(f"python -m pytest {smallest}")
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
    root: Path, *, max_modules: int = 20, max_files: int = 40
) -> dict[str, dict[str, str]]:
    """Public symbols per module with their signatures.

    The model uses this to import the right module and call the real signature
    instead of guessing an argument type or count.
    """
    skip_dirs = {"tests", "testing", ".venv", "venv", "build", "dist", "__pycache__"}
    symbols: dict[str, dict[str, str]] = {}
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        if scanned >= max_files:
            break
        if any(part in skip_dirs for part in path.parts):
            continue
        scanned += 1
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


def _has_frontend(root: Path, dependencies: set[str], package: dict[str, Any]) -> bool:
    if dependencies & _FRONTEND_DEPENDENCIES:
        return True
    scripts = package.get("scripts")
    if isinstance(scripts, dict):
        values = " ".join(
            str(value).lower() for value in scripts.values() if isinstance(value, str)
        )
        if any(hint in values for hint in _FRONTEND_SCRIPT_HINTS):
            return True
    if any((root / name).is_file() for name in _FRONTEND_FILES):
        return True
    return (root / "src" / "components").is_dir() or (root / "public").is_dir()


def _has_database(root: Path, dependencies: set[str], frameworks: list[str]) -> bool:
    if dependencies & _DATABASE_DEPENDENCIES:
        return True
    if "django" in frameworks:
        return True
    return any((root / name).is_file() for name in _DATABASE_FILES)


def _is_single_service(value: dict[str, Any]) -> bool:
    return bool(_SINGLE_SERVICE_KEYS & set(value))


def _port(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value < 65536 else None
    if isinstance(value, str) and value.isdigit():
        number = int(value)
        return number if 0 < number < 65536 else None
    return None


def _health_url(entry: dict[str, Any]) -> str:
    health = entry.get("health")
    if isinstance(health, str) and health.strip():
        text = health.strip()
        if "://" in text:
            return text
        if text.startswith("/"):
            port = _port(entry.get("port"))
            if port is not None:
                host = str(entry.get("host") or "127.0.0.1")
                return f"http://{host}:{port}{text}"
        elif ":" in text or "/" in text:
            return f"http://{text}"
        return text
    port = _port(entry.get("port"))
    if port is None:
        return ""
    host = str(entry.get("host") or "127.0.0.1")
    return f"http://{host}:{port}/health"


def _services_with_health(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if _is_single_service(value):
        entry = dict(value)
        entry["health_url"] = _health_url(entry)
        return entry
    result: dict[str, Any] = {}
    for name, item in value.items():
        if isinstance(item, dict):
            entry = dict(item)
            entry["health_url"] = _health_url(entry)
            result[str(name)] = entry
        else:
            result[str(name)] = item
    return result


def _health_hosts(services: dict[str, Any]) -> set[str]:
    """Loopback plus every host declared by a service health URL."""
    hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    entries = services.values() if not _is_single_service(services) else [services]
    for item in entries:
        if not isinstance(item, dict):
            continue
        for key in ("health_url", "health"):
            raw = item.get(key)
            if not isinstance(raw, str) or "://" not in raw:
                continue
            host = urlsplit(raw).hostname
            if host:
                hosts.add(host.lower())
    return hosts


def build_project_context(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Return inferred facts used by plan generation."""
    pyproject = _read_toml(root / "pyproject.toml")
    package = _read_json(root / "package.json")
    components = _component_manifests(root)
    dependencies: set[str] = set()
    frameworks_found: list[str] = []
    declared: list[str] = []
    for component, component_pyproject, component_package in components:
        component_dependencies = _pyproject_dependencies(
            component_pyproject
        ) | _package_dependencies(component_package)
        for path in sorted(component.glob("requirements*.txt")):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            component_dependencies |= _requirement_names(lines)
        dependencies |= component_dependencies
        for framework in _frameworks(
            component, component_dependencies, component_pyproject
        ):
            if framework not in frameworks_found:
                frameworks_found.append(framework)
        declared.extend(test_dependency_specs(component))
    test_dirs = _test_dirs(root)
    frameworks = [
        framework for framework in _FRAMEWORK_ORDER if framework in frameworks_found
    ]
    test_commands = _test_commands(
        config, pyproject, package, frameworks, test_dirs, root
    )
    test_commands = _unique([*test_commands, *_specific_test_commands(root, test_dirs)])
    for component, _component_pyproject, component_package in components:
        test_commands = _unique(
            [*test_commands, *_component_commands(root, component, component_package)]
        )
    services = _services_with_health(config.get("services"))
    return {
        "test_commands": test_commands,
        "smoke_command": _configured_smoke_command(config),
        "smallest_test": _smallest_test_file(root, test_dirs) if test_dirs else "",
        "test_modules": _test_modules(root, test_dirs),
        "test_dependencies": _unique(declared, 40),
        "test_example": _test_example(root, test_dirs),
        "symbols": _symbol_map(root),
        "services": services,
        "health_hosts": sorted(_health_hosts(services)),
        "test_dirs": test_dirs,
        "framework": frameworks,
        "has_frontend": any(
            _has_frontend(component, _pyproject_dependencies(component_pyproject)
            | _package_dependencies(component_package), component_package)
            for component, component_pyproject, component_package in components
        ),
        "has_database": _has_database(root, dependencies, frameworks),
        "existing_commands": _existing_commands(root, package),
    }
