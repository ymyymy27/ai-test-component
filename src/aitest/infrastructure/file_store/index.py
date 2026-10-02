"""Finite, read-only QuerySpec execution over a maintained index.

游标合同（架构《存储与恢复》第 13 节）：分页游标固定查询身份（完整查询
条件的确定性摘要 qid）、索引代次（generation）、提交根（commit_id）与
快照内偏移（offset）。在 (qid, generation, commit_id) 三元组下，过滤后
的行集不可变（只追加记录、维护重建必晋升代次、超根新提交被过滤），因此
偏移是确定性的，且 token 足够短以满足 QuerySpec 的冻结长度约束。

- 更换 project_id / 筛选 / 排序 / 方向复用游标 → ``invalid_cursor``；
- 索引经维护重建（generation 增加）→ 旧游标 ``invalid_cursor``，必须刷新；
- 同一代次内新提交不破坏翻页：页内只返回 ``commit_sequence <= 游标根``
  的行，翻页继续旧快照，用户刷新（无游标首页）才换根。

索引文件只保存最小摘要与精确记录引用，不复制证据正文。
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitest.contracts.queries import QuerySpec

from . import atomic

_CURSOR_SCHEMA = "aitest.index-cursor/2"
_SPEC_SCHEMA = "aitest.query-spec/1"


@dataclass(frozen=True, slots=True)
class IndexQueryResult:
    status: str
    items: tuple[dict[str, Any], ...] = ()
    next_cursor: str | None = None
    index_version: int | None = None
    generation: int | None = None
    commit_id: int | None = None


class IndexMissing(RuntimeError):
    code = "INDEX_REBUILD_REQUIRED"


def _condition_binding(spec: QuerySpec) -> tuple[object, ...]:
    """参与游标绑定的完整查询条件；页大小不在绑定内。"""
    return (
        spec.project_id,
        spec.aggregate_kind,
        spec.record_id,
        spec.revision,
        spec.sort,
        spec.descending,
    )


def _query_id(spec: QuerySpec) -> str:
    """由完整条件确定性派生的查询目录身份。"""
    raw = json.dumps(
        list(_condition_binding(spec)), separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def encode_cursor(
    offset: int,
    *,
    spec: QuerySpec,
    generation: int,
    commit_id: int,
) -> str:
    """Encode a page cursor bound to spec/generation/commit/snapshot offset."""
    raw = json.dumps(
        {
            "schema": _CURSOR_SCHEMA,
            "spec": _SPEC_SCHEMA,
            "qid": _query_id(spec),
            "gen": generation,
            "commit": commit_id,
            "offset": offset,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


@dataclass(frozen=True, slots=True)
class DecodedCursor:
    query_id: str
    generation: int
    commit_id: int
    offset: int


def decode_cursor(token: str) -> DecodedCursor:
    """Decode a bound cursor; raise ValueError on malformed/foreign tokens."""
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
    if not isinstance(payload.get("qid"), str):
        raise ValueError("invalid index cursor")
    generation = payload.get("gen")
    commit_id = payload.get("commit")
    offset = payload.get("offset")
    if (
        not isinstance(generation, int)
        or not isinstance(commit_id, int)
        or not isinstance(offset, int)
        or offset < 0
    ):
        raise ValueError("invalid index cursor")
    return DecodedCursor(
        query_id=str(payload["qid"]),
        generation=generation,
        commit_id=commit_id,
        offset=offset,
    )


class FileQueryIndex:
    """索引投影；正常提交保留代次，维护重建才晋升代次。"""

    VERSION = 2
    _FIRST_GENERATION = 1

    def __init__(self, root: Path) -> None:
        self.path = root.resolve() / "indexes.json"

    # ----- 发布 -------------------------------------------------------

    def rebuild(
        self, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]
    ) -> None:
        """正常业务提交发布索引：保留既有代次，推进提交根。

        索引是可重建投影；提交只追加行，代次不因每次提交变化，否则翻页
        游标会被无谓作废。
        """
        generation = self._read_generation()
        commit_id = max(
            (int(row["commit_sequence"]) for row in rows if "commit_sequence" in row),
            default=0,
        )
        self._write(
            version=self.VERSION,
            generation=generation,
            commit_id=commit_id,
            rows=[dict(row) for row in rows],
        )

    def rebuild_for_maintenance(
        self, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]
    ) -> int:
        """显式维护重建：晋升一个代次，旧游标按合同要求刷新。"""
        previous = self._read_generation()
        generation = previous + 1
        commit_id = max(
            (int(row["commit_sequence"]) for row in rows if "commit_sequence" in row),
            default=0,
        )
        self._write(
            version=self.VERSION,
            generation=generation,
            commit_id=commit_id,
            rows=[dict(row) for row in rows],
        )
        return generation

    def _write(
        self,
        *,
        version: int,
        generation: int,
        commit_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        atomic.write_json(
            self.path,
            {
                "version": version,
                "generation": generation,
                "last_commit_sequence": commit_id,
                "rows": rows,
            },
        )

    def _read_generation(self) -> int:
        raw = self._read_raw()
        if raw is None:
            return self._FIRST_GENERATION
        generation = raw.get("generation")
        return int(generation) if isinstance(generation, int) else self._FIRST_GENERATION

    def _read_raw(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    # ----- 查询 -------------------------------------------------------

    def _sort_key(self, spec: QuerySpec, row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row.get(spec.sort),
            row.get("aggregate_kind"),
            row.get("record_id"),
            row.get("revision"),
        )

    def query_spec(self, spec: QuerySpec) -> IndexQueryResult:
        raw = self._read_raw()
        if raw is None:
            return IndexQueryResult(status="maintenance_required")
        if (
            raw.get("version") != self.VERSION
            or not isinstance(raw.get("rows"), list)
            or not isinstance(raw.get("generation"), int)
            or not isinstance(raw.get("last_commit_sequence"), int)
        ):
            return IndexQueryResult(status="maintenance_required")
        # 坏行显式报告维护状态，绝不静默忽略后仍返回 ok。
        if any(not isinstance(row, dict) for row in raw["rows"]):
            return IndexQueryResult(status="maintenance_required")
        generation = int(raw["generation"])
        index_commit = int(raw["last_commit_sequence"])

        decoded: DecodedCursor | None = None
        if spec.cursor:
            try:
                decoded = decode_cursor(spec.cursor)
            except ValueError:
                return IndexQueryResult(
                    status="invalid_cursor",
                    index_version=self.VERSION,
                    generation=generation,
                    commit_id=index_commit,
                )
            # 游标与查询身份（完整条件摘要）、代次任一不一致都拒绝，
            # 不能悄悄换查询目录或换根。
            if decoded.query_id != _query_id(spec) or (
                decoded.generation != generation
            ):
                return IndexQueryResult(
                    status="invalid_cursor",
                    index_version=self.VERSION,
                    generation=generation,
                    commit_id=index_commit,
                )

        rows = [dict(row) for row in raw["rows"]]
        rows = [row for row in rows if row.get("project_id") == spec.project_id]
        if spec.aggregate_kind is not None:
            rows = [
                row
                for row in rows
                if row.get("aggregate_kind") == spec.aggregate_kind
            ]
        if spec.record_id is not None:
            rows = [row for row in rows if row.get("record_id") == spec.record_id]
        if spec.revision is not None:
            rows = [row for row in rows if row.get("revision") == spec.revision]

        # 快照根：首页固定当前索引根；翻页固定游标携带的提交根。索引在
        # 分页间隙追加提交时，超根的新行不会混入旧快照。
        bound_commit = decoded.commit_id if decoded is not None else index_commit
        rows = [row for row in rows if int(row.get("commit_sequence", 0)) <= bound_commit]

        try:
            sorted_rows = sorted(
                rows,
                key=lambda row: self._sort_key(spec, row),
                reverse=spec.descending,
            )
        except TypeError:
            # 排序键类型不一致说明索引内容已损坏。
            return IndexQueryResult(status="maintenance_required")
        # (qid, generation, commit_id) 固定后行集不可变，偏移续读确定。
        start = decoded.offset if decoded is not None else 0
        if start > len(sorted_rows):
            # 偏移落在快照之外（理论上不可达，因行集对该三元组不可变）。
            return IndexQueryResult(
                status="invalid_cursor",
                index_version=self.VERSION,
                generation=generation,
                commit_id=bound_commit,
            )
        remaining = sorted_rows[start:]
        page = tuple(remaining[: spec.limit])
        next_offset = start + len(page)
        next_cursor = (
            encode_cursor(
                next_offset,
                spec=spec,
                generation=generation,
                commit_id=bound_commit,
            )
            if next_offset < len(sorted_rows)
            else None
        )
        return IndexQueryResult(
            status="ok",
            items=page,
            next_cursor=next_cursor,
            index_version=self.VERSION,
            generation=generation,
            commit_id=bound_commit,
        )

    def query(
        self,
        *,
        project_id: str,
        kind: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> IndexQueryResult:
        spec = QuerySpec(
            project_id=project_id,
            aggregate_kind=kind,
            limit=limit,
            cursor=cursor,
        )
        return self.query_spec(spec)
