"""Real stdio/client/core/files; source/environment authority uses explicit fixtures."""

import json
import subprocess
import sys

from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.bootstrap import acquire_endpoint, shutdown_endpoint
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_initial_run_registration import register
from tests.unit.test_mcp_stdio_read import encode, initialize, rpc


def test_actual_stdio_inspects_saved_whole_case_via_same_core_without_business_writes(
    authoritative,
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    source = register(core, prepared)
    target = register(core, prepared, request="mcp-target-register", intent="mcp-target-intent")
    root = core.workspace.root
    sequence = core.unit_of_work.current_commit_sequence()
    core.lifetime_lock.release()
    endpoint = acquire_endpoint(root)
    endpoint.connection.close()
    values = {
        "case_id": prepared["selected_case_ids"][0], "source_run_id": source.run_id,
        "target_run_id": target.run_id,
        "source_snapshot": SnapshotContentRef.of(source).model_dump(mode="json"),
        "target_snapshot": SnapshotContentRef.of(target).model_dump(mode="json"),
    }
    messages = [initialize(), {"jsonrpc": "2.0", "method": "notifications/initialized"},
                rpc(2, "tools/list"), rpc(3, "tools/call", {
                    "name": "aitest_inspect_case_reuse", "arguments": values,
                })]
    try:
        result = subprocess.run([
            sys.executable, "-m", "aitest.interfaces.tools.cli", "mcp-relay",
            "--workspace", str(root), "--project", inputs.project_id,
            "--binding", inputs.binding_id,
        ], input=b"\n".join(encode(message) for message in messages) + b"\n",
            capture_output=True, timeout=90)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert [item["id"] for item in responses] == [1, 2, 3]
        assert "aitest_inspect_case_reuse" in {
            tool["name"] for tool in responses[1]["result"]["tools"]
        }
        response = responses[-1]["result"]["structuredContent"]
        assert response["error"] is None, response["error"]
        assert response["instance_id"] == endpoint.instance_id
        assert response["workspace_id"] == endpoint.workspace_id
        assert response["project_id"] == inputs.project_id
        assert response["binding_revision"] == inputs.binding_revision
        preview = response["result"]
        assert preview["schema_version"] == "aitest.case-reuse-inspection/1.1"
        assert preview["status"] == "unverified"
        assert "source_attempts_missing" in preview["denial_reasons"]
        assert "basis_confirmed_unverified" in preview["denial_reasons"]
        assert not any(x.endswith("_changed") for x in preview["denial_reasons"])
        assert preview["source_snapshot"] == values["source_snapshot"]
        assert preview["target_snapshot"] == values["target_snapshot"]
        assert all(item["source_attempt_id"] is None for item in preview["step_mapping"])
        assert core.unit_of_work.current_commit_sequence() == sequence
    finally:
        shutdown_endpoint(root)
