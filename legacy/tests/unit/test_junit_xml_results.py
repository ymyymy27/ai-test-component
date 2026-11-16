from __future__ import annotations

from aitest.domain.execution.junit import JUnitParseError, parse_junit


def _xml(body: str) -> bytes:
    return (
        '<testsuite tests="2" failures="1" errors="0" skipped="0">'
        + body
        + "</testsuite>"
    ).encode()


def test_parse_assertion_failure_and_frames() -> None:
    content = _xml(
        '<testcase classname="pkg.tests.test_order" name="test_create" '
        'file="pkg/tests/test_order.py">'
        '<failure type="AssertionError" message="expected 2 got 1">'
        'File "C:/work/pkg/tests/test_order.py", line 12, in test_create\n'
        'File "C:/work/src/order/service.py", line 87, in create_order\n'
        'AssertionError: expected 2 got 1'
        "</failure></testcase>"
        '<testcase classname="pkg.tests.test_order" name="test_ok" file="pkg/tests/test_order.py"/>'
    )
    result = parse_junit(
        content,
        source_files=frozenset({"pkg/tests/test_order.py", "src/order/service.py"}),
        expected_test_count=2,
        exit_code=1,
    )
    assert result.total == 2
    assert result.failed == 1
    assert result.passed == 1
    assert result.count_matches is True
    assert result.exit_conflict is False
    failed = result.tests[0]
    assert failed.node_id == "pkg/tests/test_order.py::test_create"
    assert failed.category == "assertion"
    assert failed.failure_frame is not None
    assert failed.failure_frame.snapshot_path == "src/order/service.py"
    assert failed.failure_frame.line == 87
    assert failed.suspect_source_frame is not None
    assert failed.suspect_source_frame.snapshot_path == "src/order/service.py"
    assert failed.suspect_source_frame.line == 87
    assert failed.location_unknown is False


def test_count_mismatch_and_all_skipped() -> None:
    content = _xml(
        '<testcase classname="a" name="b"><skipped message="skip"/></testcase>'
    )
    result = parse_junit(content, source_files=frozenset(), expected_test_count=2, exit_code=0)
    assert result.all_skipped is True
    assert result.count_matches is False
    assert "test_count_incomplete" in " ".join(result.issues)


def test_missing_location_is_explicit() -> None:
    content = _xml(
        '<testcase classname="pkg.tests.test_order" name="test_create" '
        'file="pkg/tests/test_order.py">'
        '<failure type="AssertionError" message="boom">no traceback</failure></testcase>'
    )
    result = parse_junit(
        content,
        source_files=frozenset({"pkg/tests/test_order.py"}),
        expected_test_count=1,
        exit_code=1,
    )
    failed = result.tests[0]
    assert failed.location_unknown is True
    assert any(item.startswith("location_unknown:") for item in result.issues)


def test_exit_conflict_is_reported() -> None:
    content = _xml(
        '<testcase classname="a" name="b"><failure type="AssertionError" message="x"/></testcase>'
    )
    result = parse_junit(content, source_files=frozenset(), exit_code=0)
    assert result.exit_conflict is True
    assert "pytest_exit_junit_conflict" in result.issues


def test_doctype_is_rejected() -> None:
    content = b'<!DOCTYPE testsuite [<!ENTITY x "y">]><testsuite/>'
    try:
        parse_junit(content, source_files=frozenset())
    except JUnitParseError:
        return
    raise AssertionError("DOCTYPE must be rejected")
