"""Workspace identity and admission."""
from pathlib import Path
import json, uuid
from contextlib import contextmanager
from .locking import writer_lock
from . import atomic
class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity_path = self.root / "workspace.json"
        if self.identity_path.exists(): self.identity=json.loads(self.identity_path.read_text(encoding="utf-8"))
        else:
            self.identity = {"workspace_id": str(uuid.uuid4()), "schema_version": "1.0", "writer_epoch": 0}
            atomic.write_json(self.identity_path, self.identity)
    @property
    def workspace_id(self): return self.identity["workspace_id"]
    def validate(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ValueError("workspace identity mismatch")
    def path(self, *parts: str) -> Path:
        candidate = (self.root.joinpath(*parts)).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("path escapes workspace root")
        return candidate
    @contextmanager
    def acquire(self):
        with writer_lock(self.root / "writer.lock"):
            self.identity["writer_epoch"] += 1
            atomic.write_json(self.identity_path, self.identity)
            yield self
