"""问题修复台账必须覆盖两份审查文档的全部条目。

台账由 `scripts/audit_ledger.py --init` 生成；这里只校验，不生成。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "audit_ledger.py"


def _load():
    spec = importlib.util.spec_from_file_location("audit_ledger", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_ledger"] = module
    spec.loader.exec_module(module)
    return module


def test_ledger_matches_both_review_documents() -> None:
    module = _load()
    issues = module.extract_issues()
    assert len(issues) == 653
    assert sum(1 for issue in issues if issue.source == "05") == 382
    assert sum(1 for issue in issues if issue.source == "06") == 271
    assert module.check() == []


def test_ledger_rejects_open_items_at_release_gate() -> None:
    module = _load()
    rows = module.parse_ledger()
    assert len(rows) == 653
    assert all(row["status"] == "未开始" for row in rows)
    errors = module.check(require_closed=True)
    assert len(errors) == 653
