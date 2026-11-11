"""Translate reviewed beginner forms into existing core commands, never execute them.

The wizard has no repository writer or approval authority. Every generated command
still passes the ordinary core guards, exact saved references and native approvals.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aitest.application.planning.draft import (
    load_template,
    template_draft_text,
    text_digest,
)
from aitest.application.planning.proposal import (
    materialize_plan_proposal,
    proposal_from_mapping,
    proposal_preview,
)
from aitest.application.planning.serialization import acceptance_scope_to_payload, case_to_payload
from aitest.application.project.serialization import binding_to_payload, environment_to_payload
from aitest.application.project.understanding import understanding_from_payload
from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseLayer,
    CaseLink,
)
from aitest.domain.planning.templates import TemplateRef
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
    #: 实际环境解释器版本，例如 3.11.9；不能写成 "3.11+"。
    python_version: str = Field(default="3.11.9", min_length=1, max_length=32)
    endpoint: str = "https://api.deepseek.com"
    # 供应商公告：deepseek-chat 与 deepseek-reasoner 自 2026-07-24 停止使用。
    model: str = "deepseek-flash"
    model_timeout: int = Field(default=60, ge=10, le=120)
    checks: list[CheckForm] = Field(default_factory=list, max_length=4)
    #: Structured AI proposal; when present, review/commit stages use it instead
    #: of the fixed four beginner checks.
    proposal: dict[str, Any] | None = None
    #: Persisted understanding returned by ``analyze_project``; the review
    #: helper re-verifies its digest/owner before using it for the preview.
    understanding: dict[str, Any] | None = None
    #: Preview-produced contract + exact understanding reference.  They are
    #: untrusted transport; plan publication revalidates both against records.
    coverage_contract: dict[str, Any] | None = None
    understanding_ref: dict[str, Any] | None = None
    #: 用户确认“业务功能目录完整、unknown 数已知”；同一计划确认冻结。
    catalog_confirmed: bool = False
    #: 项目级模型材料范围；源码片段默认关闭。
    allowed_material_kinds: list[str] = Field(
        default_factory=lambda: ["project_context", "template_content"]
    )
    source_snippets_enabled: bool = False
    #: 大型项目可按模块/子目录分范围；understanding 身份包含该 scope。
    source_scope: str = "."
    run_tier: Literal["quick", "on_demand", "full"] = "full"
    initial_driver: Literal["planned", "stepwise"] = "planned"


def _proposal_for(form: OnboardingForm) -> Any:
    if form.proposal is None:
        raise ValueError("结构化计划提案缺失")
    return proposal_from_mapping(form.proposal)


def _proposal_materials(
    form: OnboardingForm,
) -> tuple[Any, Any, list[Any], dict[str, Any], list[dict[str, Any]]]:
    """Validate the proposal and return domain objects plus their payloads."""

    proposal = _proposal_for(form)
    project = form.project_id
    scope, cases = materialize_plan_proposal(
        proposal,
        project_id=project,
        environment_id="env-" + project,
        scope_id="scope-" + project,
    )
    scope_payload = acceptance_scope_to_payload(scope, project_id=project)
    case_payloads = [case_to_payload(case, project_id=project) for case in cases]
    return proposal, scope, list(cases), scope_payload, case_payloads


def _proposal_plan_grant(
    form: OnboardingForm, *, project_id: str, plan_id: str
) -> dict[str, Any] | None:
    """v1.1 proposals carry an understanding revision, so one plan confirmation
    can also freeze the execution grant.  1.0 proposals keep the per-case
    ``confirm_basis`` path because they have no understanding revision to bind.
    """

    proposal = form.proposal
    if not isinstance(proposal, dict):
        return None
    understanding = proposal.get("understanding_revision")
    if (
        proposal.get("schema_version") != "aitest.plan-proposal/1.1"
        or type(understanding) is not int
        or understanding < 1
    ):
        return None
    return {
        # 默认执行解析器把未声明类别按 unknown（最严）处理；许可只覆盖这个真实类别。
        "allowed_action_classes": ["unknown"],
        # prepare 阶段写入的 execution_source.test_config_ref 使用同一身份。
        "credential_scope_ref": plan_id + ":1",
        "environment_id": "env-" + project_id,
        "risk_class": "medium",
        "understanding_revision": understanding,
    }


def _understanding_material(form: OnboardingForm) -> str:
    """Bounded text projection of the saved understanding for the plan prompt."""

    if form.understanding is None:
        return ""
    try:
        understanding = understanding_from_payload(
            form.understanding,
            project_id=form.project_id,
            workspace_id=form.workspace_id,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("项目理解材料无法核对，请重新分析项目") from error
    lines = [
        f"understanding_id: {understanding.understanding_id}",
        f"understanding_revision: {understanding.revision}",
    ]
    for function in understanding.catalog.functions:
        dimensions = ", ".join(function.required_dimensions)
        lines.append(f"function {function.function_id} ({function.name}): {dimensions}")
    for intent in understanding.test_intents:
        functions = ", ".join(sorted(intent.function_ids))
        lines.append(f"intent {intent.intent_id} ({intent.statement}): {functions}")
    return "\n".join(lines)


def wizard_review(form: OnboardingForm) -> dict[str, Any]:
    """Return the editable proposal preview; never writes a record."""

    proposal = _proposal_for(form)
    understanding = None
    if form.understanding is not None:
        try:
            understanding = understanding_from_payload(
                form.understanding,
                project_id=form.project_id,
                workspace_id=form.workspace_id,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError("项目理解材料无法核对，请重新分析项目") from error
    return proposal_preview(
        proposal,
        project_id=form.project_id,
        environment_id="env-" + form.project_id,
        scope_id="scope-" + form.project_id,
        understanding=understanding,
    )


_MATERIAL_KINDS = frozenset(
    {
        "project_context",
        "module_dependency_facts",
        "template_content",
        "acceptance_item",
        "case_content",
        "evidence_reference",
        "execution_fact",
        "source_snippet",
    }
)


def _material_kinds(form: OnboardingForm) -> list[str]:
    kinds = list(dict.fromkeys(form.allowed_material_kinds))
    if any(kind not in _MATERIAL_KINDS for kind in kinds):
        raise ValueError("材料范围包含未知类别")
    if form.source_snippets_enabled and "source_snippet" not in kinds:
        kinds.append("source_snippet")
    if not form.source_snippets_enabled:
        kinds = [kind for kind in kinds if kind != "source_snippet"]
    if not kinds:
        raise ValueError("至少保留一类模型材料")
    return kinds


def wizard_commands(
    form: OnboardingForm, stage: str, receipts: dict[str, Any],
    attempt: str | None = None, resume: bool = False,
    source_expected_revision: int = 0,
    revisions: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Build one bounded stage. References come from earlier successful receipts."""
    project = form.project_id
    binding_id, environment_id, plan_id = "binding-" + project, "env-" + project, "plan-" + project
    if (form.entry.startswith(("/", "\\")) or ".." in form.entry.replace("\\", "/").split("/")
            or ":" in form.entry or not form.entry.endswith(".py")):
        raise ValueError("请选择项目目录内的 Python 测试入口")
    scope = form.source_scope.strip() or "."
    if scope.startswith(("/", "\\")) or ".." in scope.replace("\\", "/").split("/") or ":" in scope:
        raise ValueError("测试范围必须是项目目录内的相对子目录")
    commands: list[dict[str, Any]] = []

    def add(
        key: str, action: str, parameters: dict[str, Any],
        target: str | None = None, *, intent_salt: str | None = None,
        expected_revision: int = 0,
    ) -> None:
        # Frozen form changes must not silently reuse an earlier persistent intent.
        # 每次用户重新生成计划都是一次新的模型生成意图；否则第一次结构校验
        # 失败后，同一意图只会重放旧的 blocked 结果。没有 salt 时保持历史
        # 摘要形状，续跑才能命中已保存的策略/项目意图。
        digest_input: list[Any] = [project, stage, key, parameters]
        if intent_salt is not None:
            digest_input.append(intent_salt)
        digest = hashlib.sha256(json.dumps(
            digest_input, ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()
        commands.append({"key": key, "action": action, "intent_id": "wizard-" + digest,
                         "expected_revision": expected_revision, "binding_revision": 1,
                         "target": target, "parameters": parameters})

    if stage == "project":
        # 继续已有项目时，项目/绑定/环境已经存在；再次以 expected_revision=0
        # 写入只会得到 B_REVISION_CONFLICT。续跑只需要重新核对并复用源码快照。
        if not resume:
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
            environment = EnvironmentRef(environment_id, 1, form.python_version,
                form.dependency_declaration, data_isolated=True,
                data_reset_policy=form.data_policy, step_timeout_seconds=120)
            add("environment", "save_environment", {"project_revision": 1, "expected_revision": 0,
                "environment": environment_to_payload(environment, project_id=project)}, environment_id)
        # 先固定源码并理解项目，复核页才能显示真实的业务功能覆盖，模型也能
        # 在生成计划时引用 understanding revision。分析失败在提交计划前暴露。
        add("snapshot", "analyze_project", {"binding_id": binding_id, "purpose": "prepare",
            "source_scope": scope, "selected_paths": [scope],
            "exclusion_rules": [".git", ".venv", "__pycache__"],
            "include_understanding": True},
            expected_revision=source_expected_revision if resume else 0,
            intent_salt=(f"resume-source-{source_expected_revision}" if resume else None))
    elif stage == "model":
        template_ref = TemplateRef("python-library", "1.0.0")
        template_text = template_draft_text(load_template(template_ref))
        add("policy", "save_model_outbound_policy", {"project_revision": 1, "policy": {
            "project_id": project, "revision": 1,
            "endpoint": {"provider": "openai-compatible", "address": form.endpoint,
                         "model_id": form.model, "purpose": "model"},
            "allowed_material_kinds": _material_kinds(form),
            "source_snippets_enabled": form.source_snippets_enabled,
            "ai_enabled": True, "confirmation": None, "revoked_material_kinds": [],
        }}, "model-policy:" + project)
        understanding_text = _understanding_material(form)
        project_context = form.name + "\n" + form.goal
        if understanding_text:
            project_context += (
                "\n\n项目理解（必须据此选择 goal_intent_ids 并生成 coverage_claims）：\n"
                + understanding_text
            )
        selected_material: dict[str, str] = {
            "project_context": project_context,
            "template_content": template_text,
        }
        source_snapshot = receipts.get("snapshot")
        if not isinstance(source_snapshot, dict):
            raise ValueError("请先完成源码分析，再生成 AI 计划")
        # 模型依据必须指向**已冻结快照**的精确身份；只给修订号会让核心
        # 判定 basis 无法核对并返回 model_basis_unverified。
        source_revision = source_snapshot.get("record_revision")
        source_snapshot_id = source_snapshot.get("snapshot_id")
        source_purpose = source_snapshot.get("purpose")
        source_content_identity = source_snapshot.get("content_identity")
        if (
            type(source_revision) is not int
            or source_revision < 1
            or not isinstance(source_snapshot_id, str)
            or not source_snapshot_id
            or source_purpose not in {"analysis", "prepare"}
            or not isinstance(source_content_identity, str)
            or not source_content_identity
        ):
            raise ValueError("源码分析依据不完整，请重新分析项目后再生成计划")
        source_ref = {
            "source_snapshot_id": source_snapshot_id,
            "purpose": source_purpose,
            "content_identity": source_content_identity,
            "record_revision": source_revision,
        }
        if form.source_snippets_enabled:
            try:
                root = Path(form.source).resolve()
                target = (root / form.entry).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    raise ValueError
                text = target.read_text(encoding="utf-8")
                if len(text.encode("utf-8")) > 65536:
                    raise ValueError
                selected_material["source_snippet"] = text
            except (OSError, UnicodeError, ValueError) as error:
                raise ValueError("选中的源码片段无法安全读取") from error
        add("suggestions", "generate_draft", {"generation_mode": "model",
            "project_revision": 1, "policy_revision": 1,
            "source_revision": source_revision, "source_ref": source_ref,
            "base_manual_revision": 0, "task_type": "case_suggestion",
            "draft_kind": "plan_proposal",
            "output_schema": (
                "aitest.plan-proposal/1.1" if understanding_text
                else "aitest.plan-proposal/1.0"
            ),
            "template_ref": {"template_id": template_ref.template_id,
                             "version": template_ref.version},
            "timeout_seconds": form.model_timeout,
            "selected_material": selected_material},
            intent_salt=attempt)
    elif stage == "commit":
        proposal, _scope, cases, scope_payload, case_payloads = _proposal_materials(form)
        current_revisions = revisions or {}
        # 重试发布时用例/范围可能已存在；重复以修订 1、expected_revision=0
        # 写入会得到 B_REVISION_CONFLICT，并让发布输入与已保存修订不一致。
        # 调用方给出当前仓储修订时，明确生成下一个修订。
        scope_current = current_revisions.get(str(scope_payload.get("scope_id", "")), 0)
        if type(scope_current) is not int or scope_current < 0:
            raise ValueError("当前验收范围修订无效")
        if scope_current:
            scope_payload = dict(scope_payload)
            scope_payload["revision"] = scope_current + 1
        for index, (case, payload) in enumerate(zip(cases, case_payloads, strict=True)):
            current = current_revisions.get(case.case_id, 0)
            if type(current) is not int or current < 0:
                raise ValueError("当前用例修订无效")
            if current:
                payload = dict(payload)
                payload["revision"] = current + 1
                case_payloads[index] = payload
            add("case-" + case.case_id, "save_case", {"case": payload},
                expected_revision=current)
        add("scope", "save_acceptance", {"acceptance_scope": scope_payload},
            expected_revision=scope_current)
        if form.run_tier == "full" and form.coverage_contract is None:
            raise ValueError(
                "完整验证需要项目理解与覆盖契约；请重新生成 v1.1 计划或选择按需自测"
            )
        if form.run_tier == "full" and not form.catalog_confirmed:
            raise ValueError("完整验证前请确认业务功能目录完整、unknown 数已知")
        plan_grant = _proposal_plan_grant(form, project_id=project, plan_id=plan_id)
        if plan_grant is None:
            for case in cases:
                add("confirm-" + case.case_id, "confirm_basis", {
                    "case_id": case.case_id,
                    "case_revision": case.revision,
                    "basis_revision": case.assertion_basis.revision,
                    "basis_text_digest": case.assertion_basis.text_digest,
                }, case.case_id)
        publish_parameters: dict[str, Any] = {
            "project_revision": 1, "expected_revision": 0,
            "plan_id": plan_id, "revision": 1,
            "scope": scope_payload, "cases": case_payloads,
            "template_versions": [{"template_id": proposal.template_id,
                                   "version": proposal.template_version}],
            "run_tier": form.run_tier, "initial_driver": form.initial_driver,
        }
        if plan_grant is not None:
            publish_parameters["plan_grant"] = plan_grant
        if form.coverage_contract is not None:
            publish_parameters["coverage_contract"] = form.coverage_contract
        if form.understanding_ref is not None:
            publish_parameters["understanding_ref"] = form.understanding_ref
        publish_parameters["catalog_confirmed"] = form.catalog_confirmed
        add("publish", "publish_plan", publish_parameters, plan_id)
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
        scope_payload = acceptance_scope_to_payload(AcceptanceScope("scope-" + project, 1,
            form.name, ids, ids, objective=form.goal), project_id=project)
        add("scope", "save_acceptance", {"acceptance_scope": scope_payload})
        add("publish", "publish_plan", {"project_revision": 1, "expected_revision": 0,
            "plan_id": plan_id, "revision": 1,
            "scope": scope_payload, "cases": cases,
            "template_versions": [{"template_id": "python-library", "version": "1.0.0"}],
            "run_tier": "on_demand", "initial_driver": "stepwise"}, plan_id)
    elif stage == "prepare":
        snapshot = receipts["snapshot"]
        if form.proposal is not None:
            _proposal, _scope, cases, _scope_payload, _case_payloads = _proposal_materials(form)
            if _proposal_plan_grant(form, project_id=project, plan_id=plan_id) is not None:
                published = receipts.get("publish")
                plan_basis = (
                    published.get("basis_confirmations")
                    if isinstance(published, dict) else None
                )
                if not isinstance(plan_basis, list) or not plan_basis or any(
                    not isinstance(item, dict) for item in plan_basis
                ):
                    raise ValueError("缺少计划确认派生的依据回执")
                confirmations = plan_basis
            else:
                confirmations = []
                for case in cases:
                    item = receipts.get("confirm-" + case.case_id)
                    if not isinstance(item, dict):
                        raise ValueError("缺少用例依据确认回执：" + case.case_id)
                    confirmations.append(item)
            selected = [case.case_id for case in cases]
        else:
            confirmations = [receipts["confirm-" + name] for name in
                             ("syntax", "normal", "boundary", "error")]
            selected = [project + "-" + name for name in
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
            "selected_case_ids": selected,
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
