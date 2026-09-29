"""Finite query contracts; callers cannot request an implicit full scan."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .identity import RequestId
from .versions import PROTOCOL_VERSION, ProtocolVersion

QueryName = Literal["record.get", "records.list", "events.list", "workspace.status", "integrity.check"]
SortKey = Literal["aggregate_kind", "record_id", "revision", "commit_sequence"]

class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    project_id: str = Field(min_length=1, max_length=128)
    aggregate_kind: str | None = Field(default=None, min_length=1, max_length=128)
    record_id: str | None = Field(default=None, min_length=1, max_length=128)
    revision: int | None = Field(default=None, ge=1)
    sort: SortKey = "aggregate_kind"
    descending: bool = False
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = Field(default=None, min_length=1, max_length=256)

class Query(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    request_id: RequestId
    name: QueryName
    workspace_id: str = Field(min_length=1, max_length=128)
    spec: QuerySpec | None = None

