"""已保存记录的精确读取原语。

项目里曾有 6—9 份手写的“读当前修订 → 校验信封三元组 → 校验项目归属”，
宽容度与错误文案各不相同。这里只保留一份结构校验；调用方传入自己的错误文案，
因此行为（含错误消息）可以逐字保持不变。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class ExactRecordReader(Protocol):
    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> object: ...


def read_exact(
    records: ExactRecordReader,
    *,
    aggregate_kind: str,
    record_id: str,
    revision: int,
    envelope_error: str,
    project_id: str | None = None,
    project_error: str | None = None,
) -> Mapping[str, object]:
    """按精确修订读取记录，并校验信封与（可选）项目归属。

    修订是否必须为正由调用方先校验；本函数只保证“读到的就是请求的那一条”。
    """

    record = records.read(
        aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
    )
    payload = getattr(record, "payload", None)
    if (
        getattr(record, "aggregate_kind", None),
        getattr(record, "record_id", None),
        getattr(record, "revision", None),
    ) != (aggregate_kind, record_id, revision) or (
        type(getattr(record, "revision", None)) is not int or not isinstance(payload, Mapping)
    ):
        raise ValueError(envelope_error)
    if project_id is not None and payload.get("project_id") != project_id:
        raise ValueError(project_error or envelope_error)
    return payload
