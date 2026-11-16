"""Parse standard JUnit XML from pytest without executing anything.

The parser is deliberately conservative: malformed XML, DOCTYPE/ENTITY,
oversized reports, and missing locations become explicit gaps instead of a
made-up pass or location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree

MAX_JUNIT_BYTES = 8 * 1024 * 1024
MAX_TESTCASES = 20_000
_FRAME = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<function>[^\r\n]+)')
_SHORT_FRAME = re.compile(
    r"^(?P<path>[^\s:][^:\r\n]*?):(?P<line>\d+)"
    r"(?:: in (?P<function>[^\r\n:]+))?(?:: .*)?$",
    re.MULTILINE,
)
_TEST_PART = re.compile(r"(^|/)(tests?|__tests__)(/|$)|(^|/)test_[^/]*\.py$|(^|/)conftest\.py$")
_LIBRARY_PARTS = frozenset(
    {"site-packages", ".venv", "venv", "_pytest", "lib/python", "python3", "python3.11"}
)


class JUnitParseError(ValueError):
    code = "B_JUNIT_INVALID"


@dataclass(frozen=True, slots=True)
class FailureFrame:
    path: str
    line: int
    function: str
    snapshot_path: str | None
    exists_in_snapshot: bool


@dataclass(frozen=True, slots=True)
class TestResult:
    node_id: str
    classname: str
    name: str
    file: str | None
    line: int | None
    outcome: str
    category: str
    failure_type: str | None = None
    message: str | None = None
    stack: str = ""
    failure_frame: FailureFrame | None = None
    suspect_source_frame: FailureFrame | None = None
    location_unknown: bool = False
    system_out: str = ""
    system_err: str = ""


@dataclass(frozen=True, slots=True)
class JUnitParseResult:
    tests: tuple[TestResult, ...] = ()
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    collection_errors: tuple[str, ...] = ()
    exit_code: int | None = None
    expected_test_count: int | None = None
    count_matches: bool = True
    exit_conflict: bool = False
    issues: tuple[str, ...] = ()

    @property
    def all_skipped(self) -> bool:
        return self.total > 0 and self.skipped == self.total


def _tag(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _text(element: ElementTree.Element | None) -> str:
    return "" if element is None or element.text is None else element.text


def _snapshot_path(path: str, source_files: frozenset[str]) -> str | None:
    normalized = path.replace("\\", "/").lstrip("./")
    if normalized in source_files:
        return normalized
    matches = [
        candidate
        for candidate in source_files
        if candidate.endswith("/" + normalized)
        or normalized.endswith("/" + candidate)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PART.search(path)) or any(part in _LIBRARY_PARTS for part in path.split("/"))


def _classname_path(classname: str, source_files: frozenset[str]) -> str | None:
    if not classname:
        return None
    candidate = classname.replace(".", "/") + ".py"
    return candidate if candidate in source_files else None


def _frames(stack: str, source_files: frozenset[str]) -> tuple[FailureFrame, ...]:
    frames: list[FailureFrame] = []
    for match in _FRAME.finditer(stack):
        raw = match.group("path")
        snapshot = _snapshot_path(raw, source_files)
        frames.append(
            FailureFrame(
                path=raw,
                line=int(match.group("line")),
                function=match.group("function").strip(),
                snapshot_path=snapshot,
                exists_in_snapshot=snapshot is not None,
            )
        )
    for match in _SHORT_FRAME.finditer(stack):
        raw = match.group("path").strip()
        if raw.startswith(("E ", ">", "assert ")):
            continue
        snapshot = _snapshot_path(raw, source_files)
        frames.append(
            FailureFrame(
                path=raw,
                line=int(match.group("line")),
                function=(match.group("function") or "").strip(),
                snapshot_path=snapshot,
                exists_in_snapshot=snapshot is not None,
            )
        )
    return tuple(frames)


def _location(
    frames: tuple[FailureFrame, ...],
) -> tuple[FailureFrame | None, FailureFrame | None, bool]:
    if not frames:
        return None, None, True
    failure = frames[-1]
    suspect = next(
        (
            frame
            for frame in reversed(frames)
            if frame.exists_in_snapshot
            and frame.snapshot_path is not None
            and not _is_test_path(frame.snapshot_path)
        ),
        None,
    )
    # A failure frame is a real location even when the parser cannot map it to
    # the frozen snapshot (e.g. JUnit-only readers have no file list). Missing
    # suspect_source_frame is a separate fact, not "location unknown".
    return failure, suspect, False


def _category(outcome: str, failure_type: str | None) -> str:
    if outcome == "skipped":
        return "skipped"
    if outcome == "passed":
        return "passed"
    if outcome == "error":
        return "error"
    lowered = (failure_type or "").lower()
    if "assert" in lowered:
        return "assertion"
    return "failure"


def parse_junit(
    content: bytes,
    *,
    source_files: frozenset[str],
    expected_test_count: int | None = None,
    exit_code: int | None = None,
) -> JUnitParseResult:
    if len(content) > MAX_JUNIT_BYTES:
        raise JUnitParseError("JUnit XML exceeds its 8 MiB budget")
    head = content[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise JUnitParseError("JUnit XML must not declare DOCTYPE or ENTITY")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise JUnitParseError(f"JUnit XML is malformed: {error}") from error

    cases = [element for element in root.iter() if _tag(element) == "testcase"]
    if len(cases) > MAX_TESTCASES:
        raise JUnitParseError("JUnit XML contains too many test cases")
    results: list[TestResult] = []
    collection_errors: list[str] = []
    issues: list[str] = []
    for case in cases:
        file = case.get("file")
        line_text = case.get("line")
        line = int(line_text) if line_text is not None and line_text.isdigit() else None
        classname = case.get("classname", "")
        name = case.get("name", "")
        file = file or _classname_path(classname, source_files)
        node_id = f"{file}::{name}" if file else f"{classname}::{name}"
        failure = next((child for child in case if _tag(child) == "failure"), None)
        error_element = next((child for child in case if _tag(child) == "error"), None)
        skipped_element = next((child for child in case if _tag(child) == "skipped"), None)
        if failure is not None:
            outcome = "failed"
        elif error_element is not None:
            outcome = "error"
        elif skipped_element is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        bad = failure if failure is not None else error_element
        stack = _text(bad)
        failure_type = bad.get("type") if bad is not None else None
        message = bad.get("message") if bad is not None else None
        frames = _frames(stack, source_files)
        failure_frame, suspect_frame, unknown = _location(frames)
        system_out = _text(next((child for child in case if _tag(child) == "system-out"), None))
        system_err = _text(next((child for child in case if _tag(child) == "system-err"), None))
        result = TestResult(
            node_id=node_id,
            classname=classname,
            name=name,
            file=file,
            line=line,
            outcome=outcome,
            category=_category(outcome, failure_type),
            failure_type=failure_type,
            message=message,
            stack=stack[:16_000],
            failure_frame=failure_frame,
            suspect_source_frame=suspect_frame,
            location_unknown=unknown and outcome in {"failed", "error"},
            system_out=system_out[:8_000],
            system_err=system_err[:8_000],
        )
        results.append(result)
        if result.location_unknown:
            issues.append(f"location_unknown:{node_id}")

    total = len(results)
    passed = sum(item.outcome == "passed" for item in results)
    failed = sum(item.outcome == "failed" for item in results)
    errors = sum(item.outcome == "error" for item in results)
    skipped_count = sum(item.outcome == "skipped" for item in results)
    suites = [element for element in root.iter() if _tag(element) == "testsuite"]
    for suite in suites:
        if suite.get("errors", "0").isdigit() and int(suite.get("errors", "0")) > 0 and total == 0:
            collection_errors.append("JUnit reports a suite-level collection error")
    count_matches = expected_test_count is None or total >= expected_test_count
    if expected_test_count is not None and total < expected_test_count:
        issues.append(f"test_count_incomplete:{total}/{expected_test_count}")
    exit_conflict = bool(
        exit_code is not None
        and (
            (exit_code == 0 and (failed or errors or collection_errors))
            or (exit_code == 1 and total > 0 and not failed and not errors)
        )
    )
    if exit_conflict:
        issues.append("pytest_exit_junit_conflict")
    return JUnitParseResult(
        tests=tuple(results),
        total=total,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped_count,
        collection_errors=tuple(collection_errors),
        exit_code=exit_code,
        expected_test_count=expected_test_count,
        count_matches=count_matches,
        exit_conflict=exit_conflict,
        issues=tuple(issues),
    )
