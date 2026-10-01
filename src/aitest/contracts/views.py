"""DTOs serialize core facts; views must not calculate business outcomes."""

from pydantic import BaseModel, Field

from .responses import Response as Response

__all__ = ["CoverageDTO", "Response"]


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
