"""In-memory UnitOfWork used to validate application closure without A package."""

from __future__ import annotations

from aitest.contracts.execution_facts import ExecutionFacts


class FakeUnitOfWork:
    def __init__(self) -> None:
        self._facts: dict[str, ExecutionFacts] = {}

    def save_execution_facts(self, facts: ExecutionFacts) -> None:
        existing = self._facts.get(facts.snapshot_commit_id)
        if existing is not None and existing != facts:
            raise ValueError("commit already has different ExecutionFacts")
        self._facts[facts.snapshot_commit_id] = facts

    def get_execution_facts(self, commit_id: str) -> ExecutionFacts:
        return self._facts[commit_id]

    def list_commits(self) -> tuple[str, ...]:
        return tuple(sorted(self._facts))


__all__ = ["FakeUnitOfWork"]
