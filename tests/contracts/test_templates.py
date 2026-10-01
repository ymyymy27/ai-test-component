import json
from importlib.resources import files
from typing import Any, cast

import pytest
from pydantic import ValidationError

from aitest.contracts.templates import TemplatePack

TEMPLATE_IDS = [
    "agent-workflow",
    "http-workflow",
    "manual-web-workflow",
    "python-library",
    "python-service",
    "ticket-workflow",
]


def load_pack(template_id: str) -> dict[str, Any]:
    path = files("aitest.resources").joinpath(f"templates/{template_id}/1.0.0.json")
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def shipped_packs() -> list[TemplatePack]:
    root = files("aitest.resources").joinpath("templates")
    return [
        TemplatePack.model_validate_json(d.joinpath("1.0.0.json").read_text(encoding="utf-8"))
        for d in root.iterdir()
        if d.is_dir()
    ]


def test_six_shipped_packs_have_versions_and_draft_examples() -> None:
    packs = shipped_packs()
    assert len(packs) == 6
    assert len({p.template_id for p in packs}) == 6
    assert sorted(p.template_id for p in packs) == TEMPLATE_IDS
    # 六个模板在 Sprint 3 升级后内容定稿，因此锁定为 released。
    # 断言意图由"模板尚未定稿，不得当完成依据"改为"模板已定稿、可用于生成草稿"；
    # released 不表示项目草稿已确认，也不表示可以执行（见模板升级设计说明第 2.1 节）。
    assert all(p.implementation_status == "released" for p in packs)


def test_template_status_rejects_unknown_value() -> None:
    data = load_pack("python-library")
    data["implementation_status"] = "scaffold"
    with pytest.raises(ValidationError):
        TemplatePack.model_validate(data)


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
@pytest.mark.parametrize("failure", ["missing", "duplicate", "dangling", "empty", "unknown"])
def test_invalid_packs_cannot_be_used(template_id: str, failure: str) -> None:
    """每个模板都要拒绝这五类非法输入（漏项拒绝案例）。"""
    data = load_pack(template_id)
    if failure == "missing":
        del data["required_item_ids"]
    elif failure == "duplicate":
        data["items"].append(data["items"][0])
    elif failure == "dangling":
        data["required_item_ids"].append("unknown")
    elif failure == "empty":
        # 该模板本有关键链路时，清空后必须给出不适用理由
        data["critical_paths"] = []
        data["no_critical_path_reason"] = None
    else:
        data["execute_shell"] = "echo unsafe"
    with pytest.raises(ValidationError):
        TemplatePack.model_validate(data)
