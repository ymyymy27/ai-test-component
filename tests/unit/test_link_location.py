"""First-anomaly, upstream/downstream and location derivation for links."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aitest.adapters.link import import_link
from aitest.core.link_location import locate_link, parse_location


def _node(
    node_id: str,
    status: str,
    *,
    parent: str = "",
    evidence_ref: str = "",
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "node_id": node_id,
        "type": "log",
        "status": status,
        "duration": None,
        "message": "",
        "evidence_ref": evidence_ref,
    }
    if parent:
        node["parent_id"] = parent
    return node


def test_locate_first_anomaly_upstream_downstream_and_location() -> None:
    nodes = [
        _node("root", "passed", evidence_ref="C:\\logs\\app.log:1"),
        _node("mid", "failed", parent="root", evidence_ref="C:\\logs\\app.log:12"),
        _node("child", "unknown", parent="mid", evidence_ref="C:\\logs\\app.log:14"),
        _node("sibling", "passed", parent="mid", evidence_ref="C:\\logs\\app.log:15"),
    ]
    assertions = [{"node_id": "mid", "expected": "1", "actual": "2", "result": "failed"}]

    result = locate_link(nodes, assertions)

    assert result["first_anomaly"]["node_id"] == "mid"
    assert [node["node_id"] for node in result["upstream"]] == ["root"]
    assert [node["node_id"] for node in result["downstream"]] == ["child", "sibling"]
    assert result["location"] == {"known": True, "path": "C:\\logs\\app.log", "line": 12}


def test_locate_uses_failed_assertion_when_every_node_passed() -> None:
    nodes = [_node("n1", "passed", evidence_ref="app.log:3")]
    assertions = [{"node_id": "n1", "expected": "a", "actual": "b", "result": "failed"}]

    result = locate_link(nodes, assertions)

    assert result["first_anomaly"]["node_id"] == "n1"
    assert result["location"] == {"known": True, "path": "app.log", "line": 3}


def test_locate_reports_unknown_location_for_database_reference() -> None:
    result = locate_link([_node("db:1", "error", evidence_ref="db:req-1:1")], [])

    assert result["first_anomaly"]["node_id"] == "db:1"
    assert result["location"]["known"] is False
    assert "数据库" in result["location"]["reason"]


def test_locate_reports_unknown_when_no_anomaly_or_evidence() -> None:
    empty = locate_link([_node("n1", "passed")], [])
    assert empty["first_anomaly"] is None
    assert empty["location"]["known"] is False

    missing = locate_link([_node("n1", "error")], [])
    assert missing["first_anomaly"]["node_id"] == "n1"
    assert missing["location"]["known"] is False
    assert "没有证据引用" in missing["location"]["reason"]


def test_parse_location_rejects_malformed_references() -> None:
    assert parse_location("just-a-name")["known"] is False
    assert parse_location("app.log:0")["known"] is False
    assert parse_location("app.log:abc")["known"] is False


def test_import_link_returns_location_without_changing_existing_fields(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "app.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "request_id": "req-1",
                "logger": "api",
                "level": "error",
                "msg": "boom",
                "timestamp": "2026-01-01T00:00:01Z",
            }
        ),
        encoding="utf-8",
    )

    result = import_link(
        "run-1",
        "req-1",
        log_paths=[log_path],
        log_format="jsonl",
        log_fields={"message": "msg", "status": "level", "module": "logger"},
        db_query=None,
    )

    assert [node["message"] for node in result["nodes"]] == ["boom"]
    assert result["assertions"] == []
    assert {"node_id": "db", "reason": "未配置数据库查询"} in result["gaps"]
    assert result["location"]["first_anomaly"]["node_id"].startswith("log:")
    assert result["location"]["first_anomaly"]["node_id"].endswith(":1")
    assert result["location"]["location"] == {
        "known": True,
        "path": str(log_path),
        "line": 1,
    }
