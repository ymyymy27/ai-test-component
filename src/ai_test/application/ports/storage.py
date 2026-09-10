from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, Protocol


class RecordRepository(Protocol):
    def get(self, kind: str, record_id: str) -> dict[str, Any] | None: ...

    def put(
        self,
        kind: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> int: ...

    def list(self, kind: str) -> Iterator[dict[str, Any]]: ...


class EvidenceObjectStore(Protocol):
    def put_bytes(self, content: bytes) -> str: ...

    def read_bytes(self, digest: str) -> bytes: ...


class WorkspaceUnitOfWork(Protocol):
    def writer(self) -> AbstractContextManager[None]: ...

