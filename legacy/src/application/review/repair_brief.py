"""`copy_repair_brief` 生产用例：从指定问题/尝试投影脱敏文本（架构 03 §5）。

合同要求（03 §5 末段）：*"copy_repair_brief 复用已有报告脱敏器，从指定 Issue/Attempt
投影文本，附可解释的复测指引；位置未知保持未知。不直接把原始日志或密钥复制到剪贴板。"*

因此本模块：

- 只读：不写任何记录、不建业务副本（合同明确"不另建 AI 问题副本"）；
- 只给标识与引用，**不复制证据正文**；
- 位置/事实不可得时显式保留"未知"，不猜、不用 0/false 顶替；
- 文本必须经 `ProjectionPort` 脱敏；没有投影器就拒绝输出（导出/复制底线不可关闭）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from aitest.application.planning.model_ports import Projection
from aitest.application.review.saved_defects import (
    ISSUE_AGGREGATE,
    blocking_state,
    issue_from_payload,
)
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.domain.review.defects import IssueDisposition, IssueRecord

BRIEF_ACTIONS = ("copy_repair_brief",)
UNKNOWN = "未知"


class RepairBriefError(ValueError):
    """复测简报错误；错误码复用已登记集合，不新造协议词汇。"""

    code = "INVALID_REQUEST"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(message)


class BriefRecordReader(Protocol):
    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int: ...
    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> Any: ...


class BriefRuntimeFactsReader(Protocol):
    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> Any: ...


class BriefProjectionPort(Protocol):
    def project(
        self, *, material: Mapping[MaterialKind, str], source_snippets_enabled: bool
    ) -> Projection: ...


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepairBriefError(f"{name} must be nonempty text")
    return value


class SavedRepairBrief:
    """只读投影；不做任何持久化。"""

    def __init__(
        self,
        *,
        records: BriefRecordReader,
        runtime: BriefRuntimeFactsReader,
        projector: BriefProjectionPort | None,
    ) -> None:
        for name, value in (("records", records), ("runtime", runtime)):
            if value is None:
                raise RepairBriefError(
                    f"repair brief requires the {name} capability",
                    code="CAPABILITY_UNAVAILABLE",
                )
        self._records, self._runtime, self._projector = records, runtime, projector

    def copy_repair_brief(self, command: object) -> Mapping[str, object]:
        project_id = _text(getattr(command, "project_id", None), "project_id")
        parameters = getattr(command, "parameters", None)
        if not isinstance(parameters, Mapping):
            raise RepairBriefError("parameters must be an object")
        unknown = set(parameters) - {"issue_id", "run_id", "attempt_id"}
        if unknown:
            raise RepairBriefError(
                f"copy_repair_brief received unknown parameters: {sorted(unknown)}"
            )
        issue_id = _text(parameters.get("issue_id"), "issue_id")
        issue = self._issue(project_id, issue_id)
        run_id = parameters.get("run_id")
        run_id = None if run_id is None else _text(run_id, "run_id")
        attempt_id = parameters.get("attempt_id")
        attempt_id = None if attempt_id is None else _text(attempt_id, "attempt_id")

        facts, facts_state = self._facts(project_id, run_id)
        def shown(value: object) -> str:
            return UNKNOWN if value is None else str(value)

        sections = [
            f"# 复测简报 {issue.issue_id}",
            "",
            f"- 标题：{issue.title}",
            f"- 状态：{issue.status.value}；处置：{issue.disposition.value}",
            f"- 严重度：{shown(issue.severity.value if issue.severity is not None else None)}"
            f"；阻塞：{blocking_state(issue)}",
            f"- 责任人：{shown(issue.owner)}",
            f"- 确认人：{shown(issue.confirmed_by)}",
            f"- 修复版本：{shown(issue.fix_version)}",
            f"- 回归方式：{shown(issue.regression_method)}",
            f"- 关闭条件：{shown(issue.closure_criteria)}",
            "",
            "## 复测指引",
            "",
        ]
        guidance, unknown_positions = self._guidance(issue, facts, facts_state, attempt_id)
        sections.extend(guidance)
        sections.extend(["", "## 证据引用（只给引用，不含正文）", ""])
        evidence_ids = self._evidence_ids(facts, attempt_id)
        if evidence_ids:
            sections.extend(f"- {evidence_id}" for evidence_id in evidence_ids)
        else:
            sections.append(f"- {UNKNOWN}（没有可读的证据引用）")
        sections.extend(["", "## 未知位置", ""])
        if unknown_positions:
            sections.extend(f"- {item}" for item in unknown_positions)
        else:
            sections.append("- 无")
        body = "\n".join(sections) + "\n"

        text, excluded = self._project(
            {
                MaterialKind.CASE_CONTENT: body,
                MaterialKind.EXECUTION_FACT: f"run={run_id or UNKNOWN};facts={facts_state}",
                MaterialKind.EVIDENCE_REFERENCE: ",".join(evidence_ids) or UNKNOWN,
            }
        )
        return {
            "brief": text,
            "issue_id": issue.issue_id,
            "issue_revision": issue.revision,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "facts_state": facts_state,
            "unknown_positions": unknown_positions,
            "excluded": excluded,
            "redaction_policy_version": "aitest.redaction/1.0",
        }

    # ------------------------------------------------------------ 读取

    def _issue(self, project_id: str, issue_id: str) -> IssueRecord:
        revision = self._records.current_revision(
            aggregate_kind=ISSUE_AGGREGATE, record_id=issue_id
        )
        if type(revision) is not int or revision < 1:
            raise RepairBriefError(f"issue {issue_id} is not saved", code="RECORD_NOT_FOUND")
        record = self._records.read(
            aggregate_kind=ISSUE_AGGREGATE, record_id=issue_id, revision=revision
        )
        payload = getattr(record, "payload", None)
        if (
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (ISSUE_AGGREGATE, issue_id, revision) or not isinstance(payload, Mapping):
            raise RepairBriefError("saved issue envelope cannot be verified")
        if payload.get("project_id") != project_id:
            raise RepairBriefError(f"issue {issue_id} belongs to another project")
        return issue_from_payload(payload)

    def _facts(
        self, project_id: str, run_id: str | None
    ) -> tuple[Any | None, str]:
        if run_id is None:
            return None, f"{UNKNOWN}（未指定运行）"
        try:
            facts = self._runtime.read_runtime_revision_facts(
                project_id=project_id, run_id=run_id
            )
        except (OSError, KeyError, TypeError, ValueError):
            return None, f"{UNKNOWN}（运行事实不可读）"
        if facts is None or getattr(facts, "project_id", None) != project_id:
            return None, f"{UNKNOWN}（运行事实不可核实）"
        return facts, "可读"

    def _evidence_ids(self, facts: Any | None, attempt_id: str | None) -> tuple[str, ...]:
        if facts is None:
            return ()
        items = [
            item
            for item in getattr(facts, "evidence_refs", ())
            if attempt_id is None or getattr(item, "attempt_id", None) == attempt_id
        ]
        return tuple(sorted({str(getattr(item, "evidence_id", "")) for item in items} - {""}))

    def _guidance(
        self, issue: IssueRecord, facts: Any | None, facts_state: str, attempt_id: str | None
    ) -> tuple[list[str], list[str]]:
        """可解释的复测指引；事实不可得的位置保持"未知"。"""

        lines: list[str] = []
        unknown: list[str] = []
        if facts is None:
            unknown.append(f"运行事实：{facts_state}")
        steps = list(getattr(facts, "steps", ())) if facts is not None else []
        attempts = list(getattr(facts, "attempts", ())) if facts is not None else []
        target = None
        if attempt_id is not None:
            target = next(
                (item for item in attempts if getattr(item, "attempt_id", None) == attempt_id),
                None,
            )
            if target is None:
                unknown.append("指定的尝试：在已保存事实中找不到")
        if target is None and attempts:
            target = attempts[-1]
            unknown.append("指定尝试：未指定，指引按最后一次尝试生成")
        if target is None:
            unknown.append("尝试：没有可读的尝试")
        if target is not None:
            step = next(
                (
                    item
                    for item in steps
                    if getattr(item, "step_id", None) == getattr(target, "step_id", None)
                ),
                None,
            )
            lines.append(f"- 复测步骤：{getattr(target, 'step_id', UNKNOWN)}")
            if step is not None:
                lines.append(f"- 用例：{getattr(step, 'case_id', UNKNOWN)}")
                lines.append(f"- 层级：{getattr(step, 'level', UNKNOWN)}")
            else:
                unknown.append("步骤：尝试所属步骤不可读")
            state = getattr(getattr(target, "state", None), "value", UNKNOWN)
            capture = getattr(getattr(target, "capture_completeness", None), "value", UNKNOWN)
            lines.append(f"- 上次尝试状态：{state}")
            lines.append(f"- 上次采集完整性：{capture}")
            lines.append(f"- 是否为当前尝试：{getattr(target, 'is_current', UNKNOWN)}")
        if issue.closure_criteria is not None:
            lines.append(f"- 复测须满足：{issue.closure_criteria}")
        if issue.regression_method is not None:
            lines.append(f"- 回归方式：{issue.regression_method}")
        lines.append("- 关闭需要**新的实际执行证据**；复用或文字声明不构成关闭依据。")
        if issue.disposition is IssueDisposition.FIXED:
            unknown.append("处置为已修复但状态未关闭：需要新的回归执行证据")
        return lines, unknown

    def _project(
        self, material: Mapping[MaterialKind, str]
    ) -> tuple[str, list[dict[str, object]]]:
        if self._projector is None:
            raise RepairBriefError(
                "repair brief requires the redaction projector capability",
                code="CAPABILITY_UNAVAILABLE",
            )
        projection = self._projector.project(material=material, source_snippets_enabled=False)
        excluded: list[dict[str, object]] = [
            {"material_kind": kind.value, "field_path": path}
            for kind, path in projection.excluded
        ]
        projected = {item.material_kind: item.projected_text for item in projection.projected}
        if MaterialKind.CASE_CONTENT not in projected:
            raise RepairBriefError("the repair brief cannot be safely projected")
        return projected[MaterialKind.CASE_CONTENT], excluded


__all__ = ["BRIEF_ACTIONS", "RepairBriefError", "SavedRepairBrief"]
