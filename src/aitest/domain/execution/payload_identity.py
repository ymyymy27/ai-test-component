"""One canonical identity for immutable execution facts and their references."""

from collections.abc import Mapping

from aitest.domain.canonical import canonical_digest


def execution_payload_digest(payload: Mapping[str, object]) -> str:
    return canonical_digest(dict(payload))
