"""dispatch/actions 只能依赖 application/domain/contracts，不能反向依赖入口或基础设施。"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ROOT / "src/aitest/application/dispatch"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_action_handlers_do_not_depend_on_entry_or_infrastructure() -> None:
    offenders: list[tuple[str, str]] = []
    for path in ACTIONS.rglob("*.py"):
        for module in _imports(path):
            if module.startswith(("aitest.bootstrap", "aitest.interfaces", "aitest.infrastructure")):
                offenders.append((str(path.relative_to(ROOT)), module))
    assert offenders == []


def test_registry_re_exports_the_public_names() -> None:
    from aitest.application import usecase_registry

    for name in ("BUseCaseDependencies", "BUseCaseError", "BUseCaseRegistry", "build_b_use_case_registry"):
        assert hasattr(usecase_registry, name), name
