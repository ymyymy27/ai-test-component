from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelRequest:
    system_prompt: str
    user_prompt: str
    max_tokens: int = 2_000


@dataclass(frozen=True, slots=True)
class ModelResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


class ModelProvider(Protocol):
    def complete(self, request: ModelRequest) -> ModelResult: ...

