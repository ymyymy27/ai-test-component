"""Infer project test commands, frameworks and capabilities from declared facts.

Only manifests, configuration files and files that actually exist are reported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aitest.core.context.contracts import (
    _api_routes,
    _frontend_api_calls,
    _frontend_assets,
)
from aitest.core.context.manifests import (
    _FRAMEWORK_ORDER,
    _bun_tests,
    _component_commands,
    _component_manifests,
    _configured_smoke_command,
    _existing_commands,
    _frameworks,
    _package_dependencies,
    _playwright_tests,
    _pyproject_dependencies,
    _read_json,
    _read_toml,
    _requirement_names,
    _script_examples,
    _smallest_test_file,
    _specific_test_commands,
    _symbol_map,
    _test_commands,
    _test_dirs,
    _test_example,
    _test_function_map,
    _test_function_summaries,
    _test_modules,
    _unique,
    package_name,
    python_dependencies,
    test_dependency_specs,
)
from aitest.core.context.services import (
    _has_database,
    _has_frontend,
    _health_hosts,
    _services_with_health,
    detect_services,
)


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
    if not services:
        services = _services_with_health(detect_services(root))
    return {
        "test_commands": test_commands,
        "smoke_command": _configured_smoke_command(config),
        "smallest_test": _smallest_test_file(root, test_dirs) if test_dirs else "",
        "test_modules": _test_modules(root, test_dirs),
        "test_functions": _test_function_map(root, test_dirs),
        "test_summaries": _test_function_summaries(root, test_dirs),
        "playwright_tests": _playwright_tests(root),
        "bun_tests": _bun_tests(root),
        "test_dependencies": _unique(declared, 40),
        "test_example": _test_example(root, test_dirs),
        "symbols": _symbol_map(root),
        "services": services,
        "health_hosts": sorted(_health_hosts(services)),
        "test_dirs": test_dirs,
        "framework": frameworks,
        "has_frontend": any(
            _has_frontend(
                component,
                _pyproject_dependencies(component_pyproject)
                | _package_dependencies(component_package),
                component_package,
            )
            for component, component_pyproject, component_package in components
        ),
        "has_database": _has_database(root, dependencies, frameworks),
        "existing_commands": _existing_commands(root, package),
        "script_examples": _script_examples(root),
        "api_routes": _api_routes(root),
        "frontend_api_calls": _frontend_api_calls(root),
        "frontend_assets": _frontend_assets(root),
    }


__all__ = [
    "_api_routes",
    "_bun_tests",
    "_component_commands",
    "_component_manifests",
    "_configured_smoke_command",
    "_existing_commands",
    "_frontend_api_calls",
    "_frontend_assets",
    "_has_database",
    "_has_frontend",
    "_health_hosts",
    "_playwright_tests",
    "_read_json",
    "_read_toml",
    "_script_examples",
    "_services_with_health",
    "_smallest_test_file",
    "_specific_test_commands",
    "_symbol_map",
    "_test_commands",
    "_test_dirs",
    "_test_example",
    "_test_function_map",
    "_test_function_summaries",
    "_test_modules",
    "build_project_context",
    "detect_services",
    "package_name",
    "python_dependencies",
    "test_dependency_specs",
]
