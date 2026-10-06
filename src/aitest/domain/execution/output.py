"""A saved stream position proves its entire attached, contiguous prefix."""

from .runs import OutputBlockRef, OutputCursor, OutputStreamName


def require_saved_output_cursors(
    attempt_id: str, cursors: tuple[OutputCursor, ...], blocks: tuple[OutputBlockRef, ...]
) -> None:
    for block in blocks:
        if (
            block.attempt_id != attempt_id
            or not isinstance(block.stream_name, OutputStreamName)
            or any(
                type(value) is not int for value in (block.block_index, block.offset, block.length)
            )
            or type(block.complete) is not bool
        ):
            raise ValueError("saved output block shape or identity differs")
    if len({cursor.stream_name for cursor in cursors}) != len(cursors):
        raise ValueError("saved output cursor streams are duplicated")
    for cursor in cursors:
        if (
            cursor.attempt_id != attempt_id
            or not isinstance(cursor.stream_name, OutputStreamName)
            or type(cursor.offset) is not int
            or type(cursor.last_block_index) is not int
            or cursor.last_block_index < 0
            or cursor.durable is not True
        ):
            raise ValueError("saved output cursor is not an exact durable position")
        prefix = sorted(
            (
                b
                for b in blocks
                if b.stream_name is cursor.stream_name and b.block_index <= cursor.last_block_index
            ),
            key=lambda b: b.block_index,
        )
        if len(prefix) != cursor.last_block_index + 1:
            raise ValueError("saved output cursor has no complete attached prefix")
        end = 0
        for index, block in enumerate(prefix):
            if block.block_index != index or block.offset != end:
                raise ValueError("saved output prefix is not contiguous")
            end += block.length
        if cursor.offset != end or cursor.last_committed_digest != prefix[-1].digest:
            raise ValueError("saved output cursor differs from the attached prefix")
