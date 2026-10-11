"""The confirmed declaration must be exactly the displayed canonical input."""

from copy import deepcopy

import pytest

from aitest.application.controlled_write import controlled_write
from aitest.application.project.serialization import environment_to_payload
from aitest.domain.approvals import ApprovalRequired
from aitest.domain.project.context import EnvironmentRef, IsolationMode, SecretRef


def declaration():
    return environment_to_payload(
        EnvironmentRef(
            "env",
            1,
            "Python 3.13",
            "declared dependencies",
            isolation_mode=IsolationMode.NONE,
            isolation_confirmed=True,
            data_isolated=False,
            data_reset_policy="separate disposable data",
            target_deployment_identity="local-test-service-v1",
            network_targets=("local-test-service",),
            secret_refs=(SecretRef("http", "API_TOKEN", "test-service-token"),),
            request_timeout_seconds=15,
            step_timeout_seconds=30,
        ),
        project_id="project",
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("project_id", "other"),
        ("revision", True),
        ("revision", 2),
        ("isolation_confirmed", "true"),
        ("isolation_confirmed", False),
        ("data_isolated", "false"),
        ("step_timeout_seconds", True),
        ("unknown", "additional input"),
        ("isolation_mode", "unknown"),
    ],
)
def test_confirmed_environment_rejects_ambiguous_body(key, value):
    body = deepcopy(declaration())
    body[key] = value
    with pytest.raises(ApprovalRequired):
        controlled_write(
            "save_environment",
            "project",
            {
                "project_revision": 1,
                "expected_revision": 0,
                "environment": body,
            },
        )


def test_environment_confirmation_keeps_declared_data_target_and_reference_scope():
    body = declaration()
    write = controlled_write(
        "save_environment",
        "project",
        {
            "project_revision": 1,
            "expected_revision": 0,
            "environment": body,
        },
    )
    assert write.payload == body and write.parameters["environment"] == body
    assert write.payload["data_isolated"] is False
    assert write.payload["target_deployment_identity"] == "local-test-service-v1"
    assert write.payload["secret_refs"] == [
        {"purpose": "http", "env_key": "API_TOKEN", "ref": "test-service-token"}
    ]
    # These are declarations and credential references, not verified actual environment facts.
    assert "interpreter_identity" not in write.payload
    assert "dependency_set_digest" not in write.payload
