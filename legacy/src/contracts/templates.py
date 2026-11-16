"""Strict shipped template format. Samples are draft inputs, never execution evidence."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemplateImplementationStatus(StrEnum):
    """模板自身的定稿状态。

    与"草稿只生成草稿"这条边界不冲突：`RELEASED` 只表示模板内容已定稿、
    可用于生成项目草稿；**不表示**项目草稿已确认，也**不表示**可以执行。
    模板不设删除路径，`DEPRECATED` 只表示新计划不再选用。

    三态不能用 `bool` 折叠：与隔离方式同理，"模板没写完"与"模板已废弃"是两件事。
    """

    DRAFT = "draft"
    RELEASED = "released"
    DEPRECATED = "deprecated"


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
    implementation_status: TemplateImplementationStatus
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
