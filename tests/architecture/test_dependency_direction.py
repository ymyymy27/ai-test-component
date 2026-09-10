import ast
from pathlib import Path


def test_domain_has_no_outward_dependencies() -> None:
    root = Path(__file__).parents[2] / "src" / "ai_test" / "domain"
    forbidden = ("ai_test.application", "ai_test.infrastructure", "ai_test.interfaces")
    violations: list[str] = []
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(forbidden):
                    violations.append(f"{source.name}: {name}")
    assert violations == []

