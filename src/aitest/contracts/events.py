"""Committed immutable event envelope."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .identity import IntentId, RequestId
from .versions import EVENT_SCHEMA_VERSION


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["aitest.event/2.0"] = EVENT_SCHEMA_VERSION
    event_id: str = Field(min_length=1, max_length=128)
    request_id: RequestId | None = None
    intent_id: IntentId | None = None
    instance_id: str
    workspace_id: str
    writer_epoch: int = Field(ge=1)
    commit_sequence: int = Field(ge=1)
    event_sequence: int = Field(ge=1)
    project_id: str
    record_id: str
    revision: int = Field(ge=1)
    event_type: str
