from pathlib import Path
from typing import Protocol


class Clock(Protocol):
    def iso_now(self) -> str: ...


class WorkspaceLocator(Protocol):
    def locate(self) -> Path: ...

