"""Bounded host setup/command preparation. No business-store writes or approvals."""

import json
import os
import re
import sys
import venv
from pathlib import Path
from typing import Any

from aitest.application.planning.proposal import PlanProposalError
from aitest.application.project.onboarding import (
    OnboardingForm,
    wizard_commands,
    wizard_review,
)
from aitest.bootstrap import (
    carrier_configuration,
    reject_path_links,
    save_purpose_credential,
    validate_model_configuration,
    verify_purpose_credential,
)


def _supported_python(version: tuple[int, int] | None = None) -> bool:
    """The tested-project environment supports Python 3.11 and newer."""
    return tuple(version or sys.version_info[:2]) >= (3, 11)


def setup(data: dict[str, Any]) -> dict[str, Any]:
    root, source = Path(data["root"]), Path(data["source"])
    project = data["project_id"]
    if not isinstance(project, str) or not re.fullmatch(r"guided-[a-f0-9-]{32,36}", project):
        raise ValueError("项目身份无效")
    if not root.is_absolute() or not source.is_absolute() or root.name != project:
        raise ValueError("请选择独立的测试数据目录")
    reject_path_links(root)
    reject_path_links(source)
    if (root.resolve().is_relative_to(source.resolve())
            or source.resolve().is_relative_to(root.resolve())):
        raise ValueError("测试数据和被测源码目录必须分开")
    if not _supported_python():
        raise ValueError("请安装 Python 3.11 或更高版本的核心环境")
    environment = root / "python"
    marker = root / "carriers.json"
    if not marker.exists():
        if environment.exists():
            raise ValueError("环境准备中断；保留现场，请使用新的独立环境目录")
        root.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=False).create(environment)
        executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        version_tag = f"{sys.version_info.major}.{sys.version_info.minor}"
        base_executable = Path(getattr(sys, "_base_executable", sys.executable))
        dependencies = environment / ("Lib/site-packages" if os.name == "nt"
                                      else f"lib/python{version_tag}/site-packages")
        configuration = carrier_configuration(project_id=project, environment_id="env-" + project,
            executable=executable, base_executable=base_executable,
            dependency_roots=(dependencies,), venv_root=environment)
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(configuration, stream)
    workspace = root / "workspace"
    reject_path_links(workspace)
    workspace.mkdir(exist_ok=True)
    runtime = f"Python {sys.version_info.major}.{sys.version_info.minor}"
    return {
        "carriers_file": str(marker),
        "workspace": str(workspace),
        "environment_ready": True,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "dependencies": f"独立 {runtime} 环境，仅标准库",
        # 登记文件只是材料：必须由宿主把环境变量指向它并受控重启核心，
        # 否则环境相关动作（如 prepare_run）会被门禁拒绝且看不出原因。
        "env_var": "AITEST_PYTHON_CARRIERS",
        "env_value": str(marker),
        "restart_hint": (
            "把 AITEST_PYTHON_CARRIERS 设为上面的 env_value，然后受控重启核心，"
            "环境依赖动作才会生效"
        ),
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("向导输入过大")
        data = json.loads(raw)
        operation = data["operation"]
        if operation == "setup":
            result = setup(data)
        elif operation == "review":
            form = OnboardingForm.model_validate(data["form"])
            try:
                result = {"valid": True, "preview": wizard_review(form)}
            except PlanProposalError as error:
                result = {
                    "valid": False,
                    "issues": [issue.as_dict() for issue in error.issues],
                }
        elif operation == "commands":
            attempt = data.get("attempt")
            if attempt is not None and (not isinstance(attempt, str) or not attempt or len(attempt) > 128):
                raise ValueError("向导重试标记无效")
            resume = data.get("resume", False)
            if type(resume) is not bool:
                raise ValueError("向导续跑标记无效")
            source_expected_revision = data.get("source_expected_revision", 0)
            if type(source_expected_revision) is not int or source_expected_revision < 0:
                raise ValueError("源码当前指针修订无效")
            raw_revisions = data.get("revisions", {})
            if not isinstance(raw_revisions, dict):
                raise ValueError("当前记录修订映射无效")
            revisions: dict[str, int] = {}
            for key, value in raw_revisions.items():
                if (not isinstance(key, str) or not key
                        or type(value) is not int or value < 0):
                    raise ValueError("当前记录修订映射无效")
                revisions[key] = value
            result = {"commands": wizard_commands(
                OnboardingForm.model_validate(data["form"]),
                data["stage"], data.get("receipts", {}), attempt, resume,
                source_expected_revision, revisions,
            )}
        elif operation == "credential":
            save_purpose_credential(purpose="model", reference=data["reference"],
                                    value=data["value"])
            result = {"configured": True, "reference": data["reference"]}
        elif operation == "model":
            target = Path(data["root"]) / "model.json"
            if not target.is_absolute():
                raise ValueError("模型配置位置无效")
            reject_path_links(target)
            config = {"schema_version": "aitest.model-provider/1", "endpoint": data["endpoint"],
                      "credential_reference": data["reference"]}
            validate_model_configuration(config)
            verify_purpose_credential(purpose="model", reference=data["reference"])
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
    except PlanProposalError as error:
        # Structured proposal errors are safe to return: they name fields, not credentials.
        print(json.dumps({"ok": False, "code": error.code, "message": str(error),
                          "issues": [issue.as_dict() for issue in error.issues]},
                         ensure_ascii=False))
        return 2
    except Exception:
        # Provider/OS errors can contain credential material; never echo them.
        print(json.dumps({"ok": False,
                          "message": "准备未完成，请核对目录、Python 3.11+ 和安全凭据配置。"},
                         ensure_ascii=False))
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    raise SystemExit(main())
