"""Pure template reference. Boundary validation lives in contracts/templates.py."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemplateRef:
    template_id: str
    version: str
