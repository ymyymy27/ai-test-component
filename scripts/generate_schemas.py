"""Generate schemas from Python contracts; do not hand-edit generated files."""

import json
from pathlib import Path

from aitest.contracts.commands import Command
from aitest.contracts.events import Event
from aitest.contracts.prepared_run import PreparedRun
from aitest.contracts.templates import TemplatePack
from aitest.contracts.views import CoverageDTO, Response


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "src/aitest/contracts/schemas"
    target.mkdir(parents=True, exist_ok=True)
    for model in (Command, Event, TemplatePack, CoverageDTO, Response, PreparedRun):
        (target / f"{model.__name__}.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
