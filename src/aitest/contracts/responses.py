"""Protocol responses containing facts and storage references only."""
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from .errors import ErrorDTO
from .versions import PROTOCOL_VERSION, ProtocolVersion

class PageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    limit: int = Field(ge=1, le=500)
    next_cursor: str | None = None
    index_state: str = "maintained"

class Response(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    intent_id: str | None = None
    result: dict[str, Any] | None = None
    page: PageInfo | None = None
    error: ErrorDTO | None = None

