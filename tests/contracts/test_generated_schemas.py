import json
from pathlib import Path

from aitest.contracts.commands import Command
from aitest.contracts.events import Event
from aitest.contracts.templates import TemplatePack
from aitest.contracts.views import CoverageDTO, Response


def test_generated_schemas_match_contract_source() -> None:
    root = Path(__file__).resolve().parents[2] / "src/aitest/contracts/schemas"
    for model in (Command, Event, TemplatePack, CoverageDTO, Response):
        stored = json.loads((root / f"{model.__name__}.json").read_text(encoding="utf-8"))
        assert stored == model.model_json_schema()
