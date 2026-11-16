"""Name an immutable source record separately from its byte-level content identity."""

from collections.abc import Mapping

from aitest.domain.canonical import canonical_hexdigest


def source_snapshot_record_identity(payload: Mapping[str, object]) -> str:
    """Complete frozen metadata participates; a previous record ID cannot affect the name.

    This names material and does not certify its origin or validity. Warehouse CAS,
    transport IDs and intent IDs belong to the operation, outside this record body.
    """
    frozen = {key: value for key, value in payload.items() if key != "snapshot_id"}
    return "source-" + canonical_hexdigest(["aitest.business-source-snapshot/2.0", frozen])
