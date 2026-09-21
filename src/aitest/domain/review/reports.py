"""Coverage sets from phase-one functional contract section 3.

Full evidence grading and release decisions are intentionally not implemented yet.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Coverage:
    selected: frozenset[str]
    required: frozenset[str]
    executed: frozenset[str]
    reused: frozenset[str]
    verified: frozenset[str]
    passed: frozenset[str]

    def __post_init__(self) -> None:
        if self.executed & self.reused:
            raise ValueError("new execution and reuse must be disjoint")
        if not self.reused <= self.verified <= (self.executed | self.reused) <= self.selected:
            raise ValueError("invalid evidence coverage sets")
        if not self.passed <= self.verified:
            raise ValueError("unverified cases cannot pass")

    @property
    def failed(self) -> frozenset[str]:
        return self.verified - self.passed

    @property
    def unverified(self) -> frozenset[str]:
        return self.selected - self.verified
