"""规则动作：导出/导入/保存草稿（JSON 与 Markdown）。

原先分散在 `usecase_registry.py` 的模块级 helper 与 build 闭包里；拆出后由
registry 以 `rules.handle_xxx(deps, command)` 绑定。`publish_rules` 仍留在
registry（它依赖受控发布闭包）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from aitest.application.dispatch.actions.common import _gaps_of, _publication_result
from aitest.application.dispatch.actions.models import controlled_publication
from aitest.application.dispatch.errors import BUseCaseError
from aitest.application.dispatch.params import (
    _as_mapping,
    _as_text,
    _bool_of,
    _command_expected_revision,
    _command_parameters,
    _command_project_id,
    _enum_of,
    _expected_revision_or_none,
    _required,
    _revision_of,
    _text_list,
)
from aitest.application.planning.persistence import save_rule_draft
from aitest.application.planning.portable import (
    RULE_PORTABLE_FIELDS,
    export_rule_payloads,
    import_rule_payloads,
)
from aitest.application.planning.publish import payload_digest, publish_rules
from aitest.application.planning.rules_markdown import (
    rule_draft_from_markdown,
    rule_markdown_from_payload,
)
from aitest.application.planning.substrate import read_scoped_record
from aitest.domain.planning.rules import RuleDraft, RuleEnablement, RuleVersion

if TYPE_CHECKING:
    from aitest.application.usecase_registry import BUseCaseDependencies

def _rule_versions_for(
    deps: BUseCaseDependencies,
    *,
    project_id: str,
    parameters: Mapping[str, object],
) -> tuple[RuleVersion, ...]:
    """按 `{rule_id, revision}` 读回**已发布**的规则版本。

    摘要取自记录本身；记录不存在时**拒绝**，不填占位值——用占位值填出来的
    计划会在 `PreparedRun.rule_versions` 里带一个假身份，事后无法核对。
    """
    raw = parameters.get("rule_revisions")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BUseCaseError("B_INVALID_PARAMETER", "rule_revisions must be a list")
    versions: list[RuleVersion] = []
    for index, item in enumerate(raw):
        entry = _as_mapping(item, f"rule_revisions[{index}]")
        rule_id = _as_text(_required(entry, "rule_id"), f"rule_revisions[{index}].rule_id")
        revision = _revision_of(_required(entry, "revision"), f"rule_revisions[{index}].revision")
        try:
            record = read_scoped_record(
                deps.reader,
                project_id=project_id,
                aggregate_kind="rule_version",
                record_id=rule_id,
                revision=revision,
            )
        except Exception as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER",
                f"no published rule version for {rule_id}@{revision}",
            ) from error
        if record.payload.get("project_id") != project_id:
            raise BUseCaseError("B_INVALID_PARAMETER", "rule version belongs to another project")
        if record.payload.get("status") != "published":
            raise BUseCaseError("B_INVALID_PARAMETER", "rule version has not been published")
        versions.append(_rule_version_from_payload(record.payload, record_revision=record.revision))
    return tuple(versions)

def _rule_version_from_payload(
    payload: Mapping[str, object], *, record_revision: int | None = None
) -> RuleVersion:
    """按 `publish_rules()` 的落盘形状重建规则版本。

    两处**如实说明**（记录里没有这些字段，不假装有）：

    - `digest`：发布时算出来放在内存对象上，**没有落进 payload**。这里用发布器
      公开的同一口径（`publish.payload_digest`）对记录内容重算，因此两者必须一致；
      若将来发布器换了口径，这条重建路径会跟着一致，不会各算一套。
    - `confirmation_id`：取**本次提交的提交序号**，记录里没有该字段。重建时用记录自带的
      提交序号占位（`created_at_commit` 同源语义），**它不参与计划冻结**，
      只用于满足领域对象的不变量。

    缺字段即拒绝，不填默认值（读不懂的记录不得被当成合法版本）。
    """
    digest = payload_digest(dict(payload))
    return RuleVersion(
        rule_id=_as_text(payload.get("rule_id"), "rule_id"),
        revision=_revision_of(payload.get("revision"), "revision"),
        scope=_as_text(payload.get("scope"), "scope"),
        text=_as_text(payload.get("text"), "text"),
        steps=_text_list(payload.get("steps"), "steps"),
        evidence_requirements=_text_list(
            payload.get("evidence_requirements"), "evidence_requirements"
        ),
        source=_as_text(payload.get("source"), "source"),
        confirmation_id=digest,
        digest=digest,
        record_revision=record_revision,
    )

def _rule_draft_of(parameters: Mapping[str, object]) -> RuleDraft:
    """把参数翻成 `RuleDraft`；字段名与领域对象**逐字一致**。"""
    enablement = (
        RuleEnablement.DISABLED
        if parameters.get("enablement") is None
        else _enum_of(RuleEnablement, parameters["enablement"], "enablement")
    )
    return RuleDraft(
        rule_id=_as_text(_required(parameters, "rule_id"), "rule_id"),
        revision=_revision_of(_required(parameters, "revision"), "revision"),
        scope=_as_text(_required(parameters, "scope"), "scope"),
        text=_as_text(_required(parameters, "text"), "text"),
        source=_as_text(_required(parameters, "source"), "source"),
        steps=_text_list(parameters.get("steps"), "steps"),
        evidence_requirements=_text_list(
            parameters.get("evidence_requirements"), "evidence_requirements"
        ),
        enablement=enablement,
        confirmed=_bool_of(parameters.get("confirmed"), "confirmed"),
        unknown_extension_fields=_text_list(
            parameters.get("unknown_extension_fields"), "unknown_extension_fields"
        ),
    )

def handle_export_rules(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    _command_project_id(command)
    parameters = _command_parameters(command)
    raw = parameters.get("rule_versions")
    if raw is None:
        raise BUseCaseError("B_INVALID_PARAMETER", "rule_versions must be a non-empty list")
    versions = _rule_versions_for(
        deps,
        project_id=_command_project_id(command),
        parameters={"rule_revisions": raw},
    )
    # 导出的是**内容**：导入方拿到草稿，"已发布"由各自实例重新发布产生。
    return {"bundle": export_rule_payloads(versions)}

def handle_save_rules(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    """保存规则**草稿**（需求 P1-FR05："未发布不生效"）。

    与 `import_rules` 共用同一个 `save_rule_draft` 落盘路径与同一份严格字段解析；
    区别只在入口形态：这里是**单条**草稿，由受控用户编辑后保存。保存不发布——
    `rule_version` 只由 `publish_rules` 产生，草稿默认未确认、未启用。
    """
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    draft = _rule_draft_of(_as_mapping(_required(parameters, "draft"), "draft"))
    staged = save_rule_draft(
        draft,
        project_id=project_id,
        unit_of_work=deps.unit_of_work,
        expected_revision=_command_expected_revision(command),
    )
    return {
        "saved": {
            "aggregate_kind": staged.aggregate_kind,
            "record_id": staged.record_id,
            "revision": staged.revision,
            "rule_id": draft.rule_id,
            "confirmed": draft.confirmed,
            "enablement": draft.enablement.value,
        },
        "published": False,
    }

def handle_import_rules(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    """导入规则：**只得到草稿**，并作为 `rule_draft` 记录落盘。

    导入不是发布：产出的草稿恒为未确认、未启用；`expected_revision` 与其他写动作同样
    由命令给出（`0` 表示新建），因此"同一 `rule_id` 再导入一次"会按正常修订冲突处理，
    要追加修订就显式给出已看到的修订。
    """
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    raw = _required(parameters, "bundle")
    bundle = raw if isinstance(raw, str) else _as_mapping(raw, "bundle")
    if isinstance(bundle, str):
        try:
            decoded: object = json.loads(bundle)
        except json.JSONDecodeError as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER", f"bundle is not valid JSON: {error}"
            ) from error
    else:
        decoded = dict(bundle)
    try:
        drafts = import_rule_payloads(decoded)
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", f"invalid bundle: {error}") from error
    if not drafts:
        raise BUseCaseError("B_INVALID_PARAMETER", "bundle carries no rules")

    stored: list[dict[str, object]] = []
    for draft in drafts:
        staged = save_rule_draft(
            draft,
            project_id=project_id,
            unit_of_work=deps.unit_of_work,
            expected_revision=_command_expected_revision(command),
        )
        stored.append(
            {
                "aggregate_kind": staged.aggregate_kind,
                "record_id": staged.record_id,
                "revision": staged.revision,
                "rule_id": draft.rule_id,
                "confirmed": draft.confirmed,
                "enablement": draft.enablement.value,
            }
        )
    return {"imported": stored}

def handle_export_rules_markdown(
    deps: BUseCaseDependencies, command: object
) -> Mapping[str, object]:
    """导出规则为 **Markdown**（需求 P1-FR05 的"Markdown 导入导出"）。

    与 `export_rules` 的区别只在**渲染形态**：内容仍是同一条 `rule_version` 的内容，
    字段集合与规范 JSON 完全一致（见 `rules_markdown.py` 的方言定义）。
    可以一次导出多条：返回 `documents`，每项一条规则、各自可独立导入。
    """
    _command_project_id(command)
    parameters = _command_parameters(command)
    raw = parameters.get("rule_versions")
    if raw is None:
        raise BUseCaseError("B_INVALID_PARAMETER", "rule_versions must be a non-empty list")
    versions = _rule_versions_for(
        deps,
        project_id=_command_project_id(command),
        parameters={"rule_revisions": raw},
    )
    bundle = export_rule_payloads(versions)
    rules = bundle["rules"]
    assert isinstance(rules, list)
    # 只把**可携带字段**交给 Markdown 渲染器：`export_rule_version()` 另带
    # `published_confirmation_id` / `published_digest` 这类**本地追溯**字段，
    # 它们不属于可携带格式（Markdown 是给人编辑的，不能携带"某实例已发布"的宣称）。
    documents: list[dict[str, object]] = []
    for payload in rules:
        portable = {key: payload[key] for key in RULE_PORTABLE_FIELDS}
        try:
            markdown = rule_markdown_from_payload(portable)
        except ValueError as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER", f"rule payload cannot be rendered: {error}"
            ) from error
        documents.append(
            {
                "rule_id": payload["rule_id"],
                "revision": payload["revision"],
                "markdown": markdown,
            }
        )
    return {"documents": documents}

def handle_import_rules_markdown(
    deps: BUseCaseDependencies, command: object
) -> Mapping[str, object]:
    """导入规则 **Markdown**：与 `import_rules` 同一口径——**只得到草稿**、并落 `rule_draft`。

    参数 `markdown` 可以是单份文档（字符串）或多份（列表）；每份解析失败都指名报错，
    不"跳过坏的那份继续导入"（那会让调用方以为全部都进来了）。
    """
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    raw = _required(parameters, "markdown")
    if isinstance(raw, str):
        sources = [raw]
    elif isinstance(raw, list):
        if not raw:
            raise BUseCaseError("B_INVALID_PARAMETER", "markdown must be a non-empty list")
        sources = [_as_text(item, f"markdown[{index}]") for index, item in enumerate(raw)]
    else:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", "markdown must be a string or a list of strings"
        )

    stored: list[dict[str, object]] = []
    for index, document in enumerate(sources):
        try:
            draft = rule_draft_from_markdown(document)
        except ValueError as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER", f"markdown[{index}] is not a rule document: {error}"
            ) from error
        staged = save_rule_draft(
            draft,
            project_id=project_id,
            unit_of_work=deps.unit_of_work,
            expected_revision=_command_expected_revision(command),
        )
        stored.append(
            {
                "aggregate_kind": staged.aggregate_kind,
                "record_id": staged.record_id,
                "revision": staged.revision,
                "rule_id": draft.rule_id,
                "confirmed": draft.confirmed,
                "enablement": draft.enablement.value,
            }
        )
    return {"imported": stored}


def handle_publish_rules(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    if deps.controlled_writes is not None:
        return controlled_publication(deps, command)
    draft = _rule_draft_of(_as_mapping(_required(parameters, "draft"), "draft"))
    result = publish_rules(
        draft,
        project_id=project_id,
        unit_of_work=deps.unit_of_work,
        reader=deps.reader,
        # 检查项 B-14：把**调用方所见修订**原样交给发布，不允许发布替调用方接受最新基线。
        # 命令层用 `0` 表达"我认定这是一条新记录"；这里**不**回退到草稿自己的修订号——
        # 那样又变成替调用方猜基线，正是本项要消除的行为。
        expected_revision=_expected_revision_or_none(command),
        context_gaps=_gaps_of(parameters.get("context_gaps"), "context_gaps"),
    )
    return _publication_result(result, published_kind="rule_version")
