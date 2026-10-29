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
    path = files("aitest.resources").joinpath(f"templates/{template_id}/manifest.json")
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def shipped_packs() -> list[TemplatePack]:
    root = files("aitest.resources").joinpath("templates")
    return [
        TemplatePack.model_validate_json(d.joinpath("manifest.json").read_text(encoding="utf-8"))
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
@pytest.mark.parametrize("failure", ["missing", "duplicate", "dangling", "empty"])
def test_invalid_packs_cannot_be_used(template_id: str, failure: str) -> None:
    """每个模板都要拒绝这四类非法输入（漏项拒绝案例）。"""
    data = load_pack(template_id)
    if failure == "missing":
        del data["required_item_ids"]
    elif failure == "duplicate":
        data["items"].append(data["items"][0])
    elif failure == "dangling":
        data["required_item_ids"].append("unknown")
    else:
        # 该模板本有关键链路时，清空后必须给出不适用理由
        data["critical_paths"] = []
        data["no_critical_path_reason"] = None
    with pytest.raises(ValidationError):
        TemplatePack.model_validate(data)


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_unknown_fields_are_preserved_instead_of_rejected(template_id: str) -> None:
    """架构 01 §3：导入模板的未知字段保留在扩展字段内，不静默丢弃。"""
    data = load_pack(template_id)
    data["execute_shell"] = "echo unsafe"
    pack = TemplatePack.model_validate(data)
    assert pack.extension_fields["execute_shell"] == "echo unsafe"
    assert pack.model_dump()["execute_shell"] == "echo unsafe"
