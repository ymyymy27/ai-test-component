"""Minimal JUnit XML parsing for suite reports."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _time(case: ElementTree.Element) -> float | None:
    raw = case.get("time")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_junit(content: bytes) -> list[dict[str, Any]]:
    """Parse ``testcase`` entries from a ``testsuite`` or ``testsuites`` document."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ValueError(f"invalid JUnit XML: {error}") from error

    results: list[dict[str, Any]] = []
    for case in root.iter():
        if _local_name(case.tag) != "testcase":
            continue
        children = {_local_name(child.tag): child for child in case}
        if "failure" in children:
            status = "failed"
            detail = children["failure"]
        elif "error" in children:
            status = "error"
            detail = children["error"]
        elif "skipped" in children:
            status = "skipped"
            detail = children["skipped"]
        else:
            status = "passed"
            detail = None
        results.append(
            {
                "name": case.get("name", ""),
                "classname": case.get("classname", ""),
                "status": status,
                "message": detail.get("message") if detail is not None else None,
                "time": _time(case),
            }
        )
    return results
