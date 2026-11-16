"""Request-scoped core actor; command parameters never populate this context."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from aitest.domain.approvals import ApprovalRequired, TrustedActor


class CoreActorContext:
    def __init__(self) -> None:
        self._actor: ContextVar[TrustedActor | None] = ContextVar("core_actor", default=None)

    def current(self) -> TrustedActor:
        actor = self._actor.get()
        if actor is None:
            raise ApprovalRequired("no controlled core entry is active")
        return actor

    @contextmanager
    def bind(self, actor: TrustedActor) -> Iterator[None]:
        token = self._actor.set(actor)
        try:
            yield
        finally:
            self._actor.reset(token)
