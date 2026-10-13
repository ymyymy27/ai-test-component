"""Finite read-only view selectors, shared by local and MCP entry points."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

ViewIdentity = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[^\x00-\x1f]*[^\x00-\x1f\s][^\x00-\x1f]*$",
        json_schema_extra={"not": {"pattern": r"[\x00-\x1f]"}},
    ),
]


class ReadViewQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "view": {"enum": ["run", "panel"]},
                        "run_id": {"type": "string"},
                    },
                    "required": ["view", "run_id"],
                    "not": {
                        "anyOf": [
                            {"required": [name]}
                            for name in (
                                "delivery_id",
                                "revision",
                                "submission_id",
                            )
                        ]
                    },
                },
                {
                    "properties": {
                        "view": {"const": "delivery"},
                        "delivery_id": {"type": "string"},
                    },
                    "required": ["view", "delivery_id"],
                    "not": {"required": ["run_id"]},
                },
            ],
        },
    )

    view: Literal["run", "panel", "delivery"]
    run_id: ViewIdentity | None = None
    delivery_id: ViewIdentity | None = None
    revision: Annotated[StrictInt, Field(gt=0)] | None = None
    submission_id: ViewIdentity | None = None

    @model_validator(mode="after")
    def finite_combination(self) -> Self:
        if self.view in {"run", "panel"}:
            if self.model_fields_set != {"view", "run_id"} or self.run_id is None:
                raise ValueError("run/panel views require only view and run_id")
        elif (
            self.model_fields_set - {"view", "delivery_id", "revision", "submission_id"}
            or self.delivery_id is None
        ):
            raise ValueError("delivery views require their finite delivery fields")
        for identity in (self.run_id, self.delivery_id, self.submission_id):
            if identity is not None and not identity.strip():
                raise ValueError("read_view requires a bounded nonblank identity")
        return self
