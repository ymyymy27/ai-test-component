"""Real local carrier configuration; no simulated human/host acceptance."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.environment_configuration import (
    MAX_CONFIG_BYTES,
    HostConfiguredEnvironmentResolver,
    carrier_configuration,
)
from tests.unit.test_python_environment import carrier as carrier
from tests.unit.test_python_environment import request


def configuration(carrier):
    return carrier_configuration(
        project_id=carrier.project_id,
        environment_id=carrier.environment_id,
        executable=carrier.executable,
        base_executable=carrier.base_executable,
        dependency_roots=carrier.dependency_roots,
        venv_root=carrier.venv_root,
    )


def install(monkeypatch, tmp_path, data):
    path = tmp_path / "carriers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("AITEST_PYTHON_CARRIERS", str(path))
    return path


def test_default_core_resolves_explicit_real_carrier_and_freezes_configuration(
    carrier, monkeypatch, tmp_path
):
    path = install(monkeypatch, tmp_path, configuration(carrier))
    core = assemble_workspace_core(tmp_path / "workspace", instance_id="configured-environment")
    try:
        resolver = core.environment_resolution.resolver
        first = resolver.resolve(request())
        assert first.resolution.executable_path == str(carrier.executable.resolve())
        path.write_text("{}", encoding="utf-8")
        assert resolver.resolve(request()) == first
        with pytest.raises(ValueError, match="配置无效"):
            HostConfiguredEnvironmentResolver().resolve(request())
    finally:
        core.lifetime_lock.release()


@pytest.mark.parametrize(
    "invalid",
    ["{}", '{"carriers":[],"carriers":[]}', "x" * (MAX_CONFIG_BYTES + 1)],
    ids=["missing-fields", "duplicate-keys", "oversize"],
)
def test_invalid_config_does_not_prevent_core_assembly(monkeypatch, tmp_path, invalid):
    path = tmp_path / "invalid.json"
    path.write_text(invalid, encoding="utf-8")
    monkeypatch.setenv("AITEST_PYTHON_CARRIERS", str(path))
    core = assemble_workspace_core(tmp_path / "workspace", instance_id="invalid-environment")
    try:
        with pytest.raises(ValueError, match="配置无效"):
            core.environment_resolution.resolver.resolve(request())
        assert core.api.workspace_id
    finally:
        core.lifetime_lock.release()


def test_registration_does_not_execute_candidate_and_hash_drift_blocks_probe(
    carrier, monkeypatch, tmp_path
):
    from aitest.infrastructure.python_environment import RegisteredPythonEnvironmentResolver

    monkeypatch.setattr(
        RegisteredPythonEnvironmentResolver,
        "_probe",
        lambda *_: pytest.fail("unexpected execution"),
    )
    data = configuration(carrier)
    data["carriers"][0]["expected_executable_digest"] = "sha256:" + "0" * 64
    install(monkeypatch, tmp_path, data)
    with pytest.raises(ValueError, match="content changed"):
        HostConfiguredEnvironmentResolver().resolve(request())


@pytest.mark.parametrize("mutation", ["duplicate", "extra", "relative", "bad-type"])
def test_invalid_registration_is_not_silently_accepted(carrier, monkeypatch, tmp_path, mutation):
    data = configuration(carrier)
    row = data["carriers"][0]
    if mutation == "duplicate":
        data["carriers"].append(dict(row))
    elif mutation == "extra":
        row["user_confirmed"] = True
    elif mutation == "relative":
        row["executable"] = "python.exe"
    else:
        row["dependency_roots"] = [True]
    install(monkeypatch, tmp_path, data)
    with pytest.raises(ValueError, match="配置无效"):
        HostConfiguredEnvironmentResolver().resolve(request())


def test_cli_creates_real_registration_without_business_write(carrier, tmp_path):
    command = [
        sys.executable,
        "-m",
        "aitest.interfaces.tools.cli",
        "python-carrier",
        "--project",
        "project",
        "--environment",
        "environment",
        "--python",
        str(carrier.executable),
        "--base-python",
        str(carrier.base_executable),
        "--venv-root",
        str(carrier.venv_root),
        "--dependencies",
        str(carrier.dependency_roots[0]),
    ]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    result = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)["carriers"][0]
    assert row["expected_executable_digest"] == carrier.expected_executable_digest
    assert not (tmp_path / "workspace.json").exists()
    output = tmp_path / "configuration.json"
    command += ["--output", str(output)]
    saved = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert saved.returncode == 0, saved.stderr
    original = output.read_bytes()
    assert json.loads(original)["carriers"][0]["environment_id"] == "environment"
    refused = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert refused.returncode == 2
    assert output.read_bytes() == original


def test_configured_real_carrier_reaches_saved_prepared_run(carrier, monkeypatch, tmp_path):
    # Saved business material and approval gestures are fixtures; interpreter
    # observation and immutable preparation persistence use production code.
    from dataclasses import replace

    from tests.support.prepared_run_factory import build_scenario
    from tests.unit.test_authoritative_preparation import _authoritative, prepare

    project_id = build_scenario("plain").project.project_id
    data = configuration(replace(carrier, project_id=project_id, environment_id="env-local"))
    install(monkeypatch, tmp_path, data)
    with _authoritative(
        tmp_path, environment_resolver=HostConfiguredEnvironmentResolver()
    ) as value:
        core, inputs, _ = value
        response = prepare(core, inputs)
        assert response.error is None, response.error
        assert response.result["status"] == "prepared", response.result
        resolution = response.result["environment"]["resolution"]
        assert resolution["executable_path"] == str(carrier.executable.resolve())
        saved = core.unit_of_work.repo.read(
            aggregate_kind="prepared_run", record_id=response.result["prepared_run_id"], revision=1
        )
        assert saved.payload["environment"]["resolution"] == resolution
