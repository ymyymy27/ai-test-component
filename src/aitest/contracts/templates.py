"""Strict shipped template format. Samples are draft inputs, never execution evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemplateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1)
    layer: Literal["L1", "L2", "L3"]
    objective: str = Field(min_length=1)
    preconditions: list[str]
    input_fields: list[str]
    entry_type: str
    expected: str = Field(min_length=1)
    assertion_basis: str = Field(min_length=1)
    verification: str = Field(min_length=1)
    evidence_requirements: list[str] = Field(min_length=1)


class CriticalPath(BaseModel):
    path_id: str
    item_ids: list[str] = Field(min_length=1)
    real_dependencies: list[str]
    required_verification: str


class DraftExample(BaseModel):
    kind: Literal["normal", "boundary", "error"]
    input: str
    expected: str
    status: Literal["draft"] = "draft"


class TemplatePack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["aitest.template/1.0"]
    template_id: str
    version: str
    name: str
    source: str
    applicability: str
    delivery_method: Literal["automatic", "registered_command", "manual", "import"]
    implementation_status: Literal["scaffold"]
    items: list[TemplateItem] = Field(min_length=1)
    required_item_ids: list[str] = Field(min_length=1)
    critical_paths: list[CriticalPath]
    no_critical_path_reason: str | None = None
    examples: list[DraftExample]

    @model_validator(mode="after")
    def references(self) -> "TemplatePack":
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate item IDs")
        if len(self.required_item_ids) != len(set(self.required_item_ids)):
            raise ValueError("duplicate required IDs")
        if not set(self.required_item_ids) <= set(ids):
            raise ValueError("dangling required item")
        path_ids = [path.path_id for path in self.critical_paths]
        if len(path_ids) != len(set(path_ids)):
            raise ValueError("duplicate path IDs")
        for path in self.critical_paths:
            if not set(path.item_ids) <= set(ids):
                raise ValueError("dangling critical path")
        if not self.critical_paths and not self.no_critical_path_reason:
            raise ValueError("empty critical paths require an applicability reason")
        if {example.kind for example in self.examples} != {"normal", "boundary", "error"}:
            raise ValueError("normal/boundary/error examples required")
        return self
