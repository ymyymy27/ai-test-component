"""Failure analysis shared by ``analysis.run`` and the run worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from aitest.errors import AiTestError
from aitest.model_config import build_client, model_settings
from aitest.models import Analysis, Run
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now


def _causes(value: list[Any]) -> list[dict[str, str]]:
    causes: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            causes.append({"cause": item.strip(), "confidence": "unknown"})
        elif isinstance(item, dict):
            cause = item.get("cause")
            confidence = item.get("confidence")
            if isinstance(cause, str) and cause.strip():
                causes.append(
                    {
                        "cause": cause.strip(),
                        "confidence": confidence.strip()
                        if isinstance(confidence, str) and confidence.strip()
                        else "unknown",
                    }
                )
    return causes


def analyze_run(ws: Workspace, project_id: str, run_dir: Path) -> dict[str, Any]:
    """Generate or reset the failure analysis for a run, never raising on model gaps."""
    run = read_model(run_dir / "run.json", Run)
    client = build_client(ws, project_id)
    failed = [case for case in run.cases if case.result in {"failed", "blocked", "error"}]
    if client is None or not failed:
        run.analysis = [Analysis(status="not_generated")]
        write_model(run_dir / "run.json", run)
        return {"status": "not_generated", "analysis": None}
    evidence = [ref for case in failed for ref in case.evidence_refs]
    try:
        data = client.analyze_failure(run.model_dump(mode="json"), evidence)
    except AiTestError as exc:
        status: Literal["invalid", "not_generated"] = (
            "invalid" if exc.info.code == "MODEL_OUTPUT_INVALID" else "not_generated"
        )
        run.analysis = [Analysis(status=status)]
        write_model(run_dir / "run.json", run)
        return {"status": status, "analysis": None}
    analysis = Analysis(
        status="generated",
        model=str(model_settings(ws, project_id)["name"]),
        generated_at=iso_now(),
        observed=str(data["observed"]),
        evidence_refs=[str(item) for item in data["evidence_refs"]],
        candidate_causes=_causes(list(data["candidate_causes"])),
        unverified=[str(item) for item in data["unverified"]],
        next_steps=[str(item) for item in data["next_steps"]],
    )
    run.analysis = [analysis]
    write_model(run_dir / "run.json", run)
    return {"status": "generated", "analysis": analysis.model_dump(mode="json")}
