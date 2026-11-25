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


def _has_forbidden_xml_decl(content: bytes) -> bool:
    for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = content[:65536].decode(encoding, errors="ignore").upper()
        except LookupError:
            continue
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            return True
    return False


def parse_junit(content: bytes) -> list[dict[str, Any]]:
    """Parse ``testcase`` entries from a ``testsuite`` or ``testsuites`` document."""
    if (
        b"<!DOCTYPE" in content.upper()
        or b"<!ENTITY" in content.upper()
        or _has_forbidden_xml_decl(content)
    ):
        raise ValueError("invalid JUnit XML: DTD and entity declarations are not allowed")
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("invalid JUnit XML: report exceeds 8 MiB")
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
        message = None
        if detail is not None:
            message = detail.get("message") or (detail.text or "").strip() or None
        results.append(
            {
                "name": case.get("name", ""),
                "classname": case.get("classname", ""),
                "status": status,
                "message": message,
                "time": _time(case),
            }
        )
    return results
