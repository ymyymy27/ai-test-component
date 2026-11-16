"""Real Python execution and storage; human consent remains an explicit fixture."""

from tests.unit.test_configured_business_verification import (
    test_published_plan_actual_execution_query_report_and_restart as _chain,
)


def test_library_plan_execution_verification_passed_report_delivery_and_restart(
    tmp_path, monkeypatch,
):
    _chain(tmp_path, monkeypatch, pure=True, complete_library=True)
