"""Read-only link import adapter for logs and configured database queries.

Import only reads evidence that already exists: matched lines from configured
log files and the output of one explicitly configured read-only query. It does
not instrument the target, generate traffic, or write to logs or databases.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from aitest.errors import AiTestError

Runner = Callable[..., subprocess.CompletedProcess[str]]

_DB_TIMEOUT_SECONDS = 30
_DB_OUTPUT_LIMIT = 1_000_000
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:@/-]{1,200}$")

_DEFAULT_FIELDS: dict[str, str] = {
    "request_id": "request_id",
    "module": "module",
    "status": "status",
    "message": "message",
}

_CANONICAL_KEYS = (
    "request_id",
    "module",
    "status",
    "message",
    "duration",
    "timestamp",
    "node_id",
    "expected",
    "actual",
)

_TIMESTAMP_KEYS = ("timestamp", "time", "ts", "@timestamp", "datetime", "logged_at")
_NODE_ID_KEYS = ("node_id", "span_id")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _gap(node_id: str, reason: str) -> dict[str, str]:
    return {"node_id": node_id, "reason": reason}


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(value)
    return ""


def _duration(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            return _parse_timestamp(float(text))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _canonical(values: Mapping[str, object], fields: Mapping[str, str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in _CANONICAL_KEYS:
        configured = fields.get(key, key)
        if configured in values and values[configured] is not None:
            result[key] = values[configured]
        elif key in values and values[key] is not None:
            result[key] = values[key]
    return result


def _timestamp_value(
    canonical: Mapping[str, object], values: Mapping[str, object], fields: Mapping[str, str]
) -> datetime | None:
    direct = _parse_timestamp(canonical.get("timestamp"))
    if direct is not None:
        return direct
    names = [fields.get("timestamp", ""), *_TIMESTAMP_KEYS]
    for name in names:
        if name:
            parsed = _parse_timestamp(values.get(name))
            if parsed is not None:
                return parsed
    return None


def _node(
    canonical: Mapping[str, object],
    values: Mapping[str, object],
    *,
    kind: str,
    evidence_ref: str,
    fallback_id: str,
) -> dict[str, Any]:
    node_id = _text(canonical.get("node_id"))
    if not node_id:
        for name in _NODE_ID_KEYS:
            node_id = _text(values.get(name))
            if node_id:
                break
    if not node_id:
        node_id = fallback_id
    return {
        "node_id": node_id,
        "type": kind,
        "status": _text(canonical.get("status")) or "unknown",
        "duration": _duration(canonical.get("duration")),
        "message": _text(canonical.get("message")),
        "evidence_ref": evidence_ref,
    }


def _assertion(node: Mapping[str, Any], canonical: Mapping[str, object]) -> dict[str, str] | None:
    expected = _text(canonical.get("expected"))
    actual = _text(canonical.get("actual"))
    if not expected or not actual:
        return None
    return {
        "node_id": cast(str, node["node_id"]),
        "expected": expected,
        "actual": actual,
        "result": "passed" if expected == actual else "failed",
    }


def _expand_paths(log_paths: Sequence[str | os.PathLike[str]]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for raw in log_paths:
        pattern = os.fspath(raw)
        if any(char in pattern for char in "*?["):
            matches = glob.glob(pattern, recursive=True)
        else:
            matches = [pattern] if os.path.isfile(pattern) else []
        for match in matches:
            path = Path(match)
            if not path.is_file():
                continue
            key = os.path.normcase(str(path.resolve(strict=False)))
            if key in seen:
                continue
            seen.add(key)
            found.append(path)
    found.sort(key=_mtime, reverse=True)
    return found


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("gbk")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")


def _log_candidates(
    path: Path,
    text: str,
    request_id: str,
    *,
    log_format: str,
    fields: Mapping[str, str],
) -> list[tuple[datetime | None, dict[str, Any], dict[str, object]]]:
    candidates: list[tuple[datetime | None, dict[str, Any], dict[str, object]]] = []
    pattern: re.Pattern[str] | None = None
    if log_format == "regex":
        pattern_text = fields.get("pattern", "")
        try:
            pattern = re.compile(pattern_text)
        except re.error as exc:
            raise AiTestError(
                "LINK_CONFIG_MISSING",
                "日志正则无效",
                "修正 log_fields.pattern 后重试",
            ) from exc

    for number, line in enumerate(text.splitlines(), start=1):
        values: dict[str, object] | None = None
        if pattern is None:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(decoded, dict):
                continue
            values = {key: item for key, item in decoded.items() if isinstance(key, str)}
        else:
            matched = pattern.search(line)
            if matched is None:
                continue
            values = {key: item for key, item in matched.groupdict().items() if item is not None}

        canonical = _canonical(values, fields)
        seen_request_id = _text(canonical.get("request_id"))
        if pattern is None and seen_request_id != request_id:
            continue
        if pattern is not None and seen_request_id and seen_request_id != request_id:
            continue

        node = _node(
            canonical,
            values,
            kind="log",
            evidence_ref=f"{path}:{number}",
            fallback_id=f"log:{number}",
        )
        candidates.append((_timestamp_value(canonical, values, fields), node, canonical))
    return candidates


def _collect_logs(
    request_id: str,
    log_paths: Sequence[str | os.PathLike[str]],
    log_format: str,
    fields: Mapping[str, str],
    max_lines: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    if not log_paths:
        return [], [_gap("log", "未配置日志路径")], []
    if log_format == "regex" and not fields.get("pattern"):
        return [], [_gap("log", "未配置日志正则")], []
    paths = _expand_paths(log_paths)
    if not paths:
        return [], [_gap("log", "未找到日志文件")], []

    candidates: list[tuple[datetime | None, dict[str, Any], dict[str, object]]] = []
    for path in paths:
        text = _read_text(path)
        if text is None:
            continue
        candidates.extend(
            _log_candidates(
                path,
                text,
                request_id,
                log_format=log_format,
                fields=fields,
            )
        )

    candidates.sort(
        key=lambda item: (
            item[0] is None,
            item[0] if item[0] is not None else _EPOCH,
        )
    )

    limit = max(0, max_lines)
    gaps: list[dict[str, str]] = []
    if len(candidates) > limit:
        dropped = len(candidates) - limit
        candidates = candidates[:limit]
        gaps.append(_gap("log", f"日志行数超过 {limit} 行，已截断 {dropped} 行"))

    nodes = [node for _, node, _ in candidates]
    assertions = [
        assertion
        for _, node, canonical in candidates
        if (assertion := _assertion(node, canonical)) is not None
    ]
    if not nodes and not gaps:
        gaps.append(_gap("log", "未找到该请求的日志"))
    return nodes, gaps, assertions


def _decode_output(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def _collect_db(
    request_id: str,
    db_query: str | None,
    runner: Runner,
    fields: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    query = (db_query or "").strip()
    if not query:
        return [], [_gap("db", "未配置数据库查询")]
    if _SAFE_REQUEST_ID.fullmatch(request_id) is None:
        return [], [_gap("db", "请求 ID 含不安全字符，未执行数据库查询")]

    command = query.replace("{request_id}", request_id)
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=_DB_TIMEOUT_SECONDS,
            shell=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return [], [_gap("db", "数据库查询执行失败")]
    if result.returncode != 0:
        return [], [_gap("db", "数据库查询执行失败")]

    stdout = _decode_output(result.stdout)
    gaps: list[dict[str, str]] = []
    if len(stdout) > _DB_OUTPUT_LIMIT:
        stdout = stdout[:_DB_OUTPUT_LIMIT]
        gaps.append(_gap("db", "数据库输出超过 1MB，已截断"))
    if not stdout.strip():
        return [], [*gaps, _gap("db", "未找到该请求的数据库结果")]

    nodes: list[dict[str, Any]] = []
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        records: list[object] = [payload]
    elif isinstance(payload, list):
        records = list(payload)
    else:
        records = []

    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            continue
        values = {key: entry for key, entry in item.items() if isinstance(key, str)}
        canonical = _canonical(values, fields)
        seen_request_id = _text(canonical.get("request_id"))
        if seen_request_id and seen_request_id != request_id:
            continue
        nodes.append(
            _node(
                canonical,
                values,
                kind="db",
                evidence_ref=f"db:{request_id}:{index}",
                fallback_id=f"db:{index}",
            )
        )

    if not nodes and records:
        gaps.append(_gap("db", "未找到该请求的数据库结果"))
    if not nodes and not records:
        first_line = stdout.strip().splitlines()[0][:500]
        nodes.append(
            {
                "node_id": "db:1",
                "type": "db",
                "status": "unknown",
                "duration": None,
                "message": first_line,
                "evidence_ref": f"db:{request_id}",
            }
        )
    return nodes, gaps


def import_link(
    run_id: str,
    request_id: str,
    *,
    log_paths: Sequence[str | os.PathLike[str]],
    log_format: str,
    log_fields: Mapping[str, str] | None,
    db_query: str | None,
    runner: Runner = subprocess.run,
    max_lines: int = 5000,
    window_seconds: int = 300,
) -> dict[str, Any]:
    """Import link nodes from existing logs and one configured read-only query.

    ``run_id`` is the caller's correlation label; this adapter keeps no
    workspace state. ``window_seconds`` is accepted for the caller's run-time
    window contract, but no run start/end fact is available here, so records
    are ordered by their own timestamps without being clipped.
    """

    del run_id, window_seconds
    if log_format not in {"jsonl", "regex"}:
        raise AiTestError(
            "LINK_CONFIG_MISSING",
            "日志格式只支持 jsonl 或 regex",
            "修正 log_format 后重试",
        )

    fields = dict(_DEFAULT_FIELDS)
    if log_fields:
        for key, value in log_fields.items():
            if isinstance(key, str) and isinstance(value, str) and value:
                fields[key] = value

    if not request_id:
        gaps = [
            _gap("request_id", "缺少请求 ID，无法可靠关联"),
            _gap("log", "缺少请求 ID，未匹配日志"),
            _gap(
                "db",
                "缺少请求 ID，未执行数据库查询" if (db_query or "").strip() else "未配置数据库查询",
            ),
        ]
        return {"nodes": [], "assertions": [], "gaps": gaps}

    log_nodes, log_gaps, assertions = _collect_logs(
        request_id,
        log_paths,
        log_format,
        fields,
        max_lines,
    )
    db_nodes, db_gaps = _collect_db(request_id, db_query, runner, fields)

    return {
        "nodes": [*log_nodes, *db_nodes],
        "assertions": assertions,
        "gaps": [*log_gaps, *db_gaps],
    }
