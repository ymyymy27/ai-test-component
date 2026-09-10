"""Delivery declaration models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Delivery:
    delivery_id: str
    task_id: str
    version: str
    completed: tuple[str, ...] = ()
    incomplete: tuple[str, ...] = ()

