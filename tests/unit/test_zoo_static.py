"""第 3 步单测：Fixture Zoo 11 个离线静态夹具全量识别验证。"""

from __future__ import annotations

from pathlib import Path

import pytest

from aitest.core.project_context import build_capabilities_snapshot, build_project_context

ZOO_STATIC_ROOT = Path("tests/fixtures/zoo/static")

EXPECTED_FIXTURES: list[tuple[str, str, str, int, int]] = [
    # (目录名, 期望支持级别, 期望能力标记, 最小后端路由数, 最小前端调用数)
    ("flask_decorator", "T1", "flask", 4, 0),
    ("fastapi_router", "T1", "fastapi", 5, 0),
    ("django_drf_router", "T1", "drf", 2, 0),
    ("express_router", "T1", "express", 4, 0),
    ("axios_template_base", "T3", "axios-template", 0, 2),
    ("fetch_wrapper", "T3", "fetch-wrapper", 0, 1),
    ("openapi_generic", "T2", "openapi", 4, 0),
    ("nextjs_app_router", "T1", "nextjs", 2, 0),
    ("nestjs_controller", "T1", "nestjs", 3, 0),
    ("spring_boot_controller", "T1", "spring_boot", 3, 0),
    ("go_gin_router", "T1", "go_gin", 3, 0),
]


def test_zoo_static_contains_all_eleven_fixtures() -> None:
    """确保 zoo/static 目录下 11 个离线静态夹具完整落地。"""
    dirs = sorted(p.name for p in ZOO_STATIC_ROOT.iterdir() if p.is_dir())
    expected = sorted(item[0] for item in EXPECTED_FIXTURES)
    assert dirs == expected
    assert len(dirs) == 11


@pytest.mark.parametrize(
    ("name", "expected_level", "expected_marker", "min_routes", "min_fe_calls"),
    EXPECTED_FIXTURES,
)
def test_zoo_static_fixture_detection(
    name: str,
    expected_level: str,
    expected_marker: str,
    min_routes: int,
    min_fe_calls: int,
) -> None:
    root = ZOO_STATIC_ROOT / name
    assert root.is_dir()
    ctx = build_project_context(root, {})
    cap = build_capabilities_snapshot(root, {}, project_context=ctx)

    assert cap["source"] == "static"
    assert cap["degraded"] is False
    assert cap["level"] == expected_level
    assert cap["fingerprint"].startswith("sha256:")

    all_markers = set([*cap["backend"], *cap["router"], *cap["frontend_calls"], *cap["unknowns"]])
    assert expected_marker in all_markers
    assert len(ctx["api_routes"]) >= min_routes
    assert len(ctx["frontend_api_calls"]) >= min_fe_calls

    if name == "fetch_wrapper":
        assert "frontend_wrapper" in cap["unknowns"]
    if name == "django_drf_router":
        assert "drf_custom_action" in cap["unsupported"]
