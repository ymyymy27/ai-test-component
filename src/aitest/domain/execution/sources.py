"""Content identity must come from actual bytes, never timestamps alone."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceFile:
    relative_path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    snapshot_id: str
    project_id: str
    purpose: str
    binding_revision: int
    files: tuple[SourceFile, ...]
    content_identity: str
