"""Template content regression tests for the six shipped packs.

These assert the requirements recorded in
`docs/文档-feix-a/B包/06-六个内置模板升级设计说明.md`; structural validity is
already covered by `test_templates.py`.
"""

import json
from importlib.resources import files
from typing import Any, cast

import pytest

TEMPLATE_IDS = [
    "agent-workflow",
    "http-workflow",
    "manual-web-workflow",
    "python-library",
    "python-service",
    "ticket-workflow",
]

# 升级前的条目 ID 集合。模板升级不得重命名或删除既有 item_id，
# 否则以准确修订引用模板的既有计划会失效（设计说明第 3.7 节）。
BASELINE_ITEM_IDS = {
    "agent-workflow": {"input", "calls", "repeat"},
    "http-workflow": {"chain", "assertions", "errors", "persistence"},
    "manual-web-workflow": {"navigation", "form", "refresh"},
    "python-library": {"syntax", "normal", "boundary", "error"},
    "python-service": {"startup", "health", "dependency-failure", "state"},
    "ticket-workflow": {"create", "page", "detail", "update", "refresh", "verify"},
}


def pack(template_id: str) -> dict[str, Any]:
    path = files("aitest.resources").joinpath(f"templates/{template_id}/1.0.0.json")
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def all_text(template_id: str) -> str:
    return json.dumps(pack(template_id), ensure_ascii=False)


def item(pack_data: dict[str, Any], item_id: str) -> dict[str, Any]:
    return next(i for i in pack_data["items"] if i["item_id"] == item_id)


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_item_ids_are_not_renamed(template_id: str) -> None:
    data = pack(template_id)
    actual = {i["item_id"] for i in data["items"]}
    assert actual == BASELINE_ITEM_IDS[template_id]


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_applicability_states_conditions(template_id: str) -> None:
    applicability = pack(template_id)["applicability"]
    assert len(applicability) >= 40, "适用条件过短，未说明适用与不适用范围"
    assert "适用条件" in applicability


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_assertion_basis_is_never_a_concrete_assertion(template_id: str) -> None:
    """模板不得代填断言依据：必须显式要求用户补齐并确认。

    依据 P1-FR04"模板只能生成草稿"与架构文档第 3 节：断言依据由用户对准确修订确认。
    本断言防止后来者误以为该字段"该被补全"。
    """
    data = pack(template_id)
    for entry in data["items"]:
        assert "用户补齐并确认" in entry["assertion_basis"], (
            f"{template_id}/{entry['item_id']} 的 assertion_basis 必须声明由用户补齐并确认"
        )


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_every_critical_path_declares_real_dependencies_and_verification(
    template_id: str,
) -> None:
    data = pack(template_id)
    paths = data["critical_paths"]
    if not paths:
        assert data["no_critical_path_reason"], (
            f"{template_id} 没有关键链路时必须给出不适用理由"
        )
        return
    for path in paths:
        assert path["real_dependencies"], f"{template_id}/{path['path_id']} 缺真实依赖"
        assert path["required_verification"], (
            f"{template_id}/{path['path_id']} 缺不可省核验"
        )


def test_python_library_database_not_applicable_but_independent_comparison_stays() -> None:
    """无持久化时数据库核验不适用，但独立预期比对仍为必测。"""
    data = pack("python-library")
    assert data["critical_paths"] == []
    reason = data["no_critical_path_reason"]
    assert "独立预期比对仍为必测" in reason
    for item_id in ("normal", "boundary"):
        assert "独立预期" in item(item_id=item_id, pack_data=data)["objective"]


def test_python_service_startup_is_a_real_process_fact() -> None:
    """仅实际服务项适用；最小启动不得以"进程存在"代替"服务就绪"。"""
    data = pack("python-service")
    assert "库项目不强套" in data["applicability"]
    startup = item(data, "startup")
    assert "不等于服务已就绪" in startup["expected"]
    assert any("进程句柄" in r for r in startup["evidence_requirements"])


def test_http_workflow_requires_persistence_evidence_not_status_code() -> None:
    """成功 HTTP 状态码不能代替持久化证据。"""
    data = pack("http-workflow")
    assert "persistence" in data["required_item_ids"]
    persistence = item(data, "persistence")
    assert "成功 HTTP 状态码不代替持久化证据" in persistence["expected"]
    assert "不以 Mock 顶替" in data["applicability"]
    assert any(
        "独立只读查询" in d for d in data["critical_paths"][0]["real_dependencies"]
    )


def test_manual_web_distinguishes_unsupported_from_not_applicable() -> None:
    """无页面不适用；尚不支持不等于不适用。"""
    data = pack("manual-web-workflow")
    assert "无页面时不适用" in data["applicability"]
    assert "尚不支持" in data["applicability"]
    assert "不得混用" in data["applicability"]
    assert "刷新" in item(data, "refresh")["objective"]


def test_agent_workflow_requires_tool_events_and_three_runs() -> None:
    """无采集列缺口；随机输出默认三次分别留存；不采信自述。"""
    data = pack("agent-workflow")
    assert "不采信 AI 自述" in data["applicability"]
    calls = item(data, "calls")
    assert "不得以文字自述补造" in calls["expected"]
    repeat = item(data, "repeat")
    assert "三次" in repeat["objective"]
    assert "分别留证" in repeat["expected"]
    assert "缺采集" in data["critical_paths"][0]["required_verification"]


def test_ticket_workflow_covers_the_full_chain_with_one_identifier() -> None:
    """创建取 ID → 分页同 ID → 详情 → 改状态 → 刷新 → 独立核验。"""
    data = pack("ticket-workflow")
    assert data["required_item_ids"] == [
        "create",
        "page",
        "detail",
        "update",
        "refresh",
        "verify",
    ]
    assert "唯一业务标识" in item(data, "create")["expected"]
    assert "业务标识" in item(data, "page")["verification"]
    assert "同一工单" in item(data, "verify")["objective"]
    assert "成功状态码不代替持久化证据" in (
        data["critical_paths"][0]["required_verification"]
    )


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_released_status_does_not_authorize_execution(template_id: str) -> None:
    """released 只表示模板内容定稿，不表示草稿已确认或可以执行。"""
    from aitest.contracts.templates import TemplateImplementationStatus

    assert pack(template_id)["implementation_status"] == "released"
    assert TemplateImplementationStatus.RELEASED.value == "released"
    assert {s.value for s in TemplateImplementationStatus} == {
        "draft",
        "released",
        "deprecated",
    }
