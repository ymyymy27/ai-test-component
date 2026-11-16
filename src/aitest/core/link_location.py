"""Locate the first anomaly in an imported link and its neighbourhood.

The result is a projection of stored facts: the first node whose status is not
``passed`` (or, when every node passed, the node of the first non-passed
assertion), the parent chain, direct children and the ``file:line`` parsed from
the node evidence reference. Missing or unparsable locations stay explicitly
unknown instead of being guessed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_BENIGN_STATUS = frozenset({"passed", "ok", "success", "info", "debug"})

_SAFE_NODE_KEYS = (
    "node_id",
    "type",
    "status",
    "duration",
    "message",
    "evidence_ref",
    "parent_id",
)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _node_id(node: Mapping[str, Any]) -> str:
    return _text(node.get("node_id"))


def _parent_id(node: Mapping[str, Any]) -> str:
    for key in ("parent_id", "parent"):
        value = _text(node.get(key))
        if value:
            return value
    return ""


def _safe_node(node: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _SAFE_NODE_KEYS:
        if key in node:
            result[key] = node[key]
    result.setdefault("node_id", _node_id(node))
    result.setdefault("status", _text(node.get("status")) or "unknown")
    parent = _parent_id(node)
    if parent:
        result["parent_id"] = parent
    return result


def _first_anomaly(
    nodes: list[dict[str, Any]], assertions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    for node in nodes:
        if _text(node.get("status")).lower() not in _BENIGN_STATUS:
            return node
    for assertion in assertions:
        if _text(assertion.get("result")).lower() == "passed":
            continue
        node_id = _text(assertion.get("node_id"))
        for node in nodes:
            if node_id and _node_id(node) == node_id:
                return node
    return None


def _upstream(node_id: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {_node_id(node): node for node in nodes if _node_id(node)}
    chain: list[dict[str, Any]] = []
    seen = {node_id}
    current = by_id.get(node_id)
    while current is not None:
        parent = _parent_id(current)
        if not parent or parent in seen:
            break
        seen.add(parent)
        parent_node = by_id.get(parent)
        if parent_node is None:
            break
        chain.append(parent_node)
        current = parent_node
    chain.reverse()
    return chain


def _downstream(node_id: str, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        node
        for node in nodes
        if _node_id(node) != node_id and _parent_id(node) == node_id
    ]


def _unknown(reason: str) -> dict[str, Any]:
    return {"known": False, "reason": reason}


def parse_location(evidence_ref: str) -> dict[str, Any]:
    """Parse ``<path>:<line>`` without assuming the path style."""
    ref = evidence_ref.strip()
    if not ref:
        return _unknown("首错节点没有证据引用")
    if ref.startswith("db:"):
        return _unknown("数据库引用不是文件位置")
    path, separator, number = ref.rpartition(":")
    if not separator or not path.strip() or not number.isdigit():
        return _unknown(f"证据引用不是 file:line 格式：{ref}")
    line = int(number)
    if line <= 0:
        return _unknown(f"证据引用的行号无效：{ref}")
    return {"known": True, "path": path.strip(), "line": line}


def locate_link(
    nodes: Sequence[Mapping[str, Any]],
    assertions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return first anomaly, upstream chain, downstream nodes and location."""
    safe_nodes = [_safe_node(node) for node in nodes]
    safe_assertions = [
        {
            "node_id": _text(item.get("node_id")),
            "result": _text(item.get("result")),
        }
        for item in assertions
    ]
    anomaly = _first_anomaly(safe_nodes, safe_assertions)
    if anomaly is None:
        return {
            "first_anomaly": None,
            "upstream": [],
            "downstream": [],
            "location": _unknown("没有可定位的异常节点"),
        }
    node_id = _node_id(anomaly)
    return {
        "first_anomaly": anomaly,
        "upstream": _upstream(node_id, safe_nodes),
        "downstream": _downstream(node_id, safe_nodes),
        "location": parse_location(_text(anomaly.get("evidence_ref"))),
    }
