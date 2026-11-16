"""Real TLS/auth and dynamic JSON identity across the persisted operation chain."""
import ssl
from dataclasses import replace
from pathlib import Path

from aitest.infrastructure.credentials import EnvironmentSecretProvider, SecretManager
from aitest.infrastructure.environment_configuration import HostConfiguredEnvironmentResolver
from tests.unit import test_default_start_source_chain
from tests.unit.test_configured_business_verification import (
    test_published_plan_actual_execution_query_report_and_restart as run_chain,
)
from tests.unit.test_environment_configuration import configuration, install
from tests.unit.test_python_environment import carrier as carrier


def test_authenticated_dynamic_registered_environment_chain(carrier, tmp_path, monkeypatch):
    install(monkeypatch, tmp_path, configuration(replace(
        carrier, project_id="project-ticket", environment_id="env-local",
    )))
    monkeypatch.setattr(test_default_start_source_chain, "_chain_environment",
                        HostConfiguredEnvironmentResolver)
    token = "synthetic-query-credential-993718"
    monkeypatch.setenv("AITEST_TEST_QUERY_TOKEN", token)
    monkeypatch.setattr(SecretManager, "default", classmethod(lambda cls: cls([
        EnvironmentSecretProvider({("http", "query-test"): "AITEST_TEST_QUERY_TOKEN"}),
    ])))
    certs = Path(__file__).resolve().parents[1] / "fixtures"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certs / "http_transport_test.crt", certs / "http_transport_test.key")
    create_context = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda: create_context(
        cafile=str(certs / "http_transport_test.crt"),
    ))
    run_chain(tmp_path, monkeypatch, dynamic=True, tls_context=context,
              credential_reference="query-test")
    for path in (tmp_path / "workspace").rglob("*"):
        if path.is_file():
            assert token.encode() not in path.read_bytes(), path
