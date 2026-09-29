"""Capability advertisement for the local core, independent of business rules."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .versions import CAPABILITY_SCHEMA_VERSION, PROTOCOL_VERSION, ProtocolVersion

CapabilityName = Literal["transactions", "immutable_records", "objects", "events", "bounded_queries", "backup", "migration", "integrity", "recovery", "redaction", "connectivity_policy"]

class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: str = CAPABILITY_SCHEMA_VERSION
    name: CapabilityName
    version: str = Field(min_length=1, max_length=32)
    enabled: bool
    reason: str | None = None

class CapabilitySet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    workspace_id: str = Field(min_length=1, max_length=128)
    capabilities: tuple[Capability, ...]

