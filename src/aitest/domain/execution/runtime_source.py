"""Frozen capture scope and observations from the owned Python execution process."""

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class RuntimeSourceFile:
    relative_path: str
    content_digest: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (not self.relative_path or path.is_absolute() or ".." in path.parts
                or "\\" in self.relative_path or ":" in self.relative_path
                or path.as_posix() != self.relative_path):
            raise ValueError("runtime source path must be a canonical relative file")
        digest = self.content_digest
        if (not digest.startswith("sha256:") or len(digest) != 71
                or any(c not in "0123456789abcdef" for c in digest[7:])):
            raise ValueError("runtime source requires a SHA256 content digest")


@dataclass(frozen=True, slots=True)
class RuntimeSourcePlan:
    files: tuple[RuntimeSourceFile, ...]
    policy: str = "python-script/1"
    carrier_digest: str = ""

    def __post_init__(self) -> None:
        if len({item.relative_path for item in self.files}) != len(self.files):
            raise ValueError("runtime source scope has duplicate file identities")


@dataclass(frozen=True, slots=True)
class RuntimeSourceObservation:
    attempt_id: str
    process_start_identity: str
    process_id: str
    interpreter: str
    entry: str
    files: tuple[RuntimeSourceFile, ...] = ()
    gaps: tuple[str, ...] = ()
    complete: bool = False
    policy: str = "python-script/1"
    executing_process_id: str = ""
    executing_process_start_identity: str = ""
