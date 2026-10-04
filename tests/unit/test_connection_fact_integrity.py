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
    store.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
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
    store.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
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


@pytest.mark.parametrize("change", ["duplicate", "missing", "unterminated"])
def test_partial_or_ambiguous_connection_fact_is_not_recoverable_as_ready(tmp_path, change):
    store = ConnectionFactStore(tmp_path)
    store.append(
        endpoint_address=ADDRESS,
        fact=TransportFact(True, 3, None, ""),
        source_session="session",
        observed_at=1.0,
    )
    raw = store.path.read_text(encoding="utf-8")
    if change == "duplicate":
        raw = raw.replace('"reachable": true', '"reachable": false, "reachable": true')
    elif change == "missing":
        record = json.loads(raw)
        del record["elapsed_ms"]
        raw = json.dumps(record) + "\n"
    else:
        raw = raw.rstrip("\n")
    store.path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        store.load(ADDRESS)


@pytest.mark.parametrize(
    "bad_fact",
    [
        TransportFact("false", 3, None, ""),
        TransportFact(False, True, "timeout", ""),
        TransportFact(True, 3, "timeout", ""),
    ],
)
def test_bad_new_fact_is_rejected_before_diagnostic_directory_is_created(tmp_path, bad_fact):
    store = ConnectionFactStore(tmp_path)
    with pytest.raises(ValueError):
        store.append(
            endpoint_address=ADDRESS, fact=bad_fact, source_session="session", observed_at=1.0
        )
    assert not store.path.exists() and not store.path.parent.exists()


@pytest.mark.parametrize(
    "metadata", [dict(elapsed_ms=True), dict(elapsed_ms=-1), dict(error_kind="timeout")]
)
def test_connected_label_with_contradictory_metadata_cannot_report_a_success(metadata):
    fact = SimpleNamespace(reachable=True, elapsed_ms=1, error_kind=None)
    for name, value in metadata.items():
        setattr(fact, name, value)
    api = LocalAPI(instance_id="metadata", connector=lambda: fact, sleeper=lambda _: None)
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
    assert response.error.code == "CONNECTIVITY_FAILED"


def test_connection_persistence_failure_is_a_safe_response_and_blocks_its_dependent_action():
    from aitest.infrastructure.capabilities import CapabilityGate
    from aitest.infrastructure.security import guard_value, known_secrets

    secret = "synthetic-connection-save-secret"
    known_secrets().register(secret)
    gate = CapabilityGate(action_dependencies={"dependent": frozenset({CONNECTION})})
    gate.configure(CONNECTION)

    def fail_save(state):
        raise OSError("diagnostic save failed: " + secret)

    api = LocalAPI(
        instance_id="save-fault",
        connector=lambda: True,
        capability_gate=gate,
        credential_projector=lambda value: guard_value(value)[0],
        connection_persistence=SimpleNamespace(load=lambda: None, save=fail_save),
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
    assert response.error.code == "INTERNAL_ERROR"
    assert secret not in response.error.message
    assert not gate.check("dependent").allowed
    assert gate.condition(CONNECTION).classification == "storage"


def test_valid_probe_fact_preserves_compatibility_with_controlled_boolean_connector():
    for connector in (lambda: True, lambda: TransportFact(True, 3, None, "")):
        api = LocalAPI(instance_id="valid-probe", connector=connector)
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
        assert response.error is None and response.result["connected"] is True


def test_default_core_handler_error_cannot_echo_a_registered_credential(tmp_path):
    from aitest.infrastructure.security import known_secrets

    secret = "synthetic-default-error-credential"
    known_secrets().register(secret)

    def fail(command):
        raise OSError("operation failed: " + secret)

    core = assemble_workspace_core(
        tmp_path, instance_id="error-boundary", extra_handlers={"raise_error": fail}
    )
    try:
        response = core.api.dispatch(
            Command(
                request_id="failure",
                action="raise_error",
                project_id="project",
                intent_id="failure-intent",
                expected_revision=0,
            ),
            Session("session", EntryKind.INTERACTIVE_CLI),
        )
        assert response.error.code == "INTERNAL_ERROR"
        assert secret not in response.model_dump_json()
    finally:
        core.lifetime_lock.release()


def test_failed_error_projection_never_falls_back_to_unfiltered_exception_text():
    def fail_projection(value):
        raise OSError("projector unavailable")

    def fail(command):
        raise OSError("sensitive original detail")

    api = LocalAPI(
        instance_id="projector-fault",
        handlers={"raise_error": fail},
        credential_projector=fail_projection,
    )
    response = api.dispatch(
        Command(
            request_id="failure",
            action="raise_error",
            project_id="project",
            intent_id="failure-intent",
            expected_revision=0,
        ),
        Session("session", EntryKind.INTERACTIVE_CLI),
    )
    assert response.error.code == "INTERNAL_ERROR"
    assert "sensitive original detail" not in response.error.message


def test_unverifiable_connection_fact_only_blocks_actions_depending_on_connection(tmp_path):
    first = assemble_workspace_core(tmp_path, instance_id="before-corruption")
    first.lifetime_lock.release()
    store = ConnectionFactStore(tmp_path)
    store.append(
        endpoint_address=ADDRESS,
        fact=TransportFact(True, 3, None, ""),
        source_session="session",
        observed_at=1.0,
    )
    record = json.loads(store.path.read_text(encoding="utf-8"))
    record["reachable"] = "false"
    store.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    original = store.path.read_bytes()
    core = assemble_workspace_core(
        tmp_path,
        instance_id="after-corruption",
        connection_endpoint=ADDRESS,
        extra_handlers={
            "local_only": lambda command: {"local": True},
            "connection_only": lambda command: {"network": True},
        },
        extra_action_dependencies={"connection_only": (CONNECTION,)},
    )
    try:
        command = Command(
            request_id="local",
            action="local_only",
            project_id="project",
            intent_id="local-intent",
            expected_revision=0,
        )
        assert core.api.dispatch(command, Session("session", EntryKind.INTERACTIVE_CLI)).result == {
            "local": True
        }
        dependent = command.model_copy(
            update={
                "request_id": "network",
                "action": "connection_only",
                "intent_id": "network-intent",
            }
        )
        assert (
            core.api.dispatch(dependent, Session("session", EntryKind.INTERACTIVE_CLI)).error.code
            == "CAPABILITY_DEGRADED"
        )
        assert core.gate.condition(CONNECTION).classification == "storage"
        assert store.path.read_bytes() == original
    finally:
        core.lifetime_lock.release()


def test_failed_credential_guard_cannot_fall_back_to_a_success_payload():
    def fail_projection(value):
        raise OSError("known credential guard unavailable")

    api = LocalAPI(
        instance_id="guard-fault",
        handlers={"payload": lambda command: {"body": "sensitive original payload"}},
        credential_projector=fail_projection,
    )
    response = api.dispatch(
        Command(
            request_id="failure",
            action="payload",
            project_id="project",
            intent_id="failure-intent",
            expected_revision=0,
        ),
        Session("session", EntryKind.INTERACTIVE_CLI),
    )
    assert response.result is None
    assert response.error.code == "INTERNAL_ERROR"
    assert "sensitive original payload" not in response.model_dump_json()
