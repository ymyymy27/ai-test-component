"""Protocol responses containing facts and storage references only."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .errors import ErrorDTO
from .versions import PROTOCOL_VERSION, ProtocolVersion


class PageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    limit: int = Field(ge=1, le=500)
    next_cursor: str | None = None
    index_state: str = "maintained"


class Response(BaseModel):
    """Unique protocol response shared by LocalAPI, ports and generated schemas."""

    model_config = ConfigDict(extra="forbid")
    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    instance_id: str = Field(min_length=1, max_length=128)
    workspace_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = None
    intent_id: str | None = None
    binding_revision: int | None = None
    result: dict[str, JsonValue] | None = None
    page: PageInfo | None = None
    error: ErrorDTO | None = None
