"""Translate reviewed beginner forms into existing core commands, never execute them.

The wizard has no repository writer or approval authority. Every generated command
still passes the ordinary core guards, exact saved references and native approvals.
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aitest.application.planning.draft import text_digest
from aitest.application.planning.serialization import acceptance_scope_to_payload, case_to_payload
from aitest.application.project.serialization import binding_to_payload, environment_to_payload
from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseLayer,
    CaseLink,
)
from aitest.domain.project.context import BindingForm, EnvironmentRef, LocalProjectBinding


class CheckForm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    objective: str = Field(min_length=1, max_length=2000)
    inputs: str = Field(min_length=1, max_length=4000)
    expected: dict[str, Any] = Field(min_length=1, max_length=100)


class OnboardingForm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(pattern=r"^guided-[a-f0-9-]{32,36}$")
    workspace_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=4096)
    entry: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=8000)
    data_policy: str = Field(min_length=1, max_length=2000)
    dependency_declaration: str = Field(min_length=1, max_length=2000)
    endpoint: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    model_timeout: int = Field(default=60, ge=10, le=120)
    checks: list[CheckForm] = Field(default_factory=list, max_length=4)


def wizard_commands(
    form: OnboardingForm, stage: str, receipts: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build one bounded stage. References come from earlier successful receipts."""
    project = form.project_id
    binding_id, environment_id, plan_id = "binding-" + project, "env-" + project, "plan-" + project
    if (form.entry.startswith(("/", "\\")) or ".." in form.entry.replace("\\", "/").split("/")
            or ":" in form.entry or not form.entry.endswith(".py")):
        raise ValueError("请选择项目目录内的 Python 测试入口")
    commands: list[dict[str, Any]] = []

    def add(key: str, action: str, parameters: dict[str, Any], target: str | None = None) -> None:
        # Frozen form changes must not silently reuse an earlier persistent intent.
        digest = hashlib.sha256(json.dumps(
            [project, stage, key, parameters], ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()
        commands.append({"key": key, "action": action, "intent_id": "wizard-" + digest,
                         "expected_revision": 0, "binding_revision": 1,
                         "target": target, "parameters": parameters})

    if stage == "project":
        add("project", "save_context", {"project": {
            "schema_version": "aitest.project/2.0", "project_id": project,
            "local_project_id": project, "workspace_id": form.workspace_id,
            "name": form.name, "goal": form.goal, "revision": 1,
            "created_at_commit": "@core-assigned", "modules": [],
        }})
        binding = LocalProjectBinding(binding_id, 1, project, form.source, BindingForm.PLAIN,
                                      manifest_digest="pending-source-analysis", confirmed=True)
        add("binding", "save_binding", {"project_revision": 1, "expected_revision": 0,
            "binding": binding_to_payload(binding)}, binding_id)
        environment = EnvironmentRef(environment_id, 1, "Python 3.13",
            form.dependency_declaration, data_isolated=True,
            data_reset_policy=form.data_policy, step_timeout_seconds=120)
        add("environment", "save_environment", {"project_revision": 1, "expected_revision": 0,
            "environment": environment_to_payload(environment, project_id=project)}, environment_id)
    elif stage == "model":
        add("policy", "save_model_outbound_policy", {"project_revision": 1, "policy": {
            "project_id": project, "revision": 1,
            "endpoint": {"provider": "openai-compatible", "address": form.endpoint,
                         "model_id": form.model, "purpose": "model"},
            "allowed_material_kinds": ["project_context"], "source_snippets_enabled": False,
            "ai_enabled": True, "confirmation": None, "revoked_material_kinds": [],
        }}, "model-policy:" + project)
        add("suggestions", "generate_draft", {"generation_mode": "model",
            "project_revision": 1, "policy_revision": 1, "source_revision": 0,
            "base_manual_revision": 0, "task_type": "check_content_draft", "draft_kind": "case",
            "timeout_seconds": form.model_timeout,
            "selected_material": {"project_context": form.name + "\n" + form.goal}})
    elif stage == "plan":
        if len(form.checks) != 4:
            raise ValueError("请核对加载、正常输入、边界和异常输入四项检查的独立预期")
        cases = []
        for name, check in zip(("syntax", "normal", "boundary", "error"), form.checks, strict=True):
            identity = project + "-" + name
            basis = json.dumps({"schema_version": "aitest.pure-function-basis/1",
                                "expected_facts": check.expected}, ensure_ascii=False)
            case = Case(identity, 1, CaseLayer.L1 if name == "syntax" else CaseLayer.L2,
                check.objective, (form.data_policy,), (check.inputs,),
                ("运行已选择的测试入口：" + form.entry + "，并把本用例输入作为命令行参数传入",),
                "逐项比对已确认的独立预期：" + json.dumps(check.expected, ensure_ascii=False),
                "pure-function-stdout",
                CaseLink(frozenset({identity}), environment_ids=frozenset({environment_id}),
                         no_critical_path_reason=form.data_policy),
                AssertionBasis(1, AssertionBasisState.PRESENT_UNCONFIRMED,
                               basis, str(text_digest(basis))),
                independent_verification="从已保存输出独立比较冻结预期，不使用执行结果生成预期")
            payload = case_to_payload(case, project_id=project)
            cases.append(payload)
            add("case-" + name, "save_case", {"case": payload})
            add("confirm-" + name, "confirm_basis", {"case_id": identity, "case_revision": 1,
                "basis_revision": 1, "basis_text_digest": text_digest(basis)}, identity)
        ids = frozenset(str(case["case_id"]) for case in cases)
        scope = acceptance_scope_to_payload(AcceptanceScope("scope-" + project, 1,
            form.name, ids, ids, objective=form.goal), project_id=project)
        add("scope", "save_acceptance", {"acceptance_scope": scope})
        add("publish", "publish_plan", {"project_revision": 1, "expected_revision": 0,
            "plan_id": plan_id, "revision": 1,
            "scope": scope, "cases": cases,
            "template_versions": [{"template_id": "python-library", "version": "1.0.0"}],
            "run_tier": "full", "initial_driver": "stepwise"}, plan_id)
        add("snapshot", "analyze_project", {"binding_id": binding_id, "purpose": "prepare",
            "source_scope": ".", "selected_paths": ["."],
            "exclusion_rules": [".git", ".venv", "__pycache__"]})
    elif stage == "prepare":
        snapshot = receipts["snapshot"]
        confirmations = [receipts["confirm-" + name] for name in
                         ("syntax", "normal", "boundary", "error")]
        add("prepare", "prepare_run", {"mode": "from_saved", "project_revision": 1,
            "binding_id": binding_id, "binding_revision": 1, "plan_id": plan_id,
            "plan_revision": 1, "snapshot_id": snapshot["snapshot_id"], "snapshot_revision": 1,
            "environment_id": environment_id, "environment_revision": 1,
            "client_id": project, "prepare_request_id": project + "-prepare",
            "execution_source": {"registered_entry": "python-main", "entry_arguments": [form.entry],
                "cwd_mapping": "workdir:.", "allowed_env_keys": [], "secret_refs": [],
                "test_config_ref": plan_id + ":1", "adapter_versions": {"command": "command/1.0"},
                "resolved_input_digest": "@core-assigned"},
            "selected_case_ids": [project + "-" + name for name in
                                  ("syntax", "normal", "boundary", "error")],
            "confirmation_refs": [{key: item[key] for key in
                ("confirmation_id", "case_id", "basis_revision", "confirmed_at_commit")}
                                  for item in confirmations]})
    elif stage == "register":
        prepared = receipts["prepare"]["prepared_run_id"]
        add("register", "register_run",
            {"prepared_run_id": prepared, "record_revision": 1}, prepared)
    else:
        raise ValueError("未知向导阶段")
    return commands
