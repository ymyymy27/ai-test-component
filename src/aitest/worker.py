"""One-shot run worker: executes cases, writes evidence and run state, then exits.

对外兼容门面：外部测试、脚本与 CLI 均继续从 ``aitest.worker`` 导入，并维持
``python -m aitest.worker`` 入口。
"""

from __future__ import annotations

import sys

from aitest.runners.command_runner import run_command
from aitest.worker_impl.deps_and_l1 import (
    L1Build,
    L1Entry,
    L1Result,
    _case_dependencies_ready,
    _dependency_suggestion,
    _environment_failure,
    _has_product_failure,
    _install_dependencies,
    _missing_command_tool,
    _missing_dependencies,
    _missing_module_from_output,
    _missing_modules_from_output,
    _resolve_dependency_package,
    _run_l1,
)
from aitest.worker_impl.env import (
    DEFAULT_REQUEST_ID_HEADER,
    DELIVERY_INCOMPLETE_MESSAGE,
    MAX_FILE_BYTES,
    SERVICES_FILE,
    EvidenceSettings,
    _dotenv_values,
    _environment_fingerprint,
    _project_config,
    _run_env,
    _venv_python,
)
from aitest.worker_impl.execution import (
    _execute,
    _mutates_seed_account,
    _repair_case_as_script,
    _repair_script_case,
    _run_python_script,
    _script_precondition_problem,
    _script_runtime_problem,
    _script_self_error,
    _write_validated_script,
)
from aitest.worker_impl.lifecycle import (
    _downgrade_script_failures,
    _postprocess,
    main,
)
from aitest.worker_impl.services import (
    _case_needs_services,
    _is_dev_server_command,
    _is_e2e_command,
    _service_block_reason,
    _services,
)

__all__ = [
    "DEFAULT_REQUEST_ID_HEADER",
    "DELIVERY_INCOMPLETE_MESSAGE",
    "EvidenceSettings",
    "L1Build",
    "L1Entry",
    "L1Result",
    "MAX_FILE_BYTES",
    "SERVICES_FILE",
    "_case_dependencies_ready",
    "_case_needs_services",
    "_dependency_suggestion",
    "_dotenv_values",
    "_downgrade_script_failures",
    "_environment_failure",
    "_environment_fingerprint",
    "_execute",
    "_has_product_failure",
    "_install_dependencies",
    "_is_dev_server_command",
    "_is_e2e_command",
    "_missing_command_tool",
    "_missing_dependencies",
    "_missing_module_from_output",
    "_missing_modules_from_output",
    "_mutates_seed_account",
    "_postprocess",
    "_project_config",
    "_repair_case_as_script",
    "_repair_script_case",
    "_resolve_dependency_package",
    "_run_env",
    "_run_l1",
    "_run_python_script",
    "_script_precondition_problem",
    "_script_runtime_problem",
    "_script_self_error",
    "_service_block_reason",
    "_services",
    "_venv_python",
    "_write_validated_script",
    "main",
    "run_command",
]

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
