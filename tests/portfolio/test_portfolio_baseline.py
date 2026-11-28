"""外部 5 项目 Portfolio 期望基线校验与显式回归入口（@pytest.mark.portfolio）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aitest.core.project_context import build_capabilities_snapshot

PORTFOLIO_ROOT = Path("tests/portfolio")
EXPECTED_PROJECTS = (
    "rbac-demo",
    "flask-rbac",
    "flask-vue-crud",
    "django-react-crud",
    "store-pos",
)


def test_portfolio_expected_baselines_schema_valid() -> None:
    """默认 pytest 校验 5 项目期望基线骨架完整存在且不包含外部源码。"""
    for name in EXPECTED_PROJECTS:
        exp_path = PORTFOLIO_ROOT / name / "expected.json"
        assert exp_path.is_file(), f"缺少 Portfolio 基线文件：{exp_path}"
        data = json.loads(exp_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["project"] == name
        assert data["max_false_positives"] == 0
        assert data["max_false_passes"] == 0
        assert isinstance(data["known_defects_total"], int)
        assert isinstance(data["min_defects_detected"], int)
        # 严禁把外部项目源码提交进 tests/portfolio/<project>/
        entries = [p.name for p in (PORTFOLIO_ROOT / name).iterdir()]
        assert entries == ["expected.json"]


@pytest.mark.portfolio
@pytest.mark.parametrize("project_name", EXPECTED_PROJECTS)
def test_portfolio_external_project_smoke(project_name: str) -> None:
    """仅当显式运行 -m portfolio 且配置 AITEST_PORTFOLIO_PROJECTS 根目录时校验外部项目快照。"""
    raw_root = os.environ.get("AITEST_PORTFOLIO_PROJECTS", "").strip()
    if not raw_root:
        pytest.skip("未配置 AITEST_PORTFOLIO_PROJECTS，跳过外部 Portfolio 实测")
    project_dir = Path(raw_root) / project_name
    if not project_dir.is_dir():
        pytest.skip(f"外部项目目录不存在：{project_dir}")
    expected = json.loads(
        (PORTFOLIO_ROOT / project_name / "expected.json").read_text(encoding="utf-8")
    )
    cap = build_capabilities_snapshot(project_dir, {})
    assert cap["source"] == "static"
    assert cap["level"] == expected["expected_level"]
