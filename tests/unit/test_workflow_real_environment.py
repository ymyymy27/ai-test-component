"""Real registered venv plus execution/query/report/export; human consent remains a fixture."""

from dataclasses import replace

from aitest.infrastructure.environment_configuration import HostConfiguredEnvironmentResolver
from tests.unit import test_default_start_source_chain
from tests.unit.test_configured_business_verification import (
    test_published_plan_actual_execution_query_report_and_restart as run_chain,
)
from tests.unit.test_environment_configuration import configuration, install
from tests.unit.test_python_environment import carrier as carrier


def test_real_registered_environment_operation_chain(carrier, tmp_path, monkeypatch):
    install(monkeypatch, tmp_path, configuration(replace(
        carrier, project_id="project-ticket", environment_id="env-local",
    )))
    # Replace only the named synthetic environment helper. No product adapter,
    # execution resolver, HTTP reader, report or evidence store is substituted.
    monkeypatch.setattr(test_default_start_source_chain, "_chain_environment",
                        HostConfiguredEnvironmentResolver)
    run_chain(tmp_path, monkeypatch)
