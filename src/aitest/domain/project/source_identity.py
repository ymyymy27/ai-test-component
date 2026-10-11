"""Name an immutable source record separately from its byte-level content identity."""

import hashlib
import json
from collections.abc import Mapping


def source_snapshot_record_identity(payload: Mapping[str, object]) -> str:
    """Complete frozen metadata participates; a previous record ID cannot affect the name.

    This names material and does not certify its origin or validity. Warehouse CAS,
    transport IDs and intent IDs belong to the operation, outside this record body.
    """
    frozen = {key: value for key, value in payload.items() if key != "snapshot_id"}
    raw = json.dumps(
        ["aitest.business-source-snapshot/2.0", frozen],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "source-" + hashlib.sha256(raw).hexdigest()
