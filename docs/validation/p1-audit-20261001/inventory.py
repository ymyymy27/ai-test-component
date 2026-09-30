"""Record audit scope and diagnostic test results without changing product files."""

from __future__ import annotations

import ast
import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent


def file_info(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    source = data.decode("utf-8-sig")
    result: dict[str, object] = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256(data).hexdigest(),
        "lines": len(source.splitlines()),
    }
    if path.suffix == ".py":
        tree = ast.parse(source)
        result["definitions"] = [
            {"name": item.name, "line": item.lineno}
            for item in ast.walk(tree)
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        ]
        result["imports"] = sorted(
            {
                item.module or ""
                for item in ast.walk(tree)
                if isinstance(item, ast.ImportFrom)
            }
            | {
                name.name
                for item in ast.walk(tree)
                if isinstance(item, ast.Import)
                for name in item.names
            }
        )
        result["not_implemented_lines"] = [
            item.lineno
            for item in ast.walk(tree)
            if isinstance(item, ast.Raise)
            and isinstance(item.exc, ast.Call)
            and isinstance(item.exc.func, ast.Name)
            and item.exc.func.id == "NotImplementedError"
        ]
        result["docstring_only_definitions"] = [
            {"name": item.name, "line": item.lineno}
            for item in ast.walk(tree)
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and len(item.body) == 1
            and isinstance(item.body[0], ast.Expr)
            and isinstance(item.body[0].value, ast.Constant)
            and isinstance(item.body[0].value.value, str)
        ]
    return result


def junit_summary(path: Path) -> dict[str, object]:
    tree = ET.parse(path)
    suites = list(tree.iter("testsuite"))
    return {
        "tests": sum(int(suite.get("tests", "0")) for suite in suites),
        "failures": sum(int(suite.get("failures", "0")) for suite in suites),
        "errors": sum(int(suite.get("errors", "0")) for suite in suites),
        "skipped": sum(int(suite.get("skipped", "0")) for suite in suites),
        "problems": [
            {
                "class": case.get("classname"),
                "name": case.get("name"),
                "kind": child.tag,
                "message": child.get("message"),
            }
            for case in tree.iter("testcase")
            for child in case
            if child.tag in {"failure", "error"}
        ],
    }


def main() -> None:
    source_paths = sorted((ROOT / "src/aitest").rglob("*.py")) + [
        ROOT / "src/aitest/resources/panel/src/index.ts",
        ROOT / "integrations/trae/src/extension.ts",
    ]
    test_paths = sorted((ROOT / "tests").rglob("*.py"))
    docs_root = ROOT / "docs/项目文档"
    normative_paths = [
        docs_root / "README.md",
        docs_root / "总体架构.md",
        docs_root / "阅读索引.md",
        *sorted((docs_root / "一期").rglob("*.md")),
    ]
    inventory = {
        "audit_date": "2026-10-01",
        "source_baseline": subprocess.check_output(
            ["git", "rev-parse", "0c890ed"], cwd=ROOT, text=True
        ).strip(),
        "method": "AST inventory plus manual contract/call-path review; not code coverage",
        "source_python_count": len(source_paths) - 2,
        "source_typescript_count": 2,
        "sources": [file_info(path) for path in source_paths],
        "tests": [file_info(path) for path in test_paths],
        "normative_documents": [file_info(path) for path in normative_paths],
    }
    summary = {
        "audit_date": "2026-10-01",
        "platform": platform.platform(),
        "python": sys.version,
        "full_pytest": junit_summary(ROOT / ".audit-p1-pytest.xml"),
        "diagnostic_pytest": {
            "excluded": ["tests/unit/test_a_pipe_peer_rejection.py"],
            "is_full_suite_pass": False,
            **junit_summary(ROOT / ".audit-p1-partial.xml"),
        },
        "ruff": "passed",
        "mypy": "passed; baseline 123, final document review 125 analyzed files; followed imports",
        "generated_schemas": "passed; regeneration produced no diff",
        "version_check": "passed; 0.4.0",
        "panel_build": "passed",
        "panel_test": "initial browser unavailable; installed Chromium; 1 passed (2.5 s)",
        "trae_package": "passed; six-file VSIX; not real Trae acceptance",
        "real_acceptance": "verified=0; not_verified=7; blocked=1; untested=27",
        "baseline_ci": {
            "run_id": 36729829040,
            "conclusion": "failure",
            "reason": "pytest collection: missing check_peer_identity",
            "url": "https://github.com/ymyymy27/ai-test-component/actions/runs/36729829040",
        },
    }
    for name, content in (("source-inventory.json", inventory), ("test-summary.json", summary)):
        (OUTPUT / name).write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"python": len(source_paths) - 2, "typescript": 2, "tests": len(test_paths)}))


if __name__ == "__main__":
    main()
