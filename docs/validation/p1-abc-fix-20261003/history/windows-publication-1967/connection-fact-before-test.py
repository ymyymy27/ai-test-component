"""Connection facts retain strict types and outrank their stale capability projection."""

import json
from types import SimpleNamespace

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.infrastructure.capabilities import CONNECTION, CapabilityState
from aitest.infrastructure.connections import ConnectionFactStore, EndpointConfig, TransportFact
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

ADDRESS = "https://api.example.com:443"


@pytest.mark.parametrize("paused", [False, True])
def test_latest_saved_success_recovers_automatic_failure_without_clearing_pause(tmp_path, paused):
    first = assemble_workspace_core(
        tmp_path, instance_id="connection-before", connection_endpoint=ADDRESS
    )
    first.gate.report(CONNECTION, healthy=False, reason="old timeout", classification="timeout")
    if paused:
        first.gate.degrade(CONNECTION, "operator paused connection")
    first.lifetime_lock.release()
    ConnectionFactStore(tmp_path).append(
        endpoint_address=ADDRESS,
        fact=TransportFact(True, 3, None, ""),
        source_session="controlled-session",
        observed_at=1.0,
    )
    second = assemble_workspace_core(
        tmp_path, instance_id="connection-after", connection_endpoint=ADDRESS
    )
    try:
        automatic = {item["key"]: item for item in second.gate.snapshot()["automatic_conditions"]}
        assert automatic[CONNECTION]["state"] == "ready"
        assert second.gate.condition(CONNECTION).manual is paused
        if paused:
            assert second.gate.condition(CONNECTION).state is CapabilityState.DEGRADED
            second.gate.restore(CONNECTION)
        assert second.gate.condition(CONNECTION).state is CapabilityState.READY
    finally:
        second.lifetime_lock.release()


@pytest.mark.parametrize("value", ["false", 1, None, [], {}])
def test_invalid_reachable_record_is_rejected_instead_of_hydrated_as_success(tmp_path, value):
    store = ConnectionFactStore(tmp_path)
    store.append(
        endpoint_address=ADDRESS,
        fact=TransportFact(False, 3, "timeout", "timeout"),
        source_session="session",
        observed_at=1.0,
    )
    record = json.loads(store.path.read_text(encoding="utf-8"))
    record["reachable"] = value
    store.path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load(ADDRESS)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "unknown"),
        ("elapsed_ms", True),
        ("elapsed_ms", -1),
        ("elapsed_ms", "3"),
        ("error_kind", "unknown"),
        ("detail", []),
        ("observed_at", float("nan")),
    ],
)
def test_corrupted_fact_metadata_is_never_silently_defaulted(tmp_path, field, value):
    store = ConnectionFactStore(tmp_path)
    store.append(
        endpoint_address=ADDRESS,
        fact=TransportFact(False, 3, "timeout", "timeout"),
        source_session="session",
        observed_at=1.0,
    )
    record = json.loads(store.path.read_text(encoding="utf-8"))
    record[field] = value
    store.path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load(ADDRESS)


@pytest.mark.parametrize("value", ["false", 1, {}, SimpleNamespace(reachable="false")])
def test_untyped_probe_result_cannot_report_connected_or_enable_a_dependency(value):
    calls = []
    api = LocalAPI(
        instance_id="strict-probe",
        connector=lambda: value,
        sleeper=lambda _: None,
        connection_persistence=SimpleNamespace(
            load=lambda: None, save=lambda state: calls.append(dict(state))
        ),
    )
    response = api.dispatch(
        Command(
            request_id="probe",
            action="test_connection",
            project_id="project",
            intent_id="probe-intent",
            expected_revision=0,
        ),
        Session("session", EntryKind.INTERACTIVE_CLI),
    )
    assert response.error is not None
    assert response.error.code == "CONNECTIVITY_FAILED"
    assert all(item["connected"] is False for item in calls)


def test_escaped_registered_credential_is_filtered_before_connection_diagnostic_write(tmp_path):
    from aitest.infrastructure.security import known_secrets

    secret = 'synthetic-connection-"credential"'
    known_secrets().register(secret)
    store = ConnectionFactStore(tmp_path)
    record = store.append(
        endpoint_address=ADDRESS,
        fact=TransportFact(False, 3, "timeout", "probe echoed " + secret),
        source_session="session",
        observed_at=1.0,
    )
    parsed = json.loads(store.path.read_text(encoding="utf-8"))
    assert secret not in parsed["detail"]
    assert secret not in record["detail"]
    assert store.load(ADDRESS).reachable is False


def test_other_endpoints_success_does_not_restore_the_configured_endpoints_failure(tmp_path):
    store = ConnectionFactStore(tmp_path)
    store.append(
        endpoint_address=ADDRESS,
        fact=TransportFact(False, 3, "timeout", "timeout"),
        source_session="session",
        observed_at=1.0,
    )
    other = EndpointConfig.from_address("https://other.example.com:443").base_address
    store.append(
        endpoint_address=other,
        fact=TransportFact(True, 3, None, ""),
        source_session="session",
        observed_at=2.0,
    )
    core = assemble_workspace_core(
        tmp_path, instance_id="connection-other", connection_endpoint=ADDRESS
    )
    try:
        assert core.gate.condition(CONNECTION).state is CapabilityState.DEGRADED
        assert core.gate.condition(CONNECTION).classification == "timeout"
    finally:
        core.lifetime_lock.release()
