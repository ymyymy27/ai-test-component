"""One canonical identity for immutable execution facts and their references."""

import hashlib
import json
from collections.abc import Mapping


def execution_payload_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
