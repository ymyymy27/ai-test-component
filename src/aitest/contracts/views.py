"""DTOs serialize core facts; views must not calculate business outcomes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ErrorDTO(BaseModel):
    code: str
    message: str
    retryable: bool = False
    next_step: str


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: Literal["aitest.local/2.0"] = "aitest.local/2.0"
    request_id: str
    instance_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    binding_revision: int | None = None
    result: dict[str, JsonValue] | None = None
    error: ErrorDTO | None = None


class CoverageDTO(BaseModel):
    selected_applicable_count: int = Field(ge=0)
    required_applicable_count: int = Field(ge=0)
    executed_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    verified_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    unverified_count: int = Field(ge=0)
    required_verified_count: int = Field(ge=0)
