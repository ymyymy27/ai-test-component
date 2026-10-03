"""Finite, read-only QuerySpec execution over sharded sorted index directories.

A-05 有限访问合同（架构《存储与恢复》第 13 节）

- 全局清单 ``indexes.json`` 只保存代次/提交根等元数据，不再整库存放
  rows；查询行按**查询目录**分片保存在 ``indexes/<family>/`` 下，
  每页只读取定位到的 1—2 个有界分片，无关历史增长只增加分片数，
  不增加固定范围分页的访问量。
- 每个分片是一条有序不可变行链：分片条目保存完整排序键 ``k`` 与
  最小摘要行 ``v``；目录元数据登记各分片首/尾键，定位用区间裁剪。
- 游标 v3 绑定完整查询条件摘要 qid、索引代次 generation、快照提交根
  commit_id 与**最后完整排序键**（服务端游标文件保存键本身，令牌
  保持 QuerySpec 冻结的 256 字符上限）；同并列键不漏不重。
- 报告业务结果、问题 facet 各有独立物理目录；行是否进入这些目录由
  发布方随提交给出的最小摘要字段决定，索引不复制证据正文。
- 查询结果携带快照提交根对应的事件边界游标 ``event_cursor``，
  列表续读事件与列表页处于同一提交边界。

目录损坏/缺分片/版本不符返回 ``maintenance_required``
（``INDEX_REBUILD_REQUIRED``），绝不回退全历史扫描伪装正常。
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from aitest.contracts.queries import QuerySpec

from . import atomic

if TYPE_CHECKING:
    from .events import FileEventJournal

VERSION: Final = 3
_SPEC_SCHEMA: Final = "aitest.query-spec/1"
_CURSOR_SCHEMA: Final = "aitest.index-cursor/3"
_FIRST_GENERATION: Final = 1
_DEFAULT_SHARD_SIZE: Final = 128

_ALL: Final = "*"

#: 通用记录有序目录：每个允许的排序键一份物理有序分片链。
_RECORD_ORDERINGS: Final = (
    ("records-kind", "aggregate_kind"),
    ("records-seq", "commit_sequence"),
    ("records-record", "record_id"),
    ("records-revision", "revision"),
    ("records-published", "published_sequence"),
    ("records-updated", "updated_sequence"),
)
#: 对象点查目录：(project, record_type, record_id, revision)。
_POINT_FAMILY: Final = "records-rev"
_REPORT_FAMILY: Final = "reports"
_REPORT_BY_ID_FAMILY: Final = "reports-byid"
_ISSUE_FAMILY: Final = "issues"
_ALL_FAMILIES: Final = (
    *(name for name, _field in _RECORD_ORDERINGS),
    _POINT_FAMILY,
    _REPORT_FAMILY,
    _REPORT_BY_ID_FAMILY,
    _ISSUE_FAMILY,
)
#: 排序键（QuerySpec.sort 取值）→ 有序目录名；方向必须为字段→目录，
#: 与 _RECORD_ORDERINGS 的目录→字段相反。
_SORT_TO_FAMILY: Final = {field: family for family, field in _RECORD_ORDERINGS}

#: 报告/问题“当前修订”折叠的服务端键账（只存物理键，不存正文）。
_SIDECAR_NAME: Final = ".latest-keys.json"
_CURSOR_PREFIX: Final = ".cursor-"

#: issues.list 固定 14 种掩码（facet 编码 × 是否叠加 severity）。
_FACET_CODES: Final = {
    "module": 1,
    "layer": 2,
    "review_state": 3,
    "workflow_state": 4,
    "disposition": 5,
    "blocking": 6,
}

#: 行内允许随摘要复制的最小标量字段；绝不复制证据正文（A-05）。
_SUMMARY_SCALAR_KEYS: Final = frozenset(
    {
        "business_outcome",
        "report_id",
        "run_id",
        "content_revision",
        "published_sequence",
        "updated_sequence",
        "severity",
        "review_state",
        "workflow_state",
        "disposition",
        "blocking",
        "layer",
        "issue_open",
    }
)
_SUMMARY_LIST_KEYS: Final = frozenset({"module_ids"})


@dataclass(frozen=True, slots=True)
class IndexQueryResult:
    status: str
    items: tuple[dict[str, Any], ...] = ()
    next_cursor: str | None = None
    index_version: int | None = None
    generation: int | None = None
    commit_id: int | None = None
    event_cursor: str | None = None


class IndexMissing(RuntimeError):
    code = "INDEX_REBUILD_REQUIRED"


# --------------------------------------------------------------- 摘要行


def build_index_row(
    *,
    project_id: str,
    aggregate_kind: str,
    record_id: str,
    revision: int,
    commit_sequence: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从已提交记录构造最小摘要索引行；只取白名单摘要字段（A-05）。"""
    row: dict[str, Any] = {
        "project_id": project_id,
        "aggregate_kind": aggregate_kind,
        "record_id": record_id,
        "revision": revision,
        "commit_sequence": commit_sequence,
    }
    data = payload or {}
    for key in _SUMMARY_SCALAR_KEYS:
        value = data.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            row[key] = value
    for key in _SUMMARY_LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list) and all(
            isinstance(item, str) for item in value
        ):
            row[key] = list(value)
    entries = data.get("issue_index_entries")
    if isinstance(entries, list):
        clean = [entry for entry in entries if isinstance(entry, dict)]
        if clean:
            row["issue_index_entries"] = clean
    return row


# --------------------------------------------------------------- 游标


def _condition_binding(spec: QuerySpec) -> tuple[object, ...]:
    """参与游标绑定的完整查询条件；页大小不在绑定内。"""
    return (
        spec.project_id,
        spec.aggregate_kind,
        spec.record_id,
        spec.revision,
        spec.sort,
        spec.descending,
        spec.business_outcome,
        spec.run_id,
        spec.report_id,
        spec.view,
        spec.facet,
        spec.facet_value,
        spec.severity,
    )


def _query_id(spec: QuerySpec) -> str:
    """由完整条件确定性派生的查询目录身份。"""
    raw = json.dumps(
        list(_condition_binding(spec)), separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class DecodedCursor:
    cursor_id: str
    query_id: str
    generation: int
    commit_id: int


def encode_cursor(
    cursor_id: str,
    *,
    spec: QuerySpec,
    generation: int,
    commit_id: int,
) -> str:
    """编码短游标令牌；完整末尾排序键保存在服务端游标文件中。"""
    payload = {
        "schema": _CURSOR_SCHEMA,
        "spec": _SPEC_SCHEMA,
        "cid": cursor_id,
        "qid": _query_id(spec),
        "gen": generation,
        "commit": commit_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(token: str) -> DecodedCursor:
    """解码游标；malformed/foreign token 抛 ValueError。"""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid index cursor") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != _CURSOR_SCHEMA
        or payload.get("spec") != _SPEC_SCHEMA
    ):
        raise ValueError("invalid index cursor")
    cid = payload.get("cid")
    qid = payload.get("qid")
    generation = payload.get("gen")
    commit_id = payload.get("commit")
    if not isinstance(cid, str) or not isinstance(qid, str):
        raise ValueError("invalid index cursor")
    if not isinstance(generation, int) or not isinstance(commit_id, int):
        raise ValueError("invalid index cursor")
    return DecodedCursor(
        cursor_id=cid,
        query_id=qid,
        generation=generation,
        commit_id=commit_id,
    )


# --------------------------------------------------------------- 键比较


class _Min:
    """比任何位置值都小的有序哨兵（用于前缀区间定位）。"""

    _inst: _Min | None = None

    def __new__(cls) -> _Min:
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __lt__(self, other: object) -> bool:
        return not isinstance(other, _Min)

    def __gt__(self, other: object) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Min)

    def __le__(self, other: object) -> bool:
        return True

    def __ge__(self, other: object) -> bool:
        return isinstance(other, _Min)


class _Max:
    """比任何位置值都大的有序哨兵。"""

    _inst: _Max | None = None

    def __new__(cls) -> _Max:
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __gt__(self, other: object) -> bool:
        return not isinstance(other, _Max)

    def __lt__(self, other: object) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Max)

    def __ge__(self, other: object) -> bool:
        return True

    def __le__(self, other: object) -> bool:
        return isinstance(other, _Max)


MIN = _Min()
MAX = _Max()
Key = tuple[Any, ...]


def _key_cmp(left: Key, right: Key) -> int:
    """逐列比较；长度不同时短前缀视为更小（用于部分前缀区间）。"""
    for a, b in zip(left, right, strict=False):
        if a != b:
            try:
                return -1 if a < b else 1
            except TypeError as error:
                raise IndexMissing(
                    "index shard contains incomparable keys"
                ) from error
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def _key_lt(left: Key, right: Key) -> bool:
    return _key_cmp(left, right) < 0


def _key_le(left: Key, right: Key) -> bool:
    return _key_cmp(left, right) <= 0


def _key_gt(left: Key, right: Key) -> bool:
    return _key_cmp(left, right) > 0


def _key_ge(left: Key, right: Key) -> bool:
    return _key_cmp(left, right) >= 0


def _prefix_equals(key: Key, prefix: tuple[Any, ...]) -> bool:
    """键的前 ``len(prefix)`` 列与前缀严格相等。"""
    if len(key) < len(prefix):
        return False
    return all(key[i] == prefix[i] for i in range(len(prefix)))


def _insort(
    items: list[tuple[Key, dict[str, Any]]],
    key: Key,
    row: dict[str, Any],
) -> None:
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi) // 2
        if _key_lt(items[mid][0], key):
            lo = mid + 1
        else:
            hi = mid
    items.insert(lo, (key, row))


# --------------------------------------------------------------- 分片目录


@dataclass(frozen=True, slots=True)
class _ShardInfo:
    file: str
    count: int
    first: Key
    last: Key


_Predicate = Callable[[dict[str, Any]], bool]


class _ShardDirectory:
    """一个查询目录的有序分片链；只做有界读写，不感知筛选语义。"""

    def __init__(self, root: Path, name: str, shard_size: int) -> None:
        self.dir = root / "indexes" / name
        self.meta_path = self.dir / "meta.json"
        self.name = name
        self.shard_size = max(8, shard_size)

    # ----- 元数据 -----------------------------------------------------

    def load_meta(self) -> dict[str, Any] | None:
        if not self.meta_path.exists():
            return None
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("shards"), list):
            return None
        return raw

    def _infos(self, meta: dict[str, Any]) -> list[_ShardInfo]:
        infos: list[_ShardInfo] = []
        for entry in meta["shards"]:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("file"), str)
                or not isinstance(entry.get("count"), int)
                or not isinstance(entry.get("first"), list)
                or not isinstance(entry.get("last"), list)
            ):
                raise IndexMissing(f"corrupt shard meta in {self.name}")
            infos.append(
                _ShardInfo(
                    file=str(entry["file"]),
                    count=int(entry["count"]),
                    first=tuple(entry["first"]),
                    last=tuple(entry["last"]),
                )
            )
        return infos

    def _read_shard(self, info: _ShardInfo) -> list[dict[str, Any]]:
        path = self.dir / info.file
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as error:
            raise IndexMissing(f"missing or corrupt shard {info.file}") from error
        if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
            raise IndexMissing(f"corrupt shard payload {info.file}")
        entries: list[dict[str, Any]] = raw["entries"]
        if len(entries) != info.count:
            raise IndexMissing(f"shard count mismatch {info.file}")
        return entries

    # ----- 发布 -------------------------------------------------------

    def wipe(self) -> None:
        if not self.dir.exists():
            return
        for child in self.dir.iterdir():
            if child.is_file() and child.suffix == ".json":
                child.unlink()

    def bulk_build(
        self,
        entries_with_keys: list[tuple[Key, dict[str, Any]]],
        *,
        generation: int,
        commit_id: int,
    ) -> None:
        """从全量有序行重建目录（维护或恢复的显式成本）。"""
        self.wipe()
        self.dir.mkdir(parents=True, exist_ok=True)
        ordered = sorted(entries_with_keys, key=lambda item: item[0])
        shard_infos: list[dict[str, Any]] = []
        for start in range(0, len(ordered), self.shard_size):
            chunk = ordered[start : start + self.shard_size]
            name = f"{uuid.uuid4().hex[:16]}.json"
            atomic.write_json(
                self.dir / name,
                {"entries": [
                    {"k": list(key), "v": row} for key, row in chunk
                ]},
            )
            shard_infos.append(
                {
                    "file": name,
                    "count": len(chunk),
                    "first": list(chunk[0][0]),
                    "last": list(chunk[-1][0]),
                }
            )
        atomic.write_json(
            self.meta_path,
            {
                "family": self.name,
                "generation": generation,
                "commit_id": commit_id,
                "shard_size": self.shard_size,
                "shards": shard_infos,
            },
        )

    def insert(
        self,
        entries_with_keys: list[tuple[Key, dict[str, Any]]],
        *,
        generation: int,
        commit_id: int,
    ) -> None:
        """把新行插入有序链；只重写落点分片（超长时分裂为两个文件）。"""
        if not entries_with_keys:
            return
        meta = self.load_meta()
        if meta is None or not meta.get("shards"):
            self.bulk_build(
                entries_with_keys, generation=generation, commit_id=commit_id
            )
            return
        infos = self._infos(meta)
        targets: dict[int, list[tuple[Key, dict[str, Any]]]] = {}
        for key, row in sorted(entries_with_keys, key=lambda item: item[0]):
            index = self._locate_shard_for_write(infos, key)
            targets.setdefault(index, []).append((key, row))
        kept = self._rewrite_shards(infos, targets, frozenset())
        self._publish_meta(kept, generation, commit_id)

    def replace(
        self,
        entries_with_keys: list[tuple[Key, dict[str, Any]]],
        evict_keys: list[Key],
        *,
        generation: int,
        commit_id: int,
    ) -> None:
        """移除旧键后插入新行（报告新修订/问题投影迁移的同目录键替换）。

        只重写落点分片与确含旧键的分片；被清空的分片连同文件一并移除。
        """
        if not entries_with_keys and not evict_keys:
            return
        meta = self.load_meta()
        if meta is None or not meta.get("shards"):
            self.bulk_build(
                entries_with_keys, generation=generation, commit_id=commit_id
            )
            return
        infos = self._infos(meta)
        targets: dict[int, list[tuple[Key, dict[str, Any]]]] = {}
        for key, row in sorted(entries_with_keys, key=lambda item: item[0]):
            index = self._locate_shard_for_write(infos, key)
            targets.setdefault(index, []).append((key, row))
        for old_key in evict_keys:
            targets.setdefault(self._locate_shard_for_write(infos, old_key), [])
        kept = self._rewrite_shards(infos, targets, frozenset(evict_keys))
        self._publish_meta(kept, generation, commit_id)

    def _publish_meta(
        self, infos: list[_ShardInfo], generation: int, commit_id: int
    ) -> None:
        atomic.write_json(
            self.meta_path,
            {
                "family": self.name,
                "generation": generation,
                "commit_id": commit_id,
                "shard_size": self.shard_size,
                "shards": [
                    {
                        "file": info.file,
                        "count": info.count,
                        "first": list(info.first),
                        "last": list(info.last),
                    }
                    for info in infos
                ],
            },
        )

    def _rewrite_shards(
        self,
        infos: list[_ShardInfo],
        targets: dict[int, list[tuple[Key, dict[str, Any]]]],
        evict_keys: frozenset[Key],
    ) -> list[_ShardInfo]:
        """重写受影响分片：剔除旧键、并入新行、超长分裂、清空即移除。"""
        new_infos: list[_ShardInfo | None] = list(infos)
        extras: list[_ShardInfo] = []
        for index, additions in sorted(targets.items()):
            merged = [
                (tuple(item["k"]), item["v"])
                for item in self._read_shard(infos[index])
                if tuple(item["k"]) not in evict_keys
            ]
            existing_keys = {key for key, _ in merged}
            for key, row in additions:
                if key not in existing_keys:
                    _insort(merged, key, row)
            old_file = infos[index].file
            if not merged:
                # 分片被整体清空：从信息链移除并删除文件，不留空壳区间。
                new_infos[index] = None
            else:
                written = self._rewrite_or_split(merged)
                new_infos[index] = written[0]
                extras.extend(written[1:])
            with suppress(OSError):
                (self.dir / old_file).unlink()
        kept = [info for info in new_infos if info is not None]
        kept.extend(extras)
        # 分裂产生的新分片按首键重新排序信息链。
        kept.sort(key=lambda info: info.first)
        return kept

    def _locate_shard_for_write(
        self, infos: list[_ShardInfo], key: Key
    ) -> int:
        for index, info in enumerate(infos):
            if _key_le(key, info.last):
                return index
        return len(infos) - 1

    def _rewrite_or_split(
        self, merged: list[tuple[Key, dict[str, Any]]]
    ) -> list[_ShardInfo]:
        chunks: list[list[tuple[Key, dict[str, Any]]]]
        if len(merged) <= self.shard_size:
            chunks = [merged]
        else:
            middle = len(merged) // 2
            chunks = [merged[:middle], merged[middle:]]
        written: list[_ShardInfo] = []
        for chunk in chunks:
            name = f"{uuid.uuid4().hex[:16]}.json"
            atomic.write_json(
                self.dir / name,
                {"entries": [
                    {"k": list(key), "v": row} for key, row in chunk
                ]},
            )
            written.append(
                _ShardInfo(
                    file=name,
                    count=len(chunk),
                    first=chunk[0][0],
                    last=chunk[-1][0],
                )
            )
        return written

    # ----- 读取：有界页扫描 ------------------------------------------

    def page(
        self,
        *,
        prefix: tuple[Any, ...],
        start_after: Key | None,
        descending: bool,
        limit: int,
        accept: _Predicate,
    ) -> tuple[list[dict[str, Any]], Key | None, bool]:
        """在固定列前缀区间按键集续读；返回 (行, 最后返回行键, 是否还有)。

        ``accept(row)`` 承担前缀等值之外的全部有限谓词（精确 ID、修订、
        提交根等）；命中 limit+1 行即证明还有下一页。只访问与前缀区间
        重叠的分片文件。
        """
        meta = self.load_meta()
        if meta is None:
            raise IndexMissing(f"index directory missing: {self.name}")
        infos = self._infos(meta)
        region_start: Key = (*prefix, MIN)
        region_end: Key = (*prefix, MAX)
        ordered = reversed(infos) if descending else iter(infos)
        collected: list[dict[str, Any]] = []
        last_key: Key | None = None
        for info in ordered:
            if not (_key_le(info.first, region_end) and _key_ge(info.last, region_start)):
                continue
            entries = self._read_shard(info)
            indexed = [(tuple(item["k"]), item["v"]) for item in entries]
            if descending:
                indexed.reverse()
            for key, row in indexed:
                if not _prefix_equals(key, prefix):
                    continue
                if start_after is not None:
                    if descending:
                        if not _key_lt(key, start_after):
                            continue
                    elif not _key_gt(key, start_after):
                        continue
                if not accept(row):
                    continue
                if len(collected) == limit:
                    # 第 limit+1 个命中行只用于证明还有下一页；游标末键
                    # 仍为已返回的第 limit 行完整排序键。
                    return collected, last_key, True
                collected.append(row)
                last_key = key
        return collected, last_key, False


# --------------------------------------------------------------- 索引门面


def _issue_mask(facet: str, severity: str | None) -> int:
    """issues.list 固定掩码：NONE=0、仅 severity=1，其余 facet 占相邻两码。"""
    if facet == "NONE":
        return 1 if severity is not None else 0
    return _FACET_CODES[facet] * 2 + (1 if severity is not None else 0)


def _row_int(row: dict[str, Any], key: str, default: int) -> int:
    value = row.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


@dataclass(frozen=True, slots=True)
class _QueryPlan:
    family: str
    prefix: Key
    descending: bool
    extra: _Predicate


class FileQueryIndex:
    """分片查询索引门面：全局元数据 + 固定查询目录 + 键集游标。

    普通提交只对通用记录目录做落点分片增量插入；报告/问题目录按业务
    身份折叠（一个报告/问题在当前提交下只暴露最新修订），新修订通过
    服务端键账精确驱逐旧键。任何目录缺失/损坏/版本不符都返回
    ``maintenance_required``，绝不回退全历史扫描。
    """

    VERSION = VERSION

    def __init__(
        self,
        root: Path,
        *,
        shard_size: int = _DEFAULT_SHARD_SIZE,
        journal: FileEventJournal | None = None,
    ) -> None:
        self.root = root.resolve()
        self.indexes_dir = self.root / "indexes"
        self.meta_path = self.root / "indexes.json"
        self.shard_size = max(8, shard_size)
        self._journal = journal

    # ----- 全局元数据 -------------------------------------------------

    def _read_meta(self) -> dict[str, Any] | None:
        if not self.meta_path.exists():
            return None
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(raw, dict) or raw.get("version") != VERSION:
            return None
        generation = raw.get("generation")
        last_commit = raw.get("last_commit_sequence")
        if not isinstance(generation, int) or not isinstance(last_commit, int):
            return None
        if generation < _FIRST_GENERATION or last_commit < 0:
            return None
        return {
            "version": VERSION,
            "generation": generation,
            "last_commit_sequence": last_commit,
        }

    def _write_meta(self, generation: int, commit_sequence: int) -> None:
        atomic.write_json(
            self.meta_path,
            {
                "version": VERSION,
                "schema": "aitest.index-root/3",
                "generation": generation,
                "last_commit_sequence": commit_sequence,
            },
        )

    def is_healthy(self) -> bool:
        """元数据与每个查询目录都可读才算健康；任一缺失即须维护重建。"""
        if self._read_meta() is None:
            return False
        return all(
            self._directory(family).load_meta() is not None
            for family in _ALL_FAMILIES
        )

    def _read_raw(self) -> dict[str, Any] | None:
        """同底座投影协作用只读元数据视图（不含行；行只在分片目录中）。"""
        return self._read_meta()

    # ----- 目录与键账 -------------------------------------------------

    def _directory(self, name: str) -> _ShardDirectory:
        return _ShardDirectory(self.root, name, self.shard_size)

    def _cursor_path(self, cursor_id: str) -> Path:
        return self.indexes_dir / f"{_CURSOR_PREFIX}{cursor_id}.json"

    def _sidecar_path(self) -> Path:
        return self.indexes_dir / _SIDECAR_NAME

    def _read_sidecar(self) -> dict[str, dict[str, list[list[Any]]]]:
        path = self._sidecar_path()
        empty: dict[str, dict[str, list[list[Any]]]] = {"reports": {}, "issues": {}}
        if not path.exists():
            return empty
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return empty
        if not isinstance(raw, dict):
            return empty
        for section in ("reports", "issues"):
            value = raw.get(section)
            empty[section] = value if isinstance(value, dict) else {}
        return empty

    def _write_sidecar(
        self, sidecar: dict[str, dict[str, list[list[Any]]]]
    ) -> None:
        atomic.write_json(self._sidecar_path(), sidecar)

    # ----- 行 → 物理键派生 -------------------------------------------

    @staticmethod
    def _generic_family_keys(
        row: dict[str, Any],
    ) -> list[tuple[str, Key]]:
        """通用记录进入全部排序目录与点查目录；各目录键列同类型可比。"""
        project = str(row["project_id"])
        kind = str(row["aggregate_kind"])
        record_id = str(row["record_id"])
        revision = int(row["revision"])
        sequence = int(row["commit_sequence"])
        keys: list[tuple[str, Key]] = []
        for family, field in _RECORD_ORDERINGS:
            value: Any = row.get(field)
            if not isinstance(value, int) or isinstance(value, bool):
                if field == "commit_sequence":
                    value = sequence
                elif field == "revision":
                    value = revision
                elif field == "aggregate_kind":
                    value = kind
                elif field == "record_id":
                    value = record_id
                else:
                    # published/updated 缺失的普通记录排在有序链低端。
                    value = 0
            keys.append((family, (project, kind, value, record_id, revision)))
        keys.append(
            (_POINT_FAMILY, (project, kind, record_id, revision))
        )
        return keys

    @staticmethod
    def _is_report_row(row: dict[str, Any]) -> bool:
        return isinstance(row.get("report_id"), str)

    @staticmethod
    def _report_sequence(row: dict[str, Any]) -> int:
        return _row_int(row, "published_sequence", int(row["commit_sequence"]))

    @staticmethod
    def _report_physical_keys(row: dict[str, Any]) -> list[tuple[str, Key]]:
        project = str(row["project_id"])
        report_id = str(row["report_id"])
        sequence = FileQueryIndex._report_sequence(row)
        revision = int(row["revision"])
        keys: list[tuple[str, Key]] = [
            (_REPORT_FAMILY, (project, _ALL, sequence, report_id))
        ]
        outcome = row.get("business_outcome")
        if isinstance(outcome, str) and outcome:
            keys.append(
                (_REPORT_FAMILY, (project, outcome, sequence, report_id))
            )
        keys.append(
            (_REPORT_BY_ID_FAMILY, (project, "report", report_id, sequence, revision))
        )
        run_id = row.get("run_id")
        if isinstance(run_id, str) and run_id:
            keys.append(
                (_REPORT_BY_ID_FAMILY, (project, "run", run_id, sequence, revision))
            )
        return keys

    @staticmethod
    def _issue_physical_keys(row: dict[str, Any]) -> list[tuple[str, Key]]:
        entries = row.get("issue_index_entries")
        if not isinstance(entries, list):
            return []
        project = str(row["project_id"])
        issue_id = str(row["record_id"])
        revision = int(row["revision"])
        sequence = _row_int(row, "updated_sequence", int(row["commit_sequence"]))
        keys: list[tuple[str, Key]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            view = entry.get("view")
            mask = entry.get("mask")
            if view not in ("OPEN", "ALL"):
                continue
            if not isinstance(mask, int) or not 0 <= mask <= 13:
                continue
            facet_value = entry.get("facet_value")
            facet_part = facet_value if isinstance(facet_value, str) and facet_value else _ALL
            severity = entry.get("severity")
            severity_part = severity if isinstance(severity, str) and severity else _ALL
            keys.append(
                (
                    _ISSUE_FAMILY,
                    (
                        project,
                        view,
                        mask,
                        facet_part,
                        severity_part,
                        sequence,
                        issue_id,
                        revision,
                    ),
                )
            )
        return keys

    @staticmethod
    def _select_latest(
        rows: list[dict[str, Any]],
        identity: Callable[[dict[str, Any]], tuple[str, ...]],
        rank: Callable[[dict[str, Any]], tuple[Any, ...]],
    ) -> dict[tuple[str, ...], dict[str, Any]]:
        chosen: dict[tuple[str, ...], dict[str, Any]] = {}
        for row in rows:
            ident = identity(row)
            current = chosen.get(ident)
            if current is None or rank(row) > rank(current):
                chosen[ident] = row
        return chosen

    def _derive_buckets(
        self, rows: list[dict[str, Any]]
    ) -> tuple[
        dict[str, list[tuple[Key, dict[str, Any]]]],
        dict[str, dict[str, list[list[Any]]]],
    ]:
        """从全量摘要行派生各目录键；报告/问题按身份折叠到当前最新修订。"""
        buckets: dict[str, list[tuple[Key, dict[str, Any]]]] = {
            family: [] for family in _ALL_FAMILIES
        }
        for row in rows:
            for family, key in self._generic_family_keys(row):
                buckets[family].append((key, row))

        latest_reports = self._select_latest(
            [row for row in rows if self._is_report_row(row)],
            lambda row: (str(row["project_id"]), str(row["report_id"])),
            lambda row: (
                self._report_sequence(row),
                _row_int(row, "content_revision", 0),
                int(row["commit_sequence"]),
                int(row["revision"]),
            ),
        )
        latest_issues = self._select_latest(
            [
                row
                for row in rows
                if isinstance(row.get("issue_index_entries"), list)
            ],
            lambda row: (str(row["project_id"]), str(row["record_id"])),
            lambda row: (
                _row_int(row, "updated_sequence", 0),
                int(row["commit_sequence"]),
                int(row["revision"]),
            ),
        )

        report_ledger: dict[str, list[list[Any]]] = {}
        for (project, report_id), row in latest_reports.items():
            physical = self._report_physical_keys(row)
            report_ledger[f"{project}\x1f{report_id}"] = [
                [family, list(key)] for family, key in physical
            ]
            for family, key in physical:
                buckets[family].append((key, row))

        issue_ledger: dict[str, list[list[Any]]] = {}
        for (project, issue_id), row in latest_issues.items():
            physical = self._issue_physical_keys(row)
            issue_ledger[f"{project}\x1f{issue_id}"] = [
                [family, list(key)] for family, key in physical
            ]
            for family, key in physical:
                buckets[family].append((key, row))

        return buckets, {"reports": report_ledger, "issues": issue_ledger}

    # ----- 发布：全量构建 / 维护重建 / 增量发布 ------------------------

    def _build_all(
        self,
        rows: list[dict[str, Any]],
        *,
        generation: int,
        commit_sequence: int,
    ) -> None:
        buckets, sidecar = self._derive_buckets(rows)
        self.indexes_dir.mkdir(parents=True, exist_ok=True)
        for family in _ALL_FAMILIES:
            self._directory(family).bulk_build(
                buckets[family],
                generation=generation,
                commit_id=commit_sequence,
            )
        self._write_sidecar(sidecar)
        self._write_meta(generation, commit_sequence)

    def rebuild(
        self,
        rows: list[dict[str, Any]],
        *,
        generation: int | None = None,
    ) -> None:
        """从权威边界全量重建全部目录（显式维护/恢复成本，保留代次）。"""
        prior = self._read_meta()
        chosen_generation = (
            generation
            if generation is not None
            else prior["generation"]
            if prior is not None
            else _FIRST_GENERATION
        )
        commit_sequence = max(
            (int(row["commit_sequence"]) for row in rows), default=0
        )
        self._build_all(
            list(rows),
            generation=chosen_generation,
            commit_sequence=commit_sequence,
        )

    def rebuild_for_maintenance(self, rows: list[dict[str, Any]]) -> int:
        """维护重建：晋升代次并使全部旧游标失效，返回新代次。"""
        prior = self._read_meta()
        generation = (
            prior["generation"] + 1
            if prior is not None
            else _FIRST_GENERATION
        )
        if self.indexes_dir.exists():
            for path in self.indexes_dir.glob(f"{_CURSOR_PREFIX}*.json"):
                with suppress(OSError):
                    path.unlink()
        commit_sequence = max(
            (int(row["commit_sequence"]) for row in rows), default=0
        )
        self._build_all(
            list(rows),
            generation=generation,
            commit_sequence=commit_sequence,
        )
        return generation

    def publish(
        self,
        new_rows: list[dict[str, Any]],
        *,
        commit_sequence: int,
        all_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        """一次业务提交的索引发布：通用目录增量、报告/问题目录键替换。

        索引根缺失或版本不符时，调用方必须提供权威全量行 ``all_rows``
        触发全量重建；否则抛 :class:`IndexMissing`，绝不带版本混用。
        """
        meta = self._read_meta()
        if meta is None:
            if all_rows is None:
                raise IndexMissing("index root missing; authority rebuild required")
            self._build_all(
                list(all_rows),
                generation=_FIRST_GENERATION,
                commit_sequence=commit_sequence,
            )
            return
        generation = meta["generation"]

        generic: dict[str, list[tuple[Key, dict[str, Any]]]] = {}
        for row in new_rows:
            for family, key in self._generic_family_keys(row):
                generic.setdefault(family, []).append((key, row))
        for family, entries in generic.items():
            self._directory(family).insert(
                entries, generation=generation, commit_id=commit_sequence
            )

        sidecar = self._read_sidecar()
        self._publish_report_replacements(
            new_rows, sidecar, generation, commit_sequence
        )
        self._publish_issue_replacements(
            new_rows, sidecar, generation, commit_sequence
        )
        self._write_meta(generation, commit_sequence)

    def _publish_report_replacements(
        self,
        new_rows: list[dict[str, Any]],
        sidecar: dict[str, dict[str, list[list[Any]]]],
        generation: int,
        commit_sequence: int,
    ) -> None:
        candidates = [row for row in new_rows if self._is_report_row(row)]
        if not candidates:
            return
        chosen = self._select_latest(
            candidates,
            lambda row: (str(row["project_id"]), str(row["report_id"])),
            lambda row: (
                self._report_sequence(row),
                _row_int(row, "content_revision", 0),
                int(row["commit_sequence"]),
                int(row["revision"]),
            ),
        )
        ledger = sidecar.setdefault("reports", {})
        additions: dict[str, list[tuple[Key, dict[str, Any]]]] = {
            _REPORT_FAMILY: [],
            _REPORT_BY_ID_FAMILY: [],
        }
        evictions: dict[str, list[Key]] = {
            _REPORT_FAMILY: [],
            _REPORT_BY_ID_FAMILY: [],
        }
        for (project, report_id), row in chosen.items():
            identity = f"{project}\x1f{report_id}"
            for entry in ledger.get(identity, []):
                if isinstance(entry, list) and len(entry) == 2:
                    family, key = entry
                    if isinstance(family, str) and isinstance(key, list):
                        evictions.setdefault(family, []).append(tuple(key))
            physical = self._report_physical_keys(row)
            ledger[identity] = [
                [family, list(key)] for family, key in physical
            ]
            for family, key in physical:
                additions.setdefault(family, []).append((key, row))
        for family in (_REPORT_FAMILY, _REPORT_BY_ID_FAMILY):
            if additions[family] or evictions[family]:
                self._directory(family).replace(
                    additions[family],
                    evictions[family],
                    generation=generation,
                    commit_id=commit_sequence,
                )
        self._write_sidecar(sidecar)

    def _publish_issue_replacements(
        self,
        new_rows: list[dict[str, Any]],
        sidecar: dict[str, dict[str, list[list[Any]]]],
        generation: int,
        commit_sequence: int,
    ) -> None:
        candidates = [
            row
            for row in new_rows
            if isinstance(row.get("issue_index_entries"), list)
        ]
        if not candidates:
            return
        chosen = self._select_latest(
            candidates,
            lambda row: (str(row["project_id"]), str(row["record_id"])),
            lambda row: (
                _row_int(row, "updated_sequence", 0),
                int(row["commit_sequence"]),
                int(row["revision"]),
            ),
        )
        ledger = sidecar.setdefault("issues", {})
        additions: list[tuple[Key, dict[str, Any]]] = []
        evictions: list[Key] = []
        for (project, issue_id), row in chosen.items():
            identity = f"{project}\x1f{issue_id}"
            for entry in ledger.get(identity, []):
                if isinstance(entry, list) and len(entry) == 2:
                    family, key = entry
                    if family == _ISSUE_FAMILY and isinstance(key, list):
                        evictions.append(tuple(key))
            physical = self._issue_physical_keys(row)
            ledger[identity] = [
                [family, list(key)] for family, key in physical
            ]
            # physical 为 (family, key) 对，目录 replace 需要 (key, row)；
            # 问题物理键全部位于 _ISSUE_FAMILY。
            additions.extend((key, row) for _family, key in physical)
        if additions or evictions:
            self._directory(_ISSUE_FAMILY).replace(
                additions,
                evictions,
                generation=generation,
                commit_id=commit_sequence,
            )
        self._write_sidecar(sidecar)

    # ----- 查询路由 ---------------------------------------------------

    @staticmethod
    def _plan(spec: QuerySpec) -> _QueryPlan:
        project = spec.project_id

        if spec.view is not None:
            mask = _issue_mask(spec.facet, spec.severity)
            issue_prefix: Key = (
                project,
                spec.view,
                mask,
                spec.facet_value or _ALL,
                spec.severity or _ALL,
            )
            return _QueryPlan(
                _ISSUE_FAMILY, issue_prefix, True, lambda _row: True
            )

        if spec.report_id is not None or spec.run_id is not None:
            by_id_prefix: Key
            if spec.report_id is not None:
                by_id_prefix = (project, "report", spec.report_id)
            else:
                by_id_prefix = (project, "run", spec.run_id or "")

            def _by_id_extra(
                row: dict[str, Any], outcome: str | None = spec.business_outcome
            ) -> bool:
                return outcome is None or row.get("business_outcome") == outcome

            return _QueryPlan(
                _REPORT_BY_ID_FAMILY, by_id_prefix, spec.descending, _by_id_extra
            )

        # 对象点查 (project, record_type, record_id, revision) 是身份查询，
        # 对包括 report 在内的所有聚合类型都必须可用，且保留全部历史修订；
        # 因此优先于 reports.latest 的“当前修订”分区（A-05）。
        if spec.record_id is not None and spec.aggregate_kind is not None:

            def _point_extra(
                row: dict[str, Any], revision: int | None = spec.revision
            ) -> bool:
                return revision is None or int(row["revision"]) == revision

            return _QueryPlan(
                _POINT_FAMILY,
                (project, spec.aggregate_kind, spec.record_id),
                spec.descending,
                _point_extra,
            )

        if spec.business_outcome is not None or spec.aggregate_kind == "report":
            partition = spec.business_outcome or _ALL
            return _QueryPlan(
                _REPORT_FAMILY,
                (project, partition),
                spec.descending,
                lambda _row: True,
            )

        family = _SORT_TO_FAMILY[spec.sort]
        generic_prefix: Key = (
            (project, spec.aggregate_kind)
            if spec.aggregate_kind is not None
            else (project,)
        )

        def _generic_extra(
            row: dict[str, Any],
            record_id: str | None = spec.record_id,
            revision: int | None = spec.revision,
        ) -> bool:
            if record_id is not None and str(row.get("record_id")) != record_id:
                return False
            return not (revision is not None and int(row["revision"]) != revision)

        return _QueryPlan(family, generic_prefix, spec.descending, _generic_extra)

    def query_spec(self, spec: QuerySpec) -> IndexQueryResult:
        """执行有限查询；损坏/缺目录→maintenance_required，游标不符→invalid_cursor。"""
        try:
            meta = self._read_meta()
            if meta is None:
                return IndexQueryResult(status="maintenance_required")
            generation = meta["generation"]
            current_commit = meta["last_commit_sequence"]

            bound = current_commit
            start_after: Key | None = None
            query_id = _query_id(spec)
            if spec.cursor is not None:
                try:
                    decoded = decode_cursor(spec.cursor)
                except ValueError:
                    return IndexQueryResult(status="invalid_cursor")
                if decoded.query_id != query_id or decoded.generation != generation:
                    return IndexQueryResult(status="invalid_cursor")
                if decoded.commit_id > current_commit or decoded.commit_id < 0:
                    return IndexQueryResult(status="invalid_cursor")
                try:
                    stored = json.loads(
                        self._cursor_path(decoded.cursor_id).read_text(
                            encoding="utf-8"
                        )
                    )
                except (OSError, json.JSONDecodeError, TypeError):
                    return IndexQueryResult(status="invalid_cursor")
                if not isinstance(stored, dict):
                    return IndexQueryResult(status="invalid_cursor")
                if (
                    stored.get("cid") != decoded.cursor_id
                    or stored.get("qid") != query_id
                    or stored.get("gen") != generation
                    or stored.get("commit") != decoded.commit_id
                    or not isinstance(stored.get("key"), list)
                ):
                    return IndexQueryResult(status="invalid_cursor")
                start_after = tuple(stored["key"])
                bound = decoded.commit_id

            plan = self._plan(spec)

            def _accept(row: dict[str, Any]) -> bool:
                if int(row["commit_sequence"]) > bound:
                    return False
                return plan.extra(row)

            rows, last_key, has_more = self._directory(plan.family).page(
                prefix=plan.prefix,
                start_after=start_after,
                descending=plan.descending,
                limit=spec.limit,
                accept=_accept,
            )

            next_cursor: str | None = None
            if has_more and last_key is not None:
                cursor_id = uuid.uuid4().hex
                self.indexes_dir.mkdir(parents=True, exist_ok=True)
                atomic.write_json(
                    self._cursor_path(cursor_id),
                    {
                        "cid": cursor_id,
                        "qid": query_id,
                        "gen": generation,
                        "commit": bound,
                        "key": list(last_key),
                    },
                )
                next_cursor = encode_cursor(
                    cursor_id,
                    spec=spec,
                    generation=generation,
                    commit_id=bound,
                )

            event_cursor: str | None = None
            if self._journal is not None:
                event_cursor = self._journal.snapshot_cursor(commit_sequence=bound)

            return IndexQueryResult(
                status="ok",
                items=tuple(rows),
                next_cursor=next_cursor,
                index_version=VERSION,
                generation=generation,
                commit_id=bound,
                event_cursor=event_cursor,
            )
        except IndexMissing:
            return IndexQueryResult(status="maintenance_required")
