"""Finite, read-only QuerySpec execution over sharded sorted index directories.

A-05 有限访问合同（架构《存储与恢复》第 13 节）

- 全局清单 ``indexes.json`` 只保存代次/提交根等元数据，不再整库存放
  rows；查询行按**查询目录**分片保存在 ``indexes/<family>/`` 下，
  页读取有限目录路径和范围内叶页；单节点最多16个子引用，叶页最多
 128行（可配置8—1024），无关历史不会使单份元数据或键账增长。
- 节点按摘要校验且不可变；叶条目保存完整排序键 ``k`` 与最小摘要
  行 ``v``。报告/问题当前键账也按准确身份点查，提交只复制相关路径。
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

from pydantic import ValidationError

from aitest.contracts.queries import QuerySpec, QueryUnsupportedFilter

from . import atomic
from .commit_manifest import CommitMaterialError, FileCommitStore
from .ordered_index import OrderedIndexTree

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
_CURSOR_PREFIX: Final = ".cursor-"


def re_root_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


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
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
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
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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
                raise IndexMissing("index shard contains incomparable keys") from error
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


_Predicate = Callable[[dict[str, Any]], bool]
_DIRECTORY_SCHEMA = "aitest.sorted-directory/1"
_ROOT_SCHEMA = "aitest.index-root/3-tree"


class _ShardDirectory:
    """Immutable ordered tree with fixed-size metadata and bounded seek paths."""

    def __init__(self, root: Path, name: str, shard_size: int) -> None:
        self.dir = root / "indexes" / name
        self.meta_path = self.dir / "meta.json"
        self.name = name
        self.shard_size = min(1024, max(8, shard_size))

    def _tree(self, meta: dict[str, Any]) -> OrderedIndexTree:
        if (
            set(meta) != {"schema", "family", "generation", "commit_id", "shard_size", "root"}
            or meta["schema"] != _DIRECTORY_SCHEMA
            or meta["family"] != self.name
            or type(meta["generation"]) is not int
            or meta["generation"] < 1
            or type(meta["commit_id"]) is not int
            or meta["commit_id"] < 0
            or type(meta["shard_size"]) is not int
            or not 8 <= meta["shard_size"] <= 1024
        ):
            raise IndexMissing(f"invalid query directory metadata: {self.name}")
        return OrderedIndexTree(
            self.dir,
            _key_cmp,
            leaf_size=meta["shard_size"],
            root=meta["root"],
            leaf_reader=self._read_shard,
        )

    def load_meta(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            self._tree(raw)
            return raw
        except (OSError, ValueError, TypeError, IndexMissing):
            return None

    def _read_shard(self, info: dict[str, Any]) -> list[dict[str, Any]]:
        tree = OrderedIndexTree(self.dir, _key_cmp, leaf_size=1024)
        entries: list[dict[str, Any]] = tree.read(info)["entries"]
        return entries

    def _publish(self, tree: OrderedIndexTree, generation: int, commit_id: int) -> dict[str, Any]:
        meta = dict(
            schema=_DIRECTORY_SCHEMA,
            family=self.name,
            generation=generation,
            commit_id=commit_id,
            shard_size=tree.leaf_size,
            root=tree.root,
        )
        atomic.write_json(self.meta_path, meta)
        return meta

    def bulk_build(
        self,
        entries_with_keys: list[tuple[Key, dict[str, Any]]],
        *,
        generation: int,
        commit_id: int,
    ) -> dict[str, Any]:
        tree = OrderedIndexTree(self.dir, _key_cmp, leaf_size=self.shard_size)
        tree.bulk_build(entries_with_keys)
        return self._publish(tree, generation, commit_id)

    def insert(
        self,
        entries_with_keys: list[tuple[Key, dict[str, Any]]],
        *,
        generation: int,
        commit_id: int,
        snapshot_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.replace(
            entries_with_keys,
            [],
            generation=generation,
            commit_id=commit_id,
            snapshot_meta=snapshot_meta,
        )

    def replace(
        self,
        entries_with_keys: list[tuple[Key, dict[str, Any]]],
        evict_keys: list[Key],
        *,
        generation: int,
        commit_id: int,
        snapshot_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        meta = snapshot_meta if snapshot_meta is not None else self.load_meta()
        if meta is None:
            raise IndexMissing(f"index directory missing: {self.name}")
        try:
            tree = self._tree(meta)
            tree.replace(entries_with_keys, evict_keys)
            return self._publish(tree, generation, commit_id)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise IndexMissing(f"query directory unavailable: {self.name}") from error

    def page(
        self,
        *,
        prefix: tuple[Any, ...],
        start_after: Key | None,
        descending: bool,
        limit: int,
        accept: _Predicate,
        snapshot_meta: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], Key | None, bool]:
        meta = snapshot_meta if snapshot_meta is not None else self.load_meta()
        if meta is None:
            raise IndexMissing(f"index directory missing: {self.name}")
        try:
            tree = self._tree(meta)
            collected: list[dict[str, Any]] = []
            last_key = None
            for key, row in tree.scan(
                lower=(*prefix, MIN), upper=(*prefix, MAX), after=start_after, descending=descending
            ):
                if not _prefix_equals(key, prefix) or not accept(row):
                    continue
                if len(collected) == limit:
                    return collected, last_key, True
                collected.append(row)
                last_key = key
            return collected, last_key, False
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise IndexMissing(f"query directory unavailable: {self.name}") from error


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
        detached: bool = False,
        snapshot_meta: dict[str, Any] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.indexes_dir = self.root / "indexes"
        self.meta_path = self.root / "indexes.json"
        self.shard_size = min(1024, max(8, shard_size))
        self._journal = journal
        self._detached = detached
        self._snapshot_meta = snapshot_meta

    # ----- 全局元数据 -------------------------------------------------

    def _read_meta(self) -> dict[str, Any] | None:
        if self._snapshot_meta is not None:
            return dict(self._snapshot_meta)
        if not self._detached:
            try:
                current = FileCommitStore(self.root).read_current()
            except CommitMaterialError:
                return None
            if current is not None:
                return dict(current["manifest"]["index_root"])
        if not self.meta_path.exists():
            return None
        try:
            if self.meta_path.stat().st_size > 16 * 1024:
                return None
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(raw, dict) or raw.get("version") != VERSION:
            return None
        generation = raw.get("generation")
        last_commit = raw.get("last_commit_sequence")
        if type(generation) is not int or type(last_commit) is not int:
            return None
        if generation < _FIRST_GENERATION or last_commit < 0:
            return None
        return {
            "version": VERSION,
            "generation": generation,
            "last_commit_sequence": last_commit,
            "snapshot_root": raw.get("snapshot_root"),
            "schema": raw.get("schema"),
        }

    def _write_meta(
        self,
        generation: int,
        commit_sequence: int,
        *,
        families: dict[str, Any],
        latest_keys: dict[str, Any],
    ) -> dict[str, Any]:
        root_id = uuid.uuid4().hex
        atomic.write_json(
            self.indexes_dir / "roots" / f"{root_id}.json",
            {
                "generation": generation,
                "commit_id": commit_sequence,
                "families": families,
                "latest_keys": latest_keys,
            },
        )
        meta = {
            "version": VERSION,
            "schema": _ROOT_SCHEMA,
            "generation": generation,
            "last_commit_sequence": commit_sequence,
            "snapshot_root": root_id,
            "snapshot_sha256": hashlib.sha256(
                (self.indexes_dir / "roots" / f"{root_id}.json").read_bytes()
            ).hexdigest(),
        }
        if not self._detached:
            store = FileCommitStore(self.root)
            current = store.read_current()
            if current is None:
                atomic.write_json(self.meta_path, meta)
            else:
                manifest = dict(current["manifest"])
                if commit_sequence != manifest["commit_sequence"]:
                    raise IndexMissing("business index publication must use the commit unit")
                manifest.update(
                    index_root=meta,
                    operation="maintenance",
                    created=[],
                    request_id=None,
                    intent_id=None,
                    project_id=None,
                    parent_manifest=current["pointer"]["manifest_digest"],
                )
                store.publish(store.prepare(manifest))
        return meta

    def is_healthy(self) -> bool:
        """Only the published root is authoritative; abandoned family metas are ignored."""
        try:
            meta = self._read_meta()
            if meta is None:
                return False
            snapshot = self._snapshot(meta)
            for name, directory_meta in [
                *snapshot["families"].items(),
                ("latest-keys", snapshot["latest_keys"]),
            ]:
                tree = self._directory(name)._tree(directory_meta)
                if tree.root is not None:
                    tree.read(tree.root)
            return True
        except (IndexMissing, OSError, ValueError, KeyError, TypeError):
            return False

    def requires_layout_migration(self) -> bool:
        meta = self._read_meta()
        return meta is not None and meta["schema"] != _ROOT_SCHEMA

    def _snapshot(self, meta: dict[str, Any]) -> dict[str, Any]:
        root_id = meta.get("snapshot_root")
        if (
            meta.get("schema") != _ROOT_SCHEMA
            or not isinstance(root_id, str)
            or not re_root_id(root_id)
        ):
            raise IndexMissing("query directory layout requires explicit migration")
        try:
            path = self.indexes_dir / "roots" / f"{root_id}.json"
            FileCommitStore.reject_links(path)
            if path.stat().st_size > 256 * 1024:
                raise IndexMissing("query root exceeds bounded metadata size")
            raw_bytes = path.read_bytes()
            if meta.get("snapshot_sha256") is not None and (
                hashlib.sha256(raw_bytes).hexdigest() != meta["snapshot_sha256"]
            ):
                raise IndexMissing("query root digest mismatch")
            raw = json.loads(raw_bytes)
            if (
                not isinstance(raw, dict)
                or set(raw) != {"generation", "commit_id", "families", "latest_keys"}
                or type(raw["generation"]) is not int
                or raw["generation"] != meta["generation"]
                or type(raw["commit_id"]) is not int
                or raw["commit_id"] != meta["last_commit_sequence"]
                or not isinstance(raw["families"], dict)
                or set(raw["families"]) != set(_ALL_FAMILIES)
            ):
                raise IndexMissing("query snapshot identity or families mismatch")
            for name, directory_meta in [
                *raw["families"].items(),
                ("latest-keys", raw["latest_keys"]),
            ]:
                self._directory(name)._tree(directory_meta)
                if (
                    directory_meta["generation"] != raw["generation"]
                    or directory_meta["commit_id"] > raw["commit_id"]
                ):
                    raise IndexMissing("query directory belongs to another root")
            return raw
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise IndexMissing("query snapshot root unavailable") from error

    def _read_raw(self) -> dict[str, Any] | None:
        """同底座投影协作用只读元数据视图（不含行；行只在分片目录中）。"""
        return self._read_meta()

    # ----- 目录与键账 -------------------------------------------------

    def _directory(self, name: str) -> _ShardDirectory:
        return _ShardDirectory(self.root, name, self.shard_size)

    def _cursor_path(self, cursor_id: str) -> Path:
        return self.indexes_dir / f"{_CURSOR_PREFIX}{cursor_id}.json"

    @staticmethod
    def _latest_rank(row: dict[str, Any], section: str) -> list[int]:
        if section == "reports":
            return [
                FileQueryIndex._report_sequence(row),
                _row_int(row, "content_revision", 0),
                int(row["commit_sequence"]),
                int(row["revision"]),
            ]
        return [
            _row_int(row, "updated_sequence", 0),
            int(row["commit_sequence"]),
            int(row["revision"]),
        ]

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
        keys.append((_POINT_FAMILY, (project, kind, record_id, revision)))
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
        keys: list[tuple[str, Key]] = [(_REPORT_FAMILY, (project, _ALL, sequence, report_id))]
        outcome = row.get("business_outcome")
        if isinstance(outcome, str) and outcome:
            keys.append((_REPORT_FAMILY, (project, outcome, sequence, report_id)))
        keys.append((_REPORT_BY_ID_FAMILY, (project, "report", report_id, sequence, revision)))
        run_id = row.get("run_id")
        if isinstance(run_id, str) and run_id:
            keys.append((_REPORT_BY_ID_FAMILY, (project, "run", run_id, sequence, revision)))
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
        list[tuple[Key, dict[str, Any]]],
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
            [row for row in rows if isinstance(row.get("issue_index_entries"), list)],
            lambda row: (str(row["project_id"]), str(row["record_id"])),
            lambda row: (
                _row_int(row, "updated_sequence", 0),
                int(row["commit_sequence"]),
                int(row["revision"]),
            ),
        )

        ledger: list[tuple[Key, dict[str, Any]]] = []
        for (project, report_id), row in latest_reports.items():
            physical = self._report_physical_keys(row)
            ledger.append(
                (
                    ("reports", project, report_id),
                    {
                        "keys": [[family, list(key)] for family, key in physical],
                        "rank": self._latest_rank(row, "reports"),
                    },
                )
            )
            for family, key in physical:
                buckets[family].append((key, row))

        for (project, issue_id), row in latest_issues.items():
            physical = self._issue_physical_keys(row)
            ledger.append(
                (
                    ("issues", project, issue_id),
                    {
                        "keys": [[family, list(key)] for family, key in physical],
                        "rank": self._latest_rank(row, "issues"),
                    },
                )
            )
            for family, key in physical:
                buckets[family].append((key, row))

        return buckets, ledger

    # ----- 发布：全量构建 / 维护重建 / 增量发布 ------------------------

    @staticmethod
    def _validate_rows(rows: list[dict[str, Any]], commit_sequence: int) -> None:
        if type(commit_sequence) is not int or commit_sequence < 0:
            raise IndexMissing("invalid query publication boundary")
        for row in rows:
            if (
                type(row.get("commit_sequence")) is not int
                or not 0 <= row["commit_sequence"] <= commit_sequence
                or type(row.get("revision")) is not int
                or row["revision"] < 1
                or any(
                    not isinstance(row.get(field), str) or not row[field]
                    for field in ("project_id", "aggregate_kind", "record_id")
                )
            ):
                raise IndexMissing("index row does not belong to the publication boundary")

    def _build_all(
        self,
        rows: list[dict[str, Any]],
        *,
        generation: int,
        commit_sequence: int,
    ) -> dict[str, Any]:
        self._validate_rows(rows, commit_sequence)
        buckets, ledger = self._derive_buckets(rows)
        self.indexes_dir.mkdir(parents=True, exist_ok=True)
        families: dict[str, Any] = {}
        for family in _ALL_FAMILIES:
            families[family] = self._directory(family).bulk_build(
                buckets[family],
                generation=generation,
                commit_id=commit_sequence,
            )
        latest_keys = self._directory("latest-keys").bulk_build(
            ledger,
            generation=generation,
            commit_id=commit_sequence,
        )
        return self._write_meta(
            generation, commit_sequence, families=families, latest_keys=latest_keys
        )

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
        commit_sequence = max((int(row["commit_sequence"]) for row in rows), default=0)
        self._build_all(
            list(rows),
            generation=chosen_generation,
            commit_sequence=commit_sequence,
        )

    def rebuild_for_maintenance(self, rows: list[dict[str, Any]]) -> int:
        """维护重建：晋升代次并使全部旧游标失效，返回新代次。"""
        prior = self._read_meta()
        generation = prior["generation"] + 1 if prior is not None else _FIRST_GENERATION
        if self.indexes_dir.exists():
            for path in self.indexes_dir.glob(f"{_CURSOR_PREFIX}*.json"):
                with suppress(OSError):
                    path.unlink()
        commit_sequence = max((int(row["commit_sequence"]) for row in rows), default=0)
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
    ) -> dict[str, Any]:
        """一次业务提交的索引发布：通用目录增量、报告/问题目录键替换。

        索引根缺失或版本不符时，调用方必须提供权威全量行 ``all_rows``
        触发全量重建；否则抛 :class:`IndexMissing`，绝不带版本混用。
        """
        self._validate_rows(new_rows, commit_sequence)
        meta = self._read_meta()
        if self.requires_layout_migration():
            raise IndexMissing("legacy query directory requires backed up migration")
        if meta is None:
            if all_rows is None:
                raise IndexMissing("index root missing; authority rebuild required")
            return self._build_all(
                list(all_rows),
                generation=_FIRST_GENERATION,
                commit_sequence=commit_sequence,
            )
        generation = meta["generation"]
        snapshot = self._snapshot(meta)
        if type(commit_sequence) is not int or commit_sequence < meta["last_commit_sequence"]:
            raise IndexMissing("query publication cannot move behind the saved commit root")
        generic: dict[str, list[tuple[Key, dict[str, Any]]]] = {}
        for row in new_rows:
            for family, key in self._generic_family_keys(row):
                generic.setdefault(family, []).append((key, row))
        for family, entries in generic.items():
            snapshot["families"][family] = self._directory(family).insert(
                entries,
                generation=generation,
                commit_id=commit_sequence,
                snapshot_meta=snapshot["families"][family],
            )

        for section in ("reports", "issues"):
            self._publish_latest_replacements(
                section, new_rows, snapshot, generation, commit_sequence
            )
        return self._write_meta(
            generation,
            commit_sequence,
            families=snapshot["families"],
            latest_keys=snapshot["latest_keys"],
        )

    def _publish_latest_replacements(
        self,
        section: str,
        new_rows: list[dict[str, Any]],
        snapshot: dict[str, Any],
        generation: int,
        commit_sequence: int,
    ) -> None:
        candidates = [
            row
            for row in new_rows
            if (
                self._is_report_row(row)
                if section == "reports"
                else isinstance(row.get("issue_index_entries"), list)
            )
        ]
        if not candidates:
            return
        chosen = self._select_latest(
            candidates,
            lambda row: (
                str(row["project_id"]),
                str(row["report_id"] if section == "reports" else row["record_id"]),
            ),
            lambda row: tuple(self._latest_rank(row, section)),
        )
        ledger_directory = self._directory("latest-keys")
        additions: dict[str, list[tuple[Key, dict[str, Any]]]] = {}
        evictions: dict[str, list[Key]] = {}
        ledger_rows: list[tuple[Key, dict[str, Any]]] = []
        ledger_evict: list[Key] = []
        allowed = (
            {_REPORT_FAMILY, _REPORT_BY_ID_FAMILY} if section == "reports" else {_ISSUE_FAMILY}
        )
        try:
            ledger = ledger_directory._tree(snapshot["latest_keys"])
            for (project, record_id), row in chosen.items():
                identity = (section, project, record_id)
                prior = ledger.get(identity)
                rank = self._latest_rank(row, section)
                if prior is not None:
                    if (
                        set(prior) != {"keys", "rank"}
                        or not isinstance(prior["keys"], list)
                        or not isinstance(prior["rank"], list)
                        or len(prior["rank"]) != len(rank)
                        or any(type(part) is not int for part in prior["rank"])
                    ):
                        raise IndexMissing("invalid current identity ledger entry")
                    if rank < prior["rank"]:
                        continue
                    for entry in prior["keys"]:
                        if (
                            not isinstance(entry, list)
                            or len(entry) != 2
                            or entry[0] not in allowed
                            or not isinstance(entry[1], list)
                            or not entry[1]
                            or entry[1][0] != project
                        ):
                            raise IndexMissing("invalid current physical key ledger")
                        OrderedIndexTree._key(entry[1])
                        evictions.setdefault(entry[0], []).append(tuple(entry[1]))
                physical = (
                    self._report_physical_keys(row)
                    if section == "reports"
                    else self._issue_physical_keys(row)
                )
                value = {"keys": [[family, list(key)] for family, key in physical], "rank": rank}
                if prior is not None and rank == prior["rank"] and value != prior:
                    raise IndexMissing("conflicting current identity at the same rank")
                ledger_rows.append((identity, value))
                ledger_evict.append(identity)
                for family, key in physical:
                    additions.setdefault(family, []).append((key, row))
            for family in sorted(allowed):
                if additions.get(family) or evictions.get(family):
                    snapshot["families"][family] = self._directory(family).replace(
                        additions.get(family, []),
                        evictions.get(family, []),
                        generation=generation,
                        commit_id=commit_sequence,
                        snapshot_meta=snapshot["families"][family],
                    )
            if ledger_rows:
                ledger.replace(ledger_rows, ledger_evict)
                snapshot["latest_keys"] = ledger_directory._publish(
                    ledger,
                    generation,
                    commit_sequence,
                )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise IndexMissing("current identity ledger unavailable") from error

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
            return _QueryPlan(_ISSUE_FAMILY, issue_prefix, True, lambda _row: True)

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

            return _QueryPlan(_REPORT_BY_ID_FAMILY, by_id_prefix, spec.descending, _by_id_extra)

        # 对象点查 (project, record_type, record_id, revision) 是身份查询，
        # 对包括 report 在内的所有聚合类型都必须可用，且保留全部历史修订；
        # 因此优先于 reports.latest 的“当前修订”分区（A-05）。
        if spec.record_id is not None and spec.aggregate_kind is not None:

            def _point_extra(row: dict[str, Any], revision: int | None = spec.revision) -> bool:
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
            (project, spec.aggregate_kind) if spec.aggregate_kind is not None else (project,)
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
            # model_copy/model_construct do not run pydantic validators. Do not
            # let internal callers bypass finite routing with those objects.
            spec = QuerySpec.model_validate(spec.model_dump(warnings=False))
            meta = self._read_meta()
            if meta is None:
                return IndexQueryResult(status="maintenance_required")
            generation = meta["generation"]
            current_commit = meta["last_commit_sequence"]

            bound = current_commit
            snapshot_root = meta.get("snapshot_root")
            snapshot_sha256 = meta.get("snapshot_sha256")
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
                        self._cursor_path(decoded.cursor_id).read_text(encoding="utf-8")
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
                snapshot_root = stored.get("snapshot_root")
                snapshot_sha256 = stored.get("snapshot_sha256")

            plan = self._plan(spec)
            snapshot = self._snapshot(
                {
                    **meta,
                    "snapshot_root": snapshot_root,
                    "snapshot_sha256": snapshot_sha256,
                    "last_commit_sequence": bound,
                }
            )
            snapshot_meta = snapshot["families"][plan.family]

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
                snapshot_meta=snapshot_meta,
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
                        "snapshot_root": snapshot_root,
                        "snapshot_sha256": snapshot_sha256,
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
        except (IndexMissing, CommitMaterialError):
            return IndexQueryResult(status="maintenance_required")
        except (QueryUnsupportedFilter, ValidationError):
            return IndexQueryResult(status="unsupported_filter")


def migrate_query_layout(root: Path) -> str:
    """Explicit format conversion, called only by the backed up migration manager."""
    index = FileQueryIndex(root)
    meta = index._read_meta()
    if meta is None:
        if index.meta_path.exists():
            raise IndexMissing("unreadable legacy index root cannot be migrated")
        return "查询根不存在，首次业务提交创建新格式"
    if meta["schema"] == _ROOT_SCHEMA:
        if not index.is_healthy():
            raise IndexMissing("current query root is unhealthy")
        return "有界查询目录已存在"
    if meta["schema"] != "aitest.index-root/3":
        raise IndexMissing("unknown query layout cannot be migrated")
    before_path = index.indexes_dir / "layout-before.json"
    if before_path.exists():
        before = json.loads(before_path.read_text(encoding="utf-8"))
        if before.get("header") != json.loads(index.meta_path.read_text(encoding="utf-8")):
            raise IndexMissing("legacy query root differs from frozen migration prestate")
    else:
        header = json.loads(index.meta_path.read_text(encoding="utf-8"))
        root_id = header.get("snapshot_root")
        if not isinstance(root_id, str) or not re_root_id(root_id):
            raise IndexMissing("legacy query snapshot missing")
        snapshot = json.loads(
            (index.indexes_dir / "roots" / f"{root_id}.json").read_text(encoding="utf-8")
        )
        if (
            snapshot.get("generation") != meta["generation"]
            or snapshot.get("commit_id") != meta["last_commit_sequence"]
            or not isinstance(snapshot.get("families"), dict)
            or set(snapshot["families"]) != set(_ALL_FAMILIES)
        ):
            raise IndexMissing("legacy query snapshot identity mismatch")
        before = {"header": header, "families": snapshot["families"]}
        # Frozen before any directory write. Retry reads this same prestate,
        # even when a previous attempt already replaced some family meta files.
        atomic.write_json(before_path, before)
    legacy = before["families"][_POINT_FAMILY]
    if not isinstance(legacy.get("shards"), list):
        raise IndexMissing("legacy identity directory unavailable")
    rows: list[dict[str, Any]] = []
    previous: Key | None = None
    for ref in legacy["shards"]:
        file = ref.get("file")
        if (
            not isinstance(file, str)
            or len(file) != 21
            or not file.endswith(".json")
            or any(c not in "0123456789abcdef" for c in file[:-5])
        ):
            raise IndexMissing("invalid legacy shard path")
        node = json.loads((index.indexes_dir / _POINT_FAMILY / file).read_text(encoding="utf-8"))
        entries = node.get("entries")
        if (
            not isinstance(entries, list)
            or not entries
            or type(ref.get("count")) is not int
            or len(entries) != ref["count"]
            or entries[0].get("k") != ref.get("first")
            or entries[-1].get("k") != ref.get("last")
        ):
            raise IndexMissing("legacy shard interval mismatch")
        for entry in entries:
            key = OrderedIndexTree._key(entry["k"])
            row = entry["v"]
            if (
                not isinstance(row, dict)
                or previous is not None
                and _key_cmp(previous, key) >= 0
                or key
                != (row["project_id"], row["aggregate_kind"], row["record_id"], row["revision"])
                or type(row["commit_sequence"]) is not int
                or not 0 <= row["commit_sequence"] <= meta["last_commit_sequence"]
            ):
                raise IndexMissing("legacy identity row mismatch")
            rows.append(row)
            previous = key
    index._build_all(
        rows, generation=meta["generation"] + 1, commit_sequence=meta["last_commit_sequence"]
    )
    return "发布有界有序树与身份键账；旧材料保留，旧游标要求刷新"


def rollback_query_layout(root: Path) -> str:
    """Restore the frozen legacy root last, without deleting any immutable node."""
    index = FileQueryIndex(root)
    before_path = index.indexes_dir / "layout-before.json"
    if not before_path.exists():
        return "本步未转换旧查询根，保留现状"
    before = json.loads(before_path.read_text(encoding="utf-8"))
    header = before["header"]
    if (
        header.get("schema") != "aitest.index-root/3"
        or not isinstance(before.get("families"), dict)
        or set(before["families"]) != set(_ALL_FAMILIES)
    ):
        raise IndexMissing("query migration prestate is corrupt")
    for name, directory_meta in before["families"].items():
        atomic.write_json(index._directory(name).meta_path, directory_meta)
    atomic.write_json(index.meta_path, header)
    return "恢复旧查询根；树节点、原分片与游标材料永久保留"
