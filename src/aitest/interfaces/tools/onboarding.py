"""Bounded host setup/command preparation. No business-store writes or approvals."""

import json
import os
import re
import sys
import venv
from pathlib import Path
from typing import Any

from aitest.application.project.onboarding import OnboardingForm, wizard_commands
from aitest.infrastructure.credentials import SecretManager, WindowsCredentialProvider
from aitest.infrastructure.environment_configuration import carrier_configuration
from aitest.infrastructure.model_configuration import validate_model_configuration
from aitest.infrastructure.python_environment import RegisteredPythonEnvironmentResolver


def setup(data: dict[str, Any]) -> dict[str, Any]:
    root, source = Path(data["root"]), Path(data["source"])
    project = data["project_id"]
    if not isinstance(project, str) or not re.fullmatch(r"guided-[a-f0-9-]{32,36}", project):
        raise ValueError("项目身份无效")
    if not root.is_absolute() or not source.is_absolute() or root.name != project:
        raise ValueError("请选择独立的测试数据目录")
    RegisteredPythonEnvironmentResolver._no_links(root)
    RegisteredPythonEnvironmentResolver._no_links(source)
    if (root.resolve().is_relative_to(source.resolve())
            or source.resolve().is_relative_to(root.resolve())):
        raise ValueError("测试数据和被测源码目录必须分开")
    if sys.version_info[:2] != (3, 13):
        raise ValueError("请安装 Python 3.13 核心环境")
    environment = root / "python"
    marker = root / "carriers.json"
    if not marker.exists():
        if environment.exists():
            raise ValueError("环境准备中断；保留现场，请使用新的独立环境目录")
        root.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=False).create(environment)
        executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        dependencies = environment / ("Lib/site-packages" if os.name == "nt"
                                      else "lib/python3.13/site-packages")
        configuration = carrier_configuration(project_id=project, environment_id="env-" + project,
            executable=executable, base_executable=Path(sys._base_executable),
            dependency_roots=(dependencies,), venv_root=environment)
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(configuration, stream)
    workspace = root / "workspace"
    RegisteredPythonEnvironmentResolver._no_links(workspace)
    workspace.mkdir(exist_ok=True)
    return {"carriers_file": str(marker), "workspace": str(workspace),
            "environment_ready": True, "dependencies": "独立 Python 3.13 环境，仅标准库"}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("向导输入过大")
        data = json.loads(raw)
        operation = data["operation"]
        if operation == "setup":
            result = setup(data)
        elif operation == "commands":
            result = {"commands": wizard_commands(OnboardingForm.model_validate(data["form"]),
                data["stage"], data.get("receipts", {}))}
        elif operation == "credential":
            WindowsCredentialProvider().save(purpose="model", reference=data["reference"],
                                             value=data["value"])
            result = {"configured": True, "reference": data["reference"]}
        elif operation == "model":
            target = Path(data["root"]) / "model.json"
            if not target.is_absolute():
                raise ValueError("模型配置位置无效")
            RegisteredPythonEnvironmentResolver._no_links(target)
            config = {"schema_version": "aitest.model-provider/1", "endpoint": data["endpoint"],
                      "credential_reference": data["reference"]}
            validate_model_configuration(config)
            secret = SecretManager.default().resolve(data["reference"], purpose="model")
            secret.clear()
            if target.exists() and json.loads(target.read_text(encoding="utf-8")) != config:
                raise ValueError("已有配置不同，请在无活动执行的新项目中配置")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                with target.open("x", encoding="utf-8") as stream:
                    json.dump(config, stream)
            result = {"model_file": str(target), "configured": True}
        else:
            raise ValueError("未知向导操作")
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception:
        # Provider/OS errors can contain credential material; never echo them.
        print(json.dumps({"ok": False,
                          "message": "准备未完成，请核对目录、Python 3.13 和安全凭据配置。"},
                         ensure_ascii=False))
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    raise SystemExit(main())
