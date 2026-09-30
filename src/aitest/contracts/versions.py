"""Stable protocol and storage schema versions owned by A."""
from typing import Final, Literal

ProtocolVersion = Literal["aitest.local/2.0"]
PROTOCOL_VERSION: Final[ProtocolVersion] = "aitest.local/2.0"
EVENT_SCHEMA_VERSION: Final[Literal["aitest.event/2.0"]] = "aitest.event/2.0"
ERROR_SCHEMA_VERSION: Final[str] = "aitest.error/2.0"
CAPABILITY_SCHEMA_VERSION: Final[str] = "aitest.capability/2.0"
STORAGE_SCHEMA_VERSION: Final[str] = "aitest.storage/1.0"

