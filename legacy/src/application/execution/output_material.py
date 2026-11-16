"""One saved-output identity and byte check for collection and exit admission."""

import hashlib

from aitest.application.ports import SpoolStore
from aitest.domain.execution.runs import Attempt, OutputBlockRef, OutputStreamName, SpoolManifest


class SavedOutputMaterialError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def require_saved_output_material(
    store: SpoolStore | None, attempt: Attempt, blocks: tuple[OutputBlockRef, ...]
) -> None:
    if not blocks:
        return
    if store is None:
        raise SavedOutputMaterialError("output_material_reader_unavailable")
    try:
        manifest = store.read_manifest(attempt.attempt_id)
        if not isinstance(manifest, SpoolManifest) or (
            manifest.run_id,
            manifest.step_id,
            manifest.attempt_id,
            manifest.schema_version,
        ) != (attempt.run_id, attempt.step_id, attempt.attempt_id, "aitest.spool/1.0"):
            raise ValueError("spool ownership differs from the original attempt")
        for block in blocks:
            if (
                not isinstance(block.stream_name, OutputStreamName)
                or any(
                    type(value) is not int
                    for value in (block.block_index, block.offset, block.length)
                )
                or type(block.complete) is not bool
                or block not in manifest.blocks
            ):
                raise ValueError("output reference differs from saved material")
            content = store.read_block(block)
            if (
                type(content) is not bytes
                or len(content) != block.length
                or "sha256:" + hashlib.sha256(content).hexdigest() != block.digest
            ):
                raise ValueError("saved output content differs from its reference")
    except (OSError, ValueError) as error:
        raise SavedOutputMaterialError("output_material_unverified") from error
