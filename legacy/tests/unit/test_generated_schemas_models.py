"""Generated contract schemas must never drift from their Python models."""

import json
from pathlib import Path

from aitest.contracts.commands import Command
from aitest.contracts.events import Event
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.http_workflow import HttpWorkflowSpec
from aitest.contracts.plan_proposal import PlanProposal
from aitest.contracts.prepared_run import PreparedRun
from aitest.contracts.read_views import ReadViewQuery
from aitest.contracts.templates import TemplatePack
from aitest.contracts.views import CoverageDTO, Response

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "src/aitest/contracts/schemas"


def test_generated_schemas_match_contract_models() -> None:
    for model in (
        Command,
        Event,
        ExecutionFacts,
        HttpWorkflowSpec,
        TemplatePack,
        PlanProposal,
        CoverageDTO,
        Response,
        PreparedRun,
        ReadViewQuery,
    ):
        expected = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        saved = (SCHEMA_ROOT / f"{model.__name__}.json").read_text(encoding="utf-8")
        assert saved == expected, f"{model.__name__}.json is stale; run scripts/generate_schemas.py"
