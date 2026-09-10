from typing import Protocol

from ai_test.domain.access import Identity


class IdentityPort(Protocol):
    def current_identity(self) -> Identity: ...


class SecretPort(Protocol):
    def get_secret(self, name: str) -> str | None: ...

