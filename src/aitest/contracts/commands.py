"""Local protocol boundary; known actions are not necessarily implemented capabilities."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

PROTOCOL_VERSION: Literal["aitest.local/2.0"] = "aitest.local/2.0"
HUMAN_ACTIONS = frozenset(
    {
        "authorize_step",
        "publish_plan",
        "publish_rules",
        "record_local_review",
        "save_model_outbound_policy",
        "close_issue",
        "update_issue",
    }
)
PHASE_ONE_ACTIONS = frozenset(
    {
        "bind_project",
        "save_context",
        "save_delivery",
        "save_acceptance",
        "analyze_project",
        "save_model_outbound_policy",
        "save_project_view_preference",
        "save_rules",
        "publish_rules",
        "generate_draft",
        "save_plan",
        "publish_plan",
        "prepare_run",
        "start_run",
        "revise_pending_steps",
        "narrow_driver",
        "authorize_step",
        "pause_run",
        "resume_run",
        "cancel_run",
        "retry_step",
        "attach_evidence",
        "verify_pending",
        "return_stage",
        "update_issue",
        "record_fix",
        "request_regression",
        "close_issue",
        "create_report",
        "record_local_review",
        "export_report",
        "copy_repair_brief",
        "query",
        "events",
        "doctor",
        "test_connection",
        "storage_usage",
        "backup",
        "migrate",
    }
)
READ_ACTIONS = frozenset({"query", "events", "doctor", "storage_usage", "copy_repair_brief"})


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal["aitest.local/2.0"] = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1)
    project_id: str | None = None
    binding_revision: int | None = Field(default=None, ge=1)
    target: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    intent_id: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def write_identity(self) -> "Command":
        if self.action in PHASE_ONE_ACTIONS - READ_ACTIONS and (
            not self.project_id or not self.intent_id or self.expected_revision is None
        ):
            raise ValueError("write actions require project, intent and expected revision")
        return self
