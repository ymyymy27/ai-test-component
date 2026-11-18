"""Failure analysis shared by ``analysis.run`` and the run worker.

The model receives bounded excerpts of the collected evidence, not just file
names. Every stored analysis is redacted and appended to the run, so a re-run
or a different model never overwrites an earlier explanation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from aitest.config import known_secret_values
from aitest.core.evidence import evidence_path
from aitest.errors import AiTestError
from aitest.model_config import build_client, load_project_config, model_settings
from aitest.models import Analysis, Run
from aitest.security import redact
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

_MAX_EVIDENCE_FILES = 10
_MAX_EVIDENCE_CHARS = 4000


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


def _evidence_excerpts(
    run_dir: Path, refs: list[str], secrets: list[str]
) -> list[str]:
    excerpts: list[str] = []
    for ref in refs[:_MAX_EVIDENCE_FILES]:
        path = evidence_path(run_dir, ref)
        if path is None or not path.is_file():
            excerpts.append(f"--- {ref} ---\n（证据文件不存在）")
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            excerpts.append(f"--- {ref} ---\n（证据文件无法读取）")
            continue
        excerpts.append(f"--- {ref} ---\n{redact(text[: _MAX_EVIDENCE_CHARS], secrets)}")
    return excerpts


def _store(ws: Workspace, project_id: str, run_dir: Path, analysis: Analysis) -> None:
    secrets = known_secret_values([load_project_config(ws, project_id)])
    analysis.observed = redact(analysis.observed, secrets)
    analysis.unverified = [redact(item, secrets) for item in analysis.unverified]
    analysis.next_steps = [redact(item, secrets) for item in analysis.next_steps]
    for cause in analysis.candidate_causes:
        cause["cause"] = redact(cause.get("cause", ""), secrets)
    # Re-read under the workspace lock: the worker, evidence.add and
    # run.manual may have written the same run while the model call ran.
    with ws.lock(timeout=120.0):
        current = read_model(run_dir / "run.json", Run)
        allowed = set(current.evidence_refs)
        analysis.evidence_refs = [
            redact(ref, secrets) for ref in analysis.evidence_refs if ref in allowed
        ]
        current.analysis.append(analysis)
        write_model(run_dir / "run.json", current)


def analyze_run(ws: Workspace, project_id: str, run_dir: Path) -> dict[str, Any]:
    """Generate a failure analysis for a run, never raising on model gaps."""
    run = read_model(run_dir / "run.json", Run)
    failed = [case for case in run.cases if case.result in {"failed", "blocked", "error"}]
    if not failed:
        analysis = Analysis(status="not_generated", reason="no_failures")
        _store(ws, project_id, run_dir, analysis)
        return {"status": "not_generated", "reason": "no_failures", "analysis": None}
    client = build_client(ws, project_id)
    if client is None:
        analysis = Analysis(status="not_generated", reason="model_missing")
        _store(ws, project_id, run_dir, analysis)
        return {"status": "not_generated", "reason": "model_missing", "analysis": None}
    refs = [ref for case in failed for ref in case.evidence_refs]
    secrets = known_secret_values([load_project_config(ws, project_id)])
    evidence = _evidence_excerpts(run_dir, refs, secrets)
    try:
        data = client.analyze_failure(run.model_dump(mode="json"), evidence)
    except AiTestError as exc:
        if exc.info.code == "MODEL_OUTPUT_INVALID":
            status: Literal["invalid", "not_generated"] = "invalid"
            reason: Literal["model_invalid", "model_error"] = "model_invalid"
        else:
            status = "not_generated"
            reason = "model_error"
        analysis = Analysis(status=status, reason=reason)
        _store(ws, project_id, run_dir, analysis)
        return {"status": status, "reason": reason, "analysis": None}
    except Exception:
        analysis = Analysis(status="not_generated", reason="model_error")
        _store(ws, project_id, run_dir, analysis)
        return {"status": "not_generated", "reason": "model_error", "analysis": None}
    analysis = Analysis(
        status="generated",
        reason="",
        model=str(model_settings(ws, project_id)["name"]),
        generated_at=iso_now(),
        observed=str(data["observed"]),
        evidence_refs=[str(item) for item in data["evidence_refs"]],
        candidate_causes=_causes(list(data["candidate_causes"])),
        unverified=[str(item) for item in data["unverified"]],
        next_steps=[str(item) for item in data["next_steps"]],
    )
    _store(ws, project_id, run_dir, analysis)
    return {
        "status": "generated",
        "reason": "",
        "analysis": analysis.model_dump(mode="json"),
    }
