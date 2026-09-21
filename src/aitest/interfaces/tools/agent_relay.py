"""MCP stdio relay boundary; cannot start a second writer or synthesize confirmations."""

from aitest.application.errors import CapabilityUnavailable


def run(binding_id: str) -> None:
    raise CapabilityUnavailable(
        "MCP relay requires the verified local pipe transport; not implemented yet"
    )
