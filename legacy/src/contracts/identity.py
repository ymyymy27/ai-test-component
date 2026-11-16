"""Stable identity value types for the local protocol.

The transport request identity and the persisted business intent identity are
both serialized as strings, but they have different lifetimes and purposes.
Keeping distinct annotated types makes that boundary visible in every
contract model without changing the wire representation.
"""

from typing import Annotated

from pydantic import StringConstraints

_IDENTITY_CONSTRAINTS = StringConstraints(
    min_length=1,
    max_length=128,
    pattern=r"^\S+$",
)

# A request identity is scoped to one transport attempt and is safe to reuse
# only when retrying the same request payload.
RequestId = Annotated[str, _IDENTITY_CONSTRAINTS]

# An intent identity is created by the application and persists with the
# business record. It must not be substituted with a request identity.
IntentId = Annotated[str, _IDENTITY_CONSTRAINTS]


__all__ = ["IntentId", "RequestId"]
