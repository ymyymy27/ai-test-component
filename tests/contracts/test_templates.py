import json
from importlib.resources import files

import pytest
from pydantic import ValidationError

from aitest.contracts.templates import TemplatePack


def template_data() -> dict:
    path = files("aitest.resources").joinpath("templates/python-library/1.0.0.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_six_shipped_packs_have_versions_and_draft_examples() -> None:
    root = files("aitest.resources").joinpath("templates")
    packs = [
        TemplatePack.model_validate_json(d.joinpath("1.0.0.json").read_text(encoding="utf-8"))
        for d in root.iterdir()
        if d.is_dir()
    ]
    assert len(packs) == 6
    assert len({p.template_id for p in packs}) == 6
    assert all(p.implementation_status == "scaffold" for p in packs)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "dangling", "empty", "unknown"])
def test_invalid_packs_cannot_be_used(failure: str) -> None:
    data = template_data()
    if failure == "missing":
        del data["required_item_ids"]
    elif failure == "duplicate":
        data["items"].append(data["items"][0])
    elif failure == "dangling":
        data["required_item_ids"].append("unknown")
    elif failure == "empty":
        data["no_critical_path_reason"] = None
    else:
        data["execute_shell"] = "echo unsafe"
    with pytest.raises(ValidationError):
        TemplatePack.model_validate(data)
