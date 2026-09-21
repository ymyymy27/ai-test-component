"""Committed event envelope; event storage/replay remains unavailable."""

from typing import Literal

from pydantic import BaseModel, Field


class Event(BaseModel):
    schema_version: Literal["aitest.event/2.0"] = "aitest.event/2.0"
    instance_id: str
    workspace_id: str
    writer_epoch: int = Field(ge=1)
    commit_sequence: int = Field(ge=1)
    event_sequence: int = Field(ge=1)
    project_id: str
    record_id: str
    revision: int = Field(ge=1)
    event_type: str
