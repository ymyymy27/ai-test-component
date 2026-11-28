"""Data models for the lightweight plugin (workspace, project, task, plan, run)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal["running", "completed", "cancelled", "interrupted", "execution_error"]
Verdict = Literal["passed", "failed", "incomplete", "not_applicable"]
CaseResult = Literal["passed", "failed", "blocked", "error", "not_run", "manual_pending"]
MockKind = Literal["real", "mock", "mixed", "unknown"]
ConfidenceTier = Literal["confirmed", "suspected", "blocked"]
IssueStatus = Literal["pending", "in_progress", "retest", "closed"]
IssueDisposition = Literal["fixed", "non_defect", "duplicate", "deferred"]
Layer = Literal["L1", "L2", "L3"]
ScopePreset = Literal["all_applicable", "selected"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def case_item_ids(case: Case) -> list[str]:
    """Acceptance-item IDs covered by one case, primary first without duplicates."""
    ids: list[str] = []
    if case.acceptance_item_id:
        ids.append(case.acceptance_item_id)
    for item_id in case.acceptance_item_ids:
        if item_id not in ids:
            ids.append(item_id)
    return ids


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dependency(Model):
    to: str
    type: Literal["calls", "interface", "shared_data"] = "calls"
    note: str = ""


class Module(Model):
    module_id: str = Field(default_factory=lambda: new_id("mod"))
    name: str
    responsibility: str = ""
    owner: str = ""
    status: str = "开发中"
    deps: list[Dependency] = Field(default_factory=list)


class Project(Model):
    schema_version: int = 1
    project_id: str
    name: str
    root: str
    goal: str = ""
    modules: list[Module] = Field(default_factory=list)
    revision: int = 1
    updated_at: str = ""
    archived: bool = False


class AcceptanceItem(Model):
    item_id: str = Field(default_factory=lambda: new_id("ai"))
    text: str
    observable: bool = True
    status: Literal["active", "pending"] = "active"
    source: str = ""
    module_id: str | None = None


class Task(Model):
    schema_version: int = 1
    task_id: str
    project_id: str
    title: str
    goal: str = ""
    intent: Literal["smoke", "business"] = "business"
    origin: Literal["manual", "goal", "range"] = "manual"
    source_map_revision: int = 0
    scope: str = ""
    inputs: str = ""
    outputs: str = ""
    preconditions: list[str] = Field(default_factory=list)
    executor: str = ""
    reviewer: str = ""
    acceptance_items: list[AcceptanceItem] = Field(default_factory=list)
    revision: int = 1


class Delivery(Model):
    schema_version: int = 1
    delivery_id: str
    task_id: str
    done: list[str] = Field(default_factory=list)
    not_done: list[str] = Field(default_factory=list)
    changed_modules: list[str] = Field(default_factory=list)
    interface_changes: list[str] = Field(default_factory=list)
    run_commands: list[str] = Field(default_factory=list)
    test_data: str = ""
    dependencies: list[str] = Field(default_factory=list)
    mocks: list[str] = Field(default_factory=list)
    self_test_evidence: list[str] = Field(default_factory=list)
    commit: str = ""
    complete: bool = True
    revision: int = 1


class Case(Model):
    case_id: str = Field(default_factory=lambda: new_id("case"))
    acceptance_item_id: str | None = None
    acceptance_item_ids: list[str] = Field(default_factory=list)
    layer: Layer
    title: str = ""
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected: str
    expectation_source: str = ""
    expectation_verified: bool = False
    warnings: list[str] = Field(default_factory=list)
    verify_method: str = ""
    dependencies: list[str] = Field(default_factory=list)
    mock_allowed: bool = True
    importance: Literal["high", "medium", "low"] = "medium"
    depends_on: Literal["build", "service"] | None = None
    mutates_state: bool = False
    script_name: str | None = None
    script_content: str | None = None
    coverage_kind: Literal["direct", "file", "shared", "manual", "unverified"] | None = None
    test_nodes: list[str] = Field(default_factory=list)
    frozen_expectation: dict[str, Any] | None = None
    generation: dict[str, Any] | None = None
    assertion_strength: Literal["strong", "weak", "unverified"] | None = None


class Scope(Model):
    preset: ScopePreset = "all_applicable"
    module_ids: list[str] = Field(default_factory=list)
    acceptance_item_ids: list[str] = Field(default_factory=list)
    frozen_at: str | None = None


class Plan(Model):
    schema_version: int = 1
    plan_id: str
    task_id: str
    plan_no: int = 0
    task_revision: int = 0
    task_contract: str = ""
    created_at: str = ""
    scope: Scope
    applicable_count: int = 0
    cases: list[Case] = Field(default_factory=list)
    item_texts: dict[str, str] = Field(default_factory=dict)
    capabilities: dict[str, Any] | None = None
    recipes: list[dict[str, Any]] = Field(default_factory=list)
    coverage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    ai_reviews: list[dict[str, Any]] = Field(default_factory=list)
    unselected_by_module: list[dict[str, Any]] = Field(default_factory=list)
    excluded_items: list[dict[str, Any]] | None = None
    warnings: list[str] = Field(default_factory=list)
    status: Literal["draft", "published"] = "draft"
    source_signature: str = ""
    revision: int = 1


class Attempt(Model):
    attempt: int = 1
    result: CaseResult = "not_run"
    stage: Literal["setup", "verify", ""] = ""
    started_at: str = ""
    finished_at: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    message: str = ""


class RunCase(Model):
    case_id: str
    result: CaseResult = "not_run"
    attempts: list[Attempt] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence_tier: ConfidenceTier | None = None
    confidence_reason: str = ""
    judgement: dict[str, Any] | None = None
    service_log_issues: list[str] = Field(default_factory=list)
    resumed_from_run_id: str = ""
    script_repair: dict[str, Any] | None = None


class Analysis(Model):
    status: Literal["generated", "not_generated", "invalid"] = "not_generated"
    reason: Literal[
        "no_failures",
        "execution_error",
        "model_missing",
        "model_invalid",
        "model_error",
        "",
    ] = ""
    model: str = ""
    generated_at: str = ""
    observed: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    candidate_causes: list[dict[str, str]] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class Run(Model):
    schema_version: int = 1
    run_id: str
    plan_id: str
    project_id: str
    kind: Literal["task", "project_check"] = "task"
    scope: Scope
    confirmed_by: str = ""
    confirmed_at: str = ""
    confirmation_id: str = ""
    confirmation_fingerprint: str = ""
    confirmation_reused: bool = False
    status: RunStatus = "running"
    postprocess: Literal["pending", "running", "done", "error"] = "pending"
    current_case_id: str = ""
    pid: int | None = None
    pid_created_at: float | None = None
    started_at: str = ""
    heartbeat_at: str = ""
    cases: list[RunCase] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_notes: dict[str, str] = Field(default_factory=dict)
    analysis: list[Analysis] = Field(default_factory=list)
    mock_judgement: list[dict[str, Any]] = Field(default_factory=list)
    service_started: bool = False
    service_name: str = ""
    l1: dict[str, Any] | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    environment_history: list[dict[str, Any]] = Field(default_factory=list)
    dependency_suggestions: list[dict[str, str]] = Field(default_factory=list)
    dependency_repairs: list[dict[str, Any]] = Field(default_factory=list)
    media_bytes: int = 0
    data_prefix: str = ""
    dependencies: list[str] = Field(default_factory=list)
    dependency_install_ok: bool | None = None
    request_ids: list[str] = Field(default_factory=list)
    latest_report_id: str = ""
    excluded_items: list[dict[str, Any]] | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    preflight: dict[str, Any] | None = None
    probe_results: list[dict[str, Any]] = Field(default_factory=list)
    resumed_from_run_id: str = ""
    ai_hypotheses: list[dict[str, Any]] = Field(default_factory=list)


class Confirmation(Model):
    schema_version: int = 1
    confirmation_id: str
    project_id: str
    plan_id: str
    plan_revision: int = 0
    acceptance_item_ids: list[str] = Field(default_factory=list)
    environment: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    side_effects: list[str] = Field(default_factory=list)
    fingerprint: str
    confirmed_by: str
    confirmed_at: str
    run_id: str = ""


class ManualStep(Model):
    step: str
    result: Literal["passed", "failed", "blocked"]
    note: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    recorded_at: str = ""


class ManualLog(Model):
    schema_version: int = 1
    run_id: str
    case_id: str
    steps: list[ManualStep] = Field(default_factory=list)


class IssueOccurrence(Model):
    run_id: str
    case_id: str
    at: str
    evidence_refs: list[str] = Field(default_factory=list)


class Issue(Model):
    schema_version: int = 1
    issue_id: str
    project_id: str
    signature: str
    title: str
    item_no: str = ""
    item_text: str = ""
    steps: list[str] = Field(default_factory=list)
    expected: str = ""
    actual: str = ""
    file: str | None = None
    line: int | None = None
    severity: Literal["P0", "P1", "P2", "P3"] = "P2"
    status: IssueStatus = "pending"
    disposition: IssueDisposition | None = None
    reason: str = ""
    unstable: bool = False
    last_result: str = ""
    verification_run_id: str = ""
    occurrences: list[IssueOccurrence] = Field(default_factory=list)
    confidence_tier: ConfidenceTier = "suspected"
    confidence_reason: str = ""
    fix_version: str | None = None
    revision: int = 1


class Opinion(Model):
    by: str
    at: str
    text: str


class Report(Model):
    schema_version: int = 1
    report_id: str
    run_id: str
    generated_at: str = ""
    outcome: Verdict
    conclusion: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    coverage: dict[str, int] = Field(default_factory=dict)
    changed_modules: list[str] = Field(default_factory=list)
    unmapped_files: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    mock_judgement: list[dict[str, Any]] = Field(default_factory=list)
    real_chain: bool = False
    summary: str | None = None
    summary_source: Literal["ai", "fallback", "none"] = "none"
    opinion: Opinion | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    issue_count: int = 0
    failure_occurrences: int = 0
    analysis: list[dict[str, Any]] = Field(default_factory=list)
    layers: dict[str, int] = Field(default_factory=dict)
    source_commit: str | None = None
    environment: dict[str, Any] = Field(default_factory=dict)
    github: dict[str, Any] | None = None
    uncovered_items: list[str] = Field(default_factory=list)
    unverified_items: list[str] = Field(default_factory=list)
    plan_warnings: list[str] = Field(default_factory=list)
    item_details: list[dict[str, Any]] = Field(default_factory=list)
    case_details: list[dict[str, Any]] = Field(default_factory=list)
    evidence_index: list[dict[str, Any]] = Field(default_factory=list)
    l1: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None
    probe_results: list[dict[str, Any]] = Field(default_factory=list)
    confidence_summary: dict[str, int] = Field(default_factory=dict)
    token_usage: dict[str, int] = Field(default_factory=dict)
    capabilities: dict[str, Any] | None = None
    coverage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    source_summary: dict[str, int] = Field(default_factory=dict)
    assertion_strength: dict[str, int] = Field(default_factory=dict)
    revision: int = 1


class WorkspaceMeta(Model):
    schema_version: int = 1
    workspace_id: str
    created_at: str
