import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src/aitest"


def test_dependency_direction() -> None:
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imports = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for name in imports:
                if relative.parts[0] == "domain":
                    assert not name.startswith(
                        (
                            "aitest.application",
                            "aitest.infrastructure",
                            "aitest.interfaces",
                            "aitest.contracts",
                            "pydantic",
                        )
                    )
                if relative.parts[0] == "application":
                    assert not name.startswith(("aitest.infrastructure", "aitest.interfaces"))
                if relative.parts[0] == "infrastructure" and name.startswith("aitest.application"):
                    assert name in {"aitest.application.ports", "aitest.application.errors"}
                if relative.parts[0] == "interfaces":
                    assert not name.startswith("aitest.infrastructure")


def test_forbidden_legacy_and_later_phase_modules_absent() -> None:
    assert not list((ROOT.parent / "ai_test").rglob("*.py"))
    for name in [
        "interfaces/asgi_host.py",
        "application/collaboration",
        "domain/identity",
        "domain/delivery",
        "application/diagrams.py",
        "application/locations.py",
    ]:
        assert not (ROOT / name).exists()
