import hashlib
import os
import tempfile
from contextlib import suppress
from pathlib import Path


class FileObjectStore:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "objects" / "sha256"

    def put_bytes(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        target = self.root / digest[:2] / digest[2:]
        if target.exists():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".object.", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary, target)
            except FileExistsError:
                os.unlink(temporary)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise
        return digest

    def read_bytes(self, digest: str) -> bytes:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid sha256 digest")
        return (self.root / digest[:2] / digest[2:]).read_bytes()
