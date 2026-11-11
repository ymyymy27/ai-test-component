"""宿主只读视图查询（架构 06 §1/§2/§4/§5、§54 `editor_host` 的"只读查询与事件续读"）。

面板读的是 **DTO 视图**（RunDTO/DecisionDTO/…），不是原始记录载荷：架构 06 §1 把 DTO 的生产方
写成"应用查询与 interfaces/dto.py"，§9 又要求"视图不直接读文件、调用适配器或重算判定"。
本模块就是那层只读查询接缝：

- 判定**只算一次**：调用 D 侧 `SavedReportService.live_decision`（报告与面板共用同一次领域判定），
  再经 `interfaces/dto.py` 的纯投影函数生成 DTO；
- **不写任何记录**：只读已提交事实与冻结准备，协商式 `read_view` 暴露有限选择器；
- 外层返回读取期间未变化的全视图提交/事件边界，供宿主续读；DTO 内的执行事实快照
  标识保留原义，不把它当成整个视图的事件游标。
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aitest.application.planning.serialization import plan_coverage_contract_from_payload
from aitest.application.planning.substrate import require_record_identity
from aitest.application.ports import EvidenceObjectStore, RecordQuery
from aitest.application.project.delivery_verification import (
    DeliveryVerificationReader,
    delivery_projection,
)
from aitest.application.project.serialization import delivery_from_payload
from aitest.application.review.function_coverage_run import build_run_function_coverage
from aitest.application.review.saved_reports import SavedReportService
from aitest.contracts.commands import Command
from aitest.contracts.errors import ErrorCode
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.read_views import ReadViewQuery
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.review.function_coverage import FunctionCoverage
from aitest.domain.review.reports import DecisionResult
from aitest.interfaces.dto import (
    DeliveryViewDTO,
    PanelViewDTO,
    RunViewDTO,
    delivery_view_dto,
    panel_view_dto,
    run_view_dto,
)

__all__ = ["CommittedViewReader", "ReadViewError"]

# 证据正文读取预算：超过该大小只返回元数据，避免面板负载失控。
_MAX_EVIDENCE_BODY_BYTES = 4 * 1024 * 1024

# 证据正文显示态投影（CORE-001 1.95：视图不返回绝对路径）。
_QUOTED_ABSOLUTE_PATH = re.compile(r'"(?:[A-Za-z]:[\\/]|\\\\|//|/)[^"\r\n]*"')
_UNQUOTED_UNC_PATH = re.compile(r"(?<![\w])\\\\[^\s\"'<>|\r\n]+")
_UNQUOTED_POSIX_UNC_PATH = re.compile(r"(?<![\w:])//[^\s\"'<>|/]+/[^\s\"'<>|\r\n]*")
_UNQUOTED_WINDOWS_PATH = re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\s\"'<>|\r\n]*")
_UNQUOTED_POSIX_PATH = re.compile(r"(?<![\w:/])/(?:[^\s\"'<>|/]+/)*[^\s\"'<>|/]+")


class ReadViewError(ValueError):
    """带结构化错误码的只读视图失败。

    入口层用 ``getattr(exc, "code", "INTERNAL_ERROR")`` 取码；只读视图此前
    统一抛裸 ``ValueError``，会把“参数错误/记录不存在/需重取快照”都报成
    ``INTERNAL_ERROR``。这里补上已登记的 ``ErrorCode``（架构05 §3/§4）。
    """

    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


def _project_display_paths(text: str, workspace_root: str | None) -> str:
    """把正文中的绝对路径替换为稳定占位符（CORE-001 1.95）。

    证据正文来自安全捕获流，已过滤凭据，但仍可能包含工作空间、用户目录、
    临时目录或被测进程栈里的绝对路径；视图合同要求不返回这些内部信息。
    先替换已知根，再用保守模式覆盖引号内/未加引号的绝对路径；URL 不匹配。
    """

    roots: list[tuple[str, str]] = []
    for root, token in (
        (workspace_root, "<workspace>"),
        (str(Path.home()), "<home>"),
        (tempfile.gettempdir(), "<temp>"),
    ):
        if root and len(root) > 1:
            roots.append((root, token))
    projected = text
    for root, token in roots:
        projected = re.sub(re.escape(root), token, projected, flags=re.IGNORECASE)
    projected = _QUOTED_ABSOLUTE_PATH.sub('"<absolute-path>"', projected)
    projected = _UNQUOTED_UNC_PATH.sub("<absolute-path>", projected)
    projected = _UNQUOTED_POSIX_UNC_PATH.sub("<absolute-path>", projected)
    projected = _UNQUOTED_WINDOWS_PATH.sub("<absolute-path>", projected)
    projected = _UNQUOTED_POSIX_PATH.sub("<absolute-path>", projected)
    return projected



class CommittedViewReader:
    """从**已提交记录**读视图 DTO；判定复用 D 侧唯一装配点。"""

    def __init__(
        self,
        *,
        decisions: SavedReportService,
        reader: Any | None = None,
        records: Any | None = None,
        delivery_verification: DeliveryVerificationReader | None = None,
        objects: EvidenceObjectStore | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        if decisions is None:
            raise ValueError("the view reader requires the live decision capability")
        self._decisions = decisions
        self._reader = reader
        self._records = records
        self._delivery_verification = delivery_verification
        self._objects = objects
        # 仅用于证据正文的显示态路径投影，不参与记录读取或判定。
        self._workspace_root = None if workspace_root is None else str(workspace_root)

    def dispatch(self, command: Command) -> Mapping[str, object]:
        """Finite DTO query with a single verified outer view/event boundary."""
        project = command.project_id
        if not isinstance(project, str) or not project.strip() or len(project) > 128:
            # 参数问题应可纠正，不能报成 INTERNAL_ERROR（架构05 §3）。
            raise ReadViewError(
                "read_view requires an explicit project",
                code=ErrorCode.INVALID_REQUEST.value,
            )
        try:
            selector = ReadViewQuery.model_validate(command.parameters)
        except ValueError as exc:
            raise ReadViewError(
                "invalid read_view selector", code=ErrorCode.INVALID_REQUEST.value
            ) from exc
        view = selector.view
        if view in {
            "plan", "report", "case", "issue", "evidence", "local_review", "reuse_decision",
            "rule_draft", "rule_version",
        }:
            assert selector.record_id is not None and selector.revision is not None
            boundary = self._boundary(project)
            if view == "evidence":
                record_view = self.evidence_view(
                    project_id=project, record_id=selector.record_id,
                    revision=selector.revision,
                )
            else:
                record_view = self.record_view(
                    project_id=project, kind=view, record_id=selector.record_id,
                    revision=selector.revision,
                )
            if self._boundary(project) != boundary:
                # 架构05 §4：游标失效要求重新取同边界快照，不伪装成内部错误。
                raise ReadViewError(
                    "the committed view boundary changed while reading",
                    code=ErrorCode.SNAPSHOT_REQUIRED.value,
                )
            return {"view_type": view, "view": record_view, **boundary}
        identity = selector.run_id if view in {"run", "panel"} else selector.delivery_id
        assert identity is not None
        boundary = self._boundary(project)
        result: RunViewDTO | PanelViewDTO | DeliveryViewDTO
        if view == "run":
            result = self.run_view(project_id=project, run_id=identity)
        elif view == "panel":
            result = self.panel_view(project_id=project, run_id=identity)
        else:
            result = self.delivery_view(
                project_id=project,
                delivery_id=identity,
                revision=selector.revision,
                submission_id=selector.submission_id,
            )
        if self._boundary(project) != boundary:
            raise ReadViewError(
                "the committed view boundary changed while reading",
                code=ErrorCode.SNAPSHOT_REQUIRED.value,
            )
        return {"view_type": view, "view": result.model_dump(mode="json"), **boundary}

    def evidence_view(
        self, *, project_id: str, record_id: str, revision: int,
    ) -> dict[str, object]:
        """证据正文只读视图（Issue #125）。

        形状与 plan/report/case/issue 一致（record_id + revision + record），按需附带脱敏后的正文：
        1. `redaction_state` 非安全态（非 redacted/not_required）→ 只返回元数据；
        2. 正文来自对象仓（落盘前已过不可关闭的凭据过滤），这里只做显示态
           绝对路径投影，不改动已保存字节；
        3. 元数据用白名单，剔除 `code_identity`（含 workspace_ref 绝对路径）等敏感字段；
        4. 超过 4MB 只返回元数据，避免面板负载失控。
        """
        if self._reader is None:
            # 视图能力未装配：明确“能力不可用”，不报内部错误。
            raise ReadViewError(
                "record view is unavailable",
                code=ErrorCode.CAPABILITY_UNAVAILABLE.value,
            )
        try:
            record = self._reader.read(
                aggregate_kind="evidence_ref", record_id=record_id, revision=revision,
            )
        except (KeyError, OSError, ValueError) as exc:
            # 记录仓对缺失/未知修订抛 ValueError；入口需要可核对的 RECORD_NOT_FOUND。
            raise ReadViewError(
                "saved evidence is unavailable", code=ErrorCode.RECORD_NOT_FOUND.value
            ) from exc
        try:
            require_record_identity(
                record, aggregate_kind="evidence_ref", record_id=record_id, revision=revision,
            )
        except ValueError as exc:
            raise ReadViewError(
                "saved evidence envelope is inconsistent",
                code=ErrorCode.INTEGRITY_CHECK_FAILED.value,
            ) from exc
        payload = record.payload
        if payload.get("project_id") != project_id or payload.get("evidence_id") != record_id:
            # 跨项目/编号不符不暴露“记录是否存在”，统一按不可用处理。
            raise ReadViewError(
                "saved evidence is unavailable", code=ErrorCode.RECORD_NOT_FOUND.value
            )
        if payload.get("evidence_revision") != revision:
            # CORE-001 1.95 的选择器 revision 就是 evidence_revision，不能返回不一致快照。
            raise ReadViewError(
                "saved evidence revision differs from the requested revision",
                code=ErrorCode.INTEGRITY_CHECK_FAILED.value,
            )

        whitelist = (
            "evidence_id", "evidence_revision", "project_id", "run_id", "step_id",
            "attempt_id", "source_instance_id", "evidence_kind", "capture_source",
            "object_digest", "object_size", "integrity", "redaction_state",
            "projection_state", "redaction_summary_ref", "evidence_level",
            "gap_ids", "media_type", "created_at",
        )
        view: dict[str, object] = {
            "project_id": project_id,
            "record_id": record_id,
            "record_revision": revision,
            "record_kind": "evidence",
            "historical_snapshot": True,
            "record": {key: payload[key] for key in whitelist if key in payload},
        }

        redaction_state = payload.get("redaction_state")
        if redaction_state not in {"redacted", "not_required"}:
            view["body_omitted_reason"] = "redaction_unsafe"
            return view

        object_size = payload.get("object_size")
        object_digest = payload.get("object_digest")
        media_type = payload.get("media_type") or "application/octet-stream"
        if (
            self._objects is None or not isinstance(object_size, int)
            or object_size < 0 or not isinstance(object_digest, str)
            or not object_digest.startswith("sha256:")
        ):
            view["body_omitted_reason"] = "object_unavailable"
            return view
        if object_size > _MAX_EVIDENCE_BODY_BYTES:
            view["body_omitted_reason"] = "body_too_large"
            return view

        try:
            content = self._objects.read_bytes(StoredObjectRef(
                project_id, object_digest, object_size, media_type,
                f"objects/{project_id}/{object_digest.removeprefix('sha256:')}",
            ))
        except (OSError, ValueError):
            view["body_omitted_reason"] = "object_unreadable"
            return view
        if type(content) is not bytes or len(content) != object_size:
            view["body_omitted_reason"] = "object_corrupted"
            return view

        try:
            body = content.decode("utf-8")
        except UnicodeDecodeError:
            view["body_omitted_reason"] = "binary_content"
        else:
            # 正文保留业务内容，但按 CORE-001 1.95 移除绝对路径等内部信息。
            view["body"] = _project_display_paths(body, self._workspace_root)
        return view

    def record_view(
        self, *, project_id: str, kind: str, record_id: str, revision: int,
    ) -> dict[str, object]:
        """Exact project-owned public fields; no paths, credentials or generic record dump."""
        fields = {
            "plan": ("plan_id", "revision", "scope", "cases", "template_versions",
                     "run_tier", "initial_driver", "status", "scope_id", "scope_revision",
                     "required_case_ids", "case_revisions", "rule_revisions",
                     "coverage_contract"),
            "case": ("case_id", "revision", "layer", "objective", "preconditions", "inputs",
                     "steps", "expected", "verification_method", "links", "assertion_basis",
                     "independent_verification", "mock_scope", "importance"),
            "report": ("report_id", "content_revision", "run_id", "context", "decision",
                       "business_outcome", "evidence_refs", "evidence_revisions",
                       "issue_revisions", "reuse",
                       "created_at", "execution_snapshot_ref", "blocking_issue_ids"),
            "issue": ("issue_id", "title", "description", "severity", "status", "state",
                      "expected", "actual", "evidence_refs", "evidence_revisions", "owner",
                      "facts", "hypotheses",
                      "project_id", "run_id", "case_id", "step_id", "attempt_id"),
            "local_review": ("review_id", "report_id", "report_revision", "reviewer",
                             "conclusion", "explanation", "recorded_at"),
            # 复用选择每个目标运行一条记录；面板读取当前修订后再追加新选择。
            "reuse_decision": ("target_run_id", "status", "references"),
            "rule_draft": ("rule_id", "revision", "scope", "text", "source", "steps",
                           "evidence_requirements", "enablement", "confirmed",
                           "unknown_extension_fields"),
            "rule_version": ("rule_id", "revision", "scope", "text", "source", "steps",
                             "evidence_requirements", "confirmation_id", "digest",
                             "status"),
        }
        if kind not in fields or self._reader is None:
            raise ValueError("record view is unavailable")
        record = self._reader.read(aggregate_kind=kind, record_id=record_id, revision=revision)
        require_record_identity(
            record, aggregate_kind=kind, record_id=record_id, revision=revision,
        )
        payload = record.payload
        identity_key = (
            "review_id" if kind == "local_review"
            else "rule_id" if kind in {"rule_draft", "rule_version"}
            else f"{kind}_id"
        )
        # 复用选择由 (project, target_run) 派生记录身份，正文不带同名 id；
        # 记录仓已按 project 隔离，这里只核对项目并使用请求的精确修订。
        if payload.get("project_id") != project_id or (
            kind != "reuse_decision" and payload.get(identity_key) != record_id
        ):
            raise ValueError("record view does not match its project and identity")
        record_fields = {key: payload[key] for key in fields[kind] if key in payload}
        if kind in {"report", "issue"}:
            # CORE-001 1.96：旧记录缺该键时按空映射读取；键存在但形状损坏时
            # 不能静默当成空映射，否则会掩盖记录损坏。
            revisions = record_fields.get("evidence_revisions")
            if revisions is None:
                record_fields["evidence_revisions"] = {}
            elif isinstance(revisions, Mapping):
                record_fields["evidence_revisions"] = dict(revisions)
            else:
                raise ReadViewError(
                    "saved evidence_revisions is not an object",
                    code=ErrorCode.INTEGRITY_CHECK_FAILED.value,
                )
        return {
            "project_id": project_id, "record_id": record_id, "record_revision": revision,
            "record_kind": kind, "historical_snapshot": True,
            "record": record_fields,
        }

    def _boundary(self, project_id: str) -> dict[str, object]:
        query = getattr(self._reader, "query", None)
        if not callable(query):
            # 取不到配对边界时要求客户端重取快照，不返回猜测值（架构05 §4）。
            raise ReadViewError(
                "committed view/event pairing is unavailable",
                code=ErrorCode.SNAPSHOT_REQUIRED.value,
            )
        page = query(RecordQuery(project_id=project_id, limit=1))
        values = {
            name: getattr(page, name, None)
            for name in ("commit_id", "event_cursor", "generation", "index_version")
        }
        for name in ("commit_id", "generation", "index_version"):
            value = values[name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ReadViewError(
                    "the exact committed view/event boundary is unverified",
                    code=ErrorCode.SNAPSHOT_REQUIRED.value,
                )
        cursor = values["event_cursor"]
        if not isinstance(cursor, str) or not cursor.strip():
            raise ReadViewError(
                "the exact committed view/event boundary is unverified",
                code=ErrorCode.SNAPSHOT_REQUIRED.value,
            )
        return values

    def _function_coverage(
        self, project_id: str, facts: ExecutionFacts, decision: DecisionResult
    ) -> tuple[FunctionCoverage, ...]:
        """从运行所冻结的计划契约生成功能×维度覆盖投影。

        计划没有覆盖契约、记录不可读或契约损坏时返回空元组：这是只读视图，
        不能因为附加收据不可用就让整份运行视图失败；运行事实与判定仍可读。
        """
        if self._records is None:
            return ()
        try:
            ref = facts.plan_revision
            record = self._records.read(
                aggregate_kind="plan",
                record_id=ref.revision_id,
                revision=ref.revision_no,
            )
            payload = getattr(record, "payload", None)
            if not isinstance(payload, Mapping):
                return ()
            contract = plan_coverage_contract_from_payload(payload)
            if contract is None:
                return ()
            plan_revision = payload.get("revision")
            if type(plan_revision) is not int or plan_revision < 1:
                return ()
            return build_run_function_coverage(
                contract,
                coverage=decision.coverage,
                plan_revision=plan_revision,
                run_id=facts.run_id,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return ()

    def run_view(self, *, project_id: str, run_id: str) -> RunViewDTO:
        """运行视图：事实 + 同一次领域判定 → `RunViewDTO`（含快照边界与配对游标）。"""

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("the run view requires an explicit project identity")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("the run view requires an explicit run identity")
        facts, decision = self._decisions.live_decision(project_id, run_id)
        return run_view_dto(
            facts,
            decision,
            function_coverage=self._function_coverage(project_id, facts, decision),
        )

    def panel_view(self, *, project_id: str, run_id: str) -> PanelViewDTO:
        """面板主屏视图（架构 06 §1 的「当前段 + 信息区」）：同一次判定 + 运行视图。

        只读已提交事实与**同一次**领域判定；`primary_action` 的动作标识未冻结，故不给候选
        （见待裁定第 8 项）。
        """

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("the panel view requires an explicit project identity")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("the panel view requires an explicit run identity")
        facts, decision = self._decisions.live_decision(project_id, run_id)
        function_coverage = self._function_coverage(project_id, facts, decision)
        return panel_view_dto(
            facts,
            decision,
            run_view_dto(facts, decision, function_coverage=function_coverage),
            project_name=self._project_name(project_id),
        )

    def _project_name(self, project_id: str) -> str | None:
        """项目记录里的名称（只读投影）；读不到就返回 None，界面回退到通用标题。"""
        records = self._records
        if records is None:
            return None
        try:
            revision = records.current_revision(aggregate_kind="project", record_id=project_id)
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                return None
            record = records.read(
                aggregate_kind="project", record_id=project_id, revision=revision,
            )
            name = record.payload.get("name")
        except (OSError, KeyError, TypeError, ValueError):
            return None
        return name.strip() if isinstance(name, str) and name.strip() else None

    def delivery_view(
        self,
        *,
        project_id: str,
        delivery_id: str,
        revision: int | None = None,
        submission_id: str | None = None,
    ) -> DeliveryViewDTO:
        """交付说明视图（BD-001 §7.2）：自述与验证**分列**，核验状态取冻结三态。

        - 只读**准确修订**：给出 `revision` 时按该修订读，否则读当前修订指针（不读"最新正文"）；
        - 记录正文里的 `project_id` 必须与请求项目一致（跨项目一律拒绝）；
        - 传入 `submission_id` 时附带该正式提测记录的验收项集合与内容身份；**不猜**"哪个提测"。
        """

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("the delivery view requires an explicit project identity")
        if not isinstance(delivery_id, str) or not delivery_id.strip():
            raise ValueError("the delivery view requires an explicit delivery identity")
        if self._reader is None or self._records is None:
            raise ValueError("the delivery view requires saved record access")
        exact = revision
        if exact is None:
            current = self._records.current_revision("delivery", delivery_id)
            if type(current) is not int or current < 1:
                raise ValueError("saved delivery is unavailable")
            exact = current
        if type(exact) is not int or exact < 1:
            raise ValueError("the delivery view requires a positive record revision")
        record = self._reader.read(aggregate_kind="delivery", record_id=delivery_id, revision=exact)
        payload = record.payload
        delivery = delivery_from_payload(payload)
        if payload.get("project_id") != project_id:
            raise ValueError("saved delivery belongs to another project")
        if (
            delivery.delivery_id != delivery_id
            or record.record_id != delivery_id
            or record.revision != exact
            or record.aggregate_kind != "delivery"
        ):
            raise ValueError("saved delivery does not match its exact record identity")
        submission: dict[str, object] | None = None
        if submission_id is not None:
            if not isinstance(submission_id, str) or not submission_id.strip():
                raise ValueError("the delivery view requires an explicit submission identity")
            submission_revision = self._records.current_revision(
                "delivery_submission", submission_id
            )
            if type(submission_revision) is not int or submission_revision < 1:
                raise ValueError("saved delivery submission is unavailable")
            submission_record = self._reader.read(
                aggregate_kind="delivery_submission",
                record_id=submission_id,
                revision=submission_revision,
            )
            submission = dict(submission_record.payload)
            if submission.get("project_id") != project_id:
                raise ValueError("saved delivery submission belongs to another project")
            if (
                submission.get("submission_id") != submission_id
                or submission.get("delivery_id") != delivery_id
                or submission.get("delivery_record_revision") != exact
                or submission_record.record_id != submission_id
                or submission_record.aggregate_kind != "delivery_submission"
                or submission_record.revision != submission_revision
            ):
                raise ValueError("saved submission does not bind the exact delivery revision")
            # Record revision belongs to the verified envelope, not its business body.
            submission["revision"] = submission_record.revision
        view = delivery_view_dto(delivery, project_id=project_id, submission=submission)
        projection = delivery_projection(submission) if submission is not None else None
        if projection is not None and submission is not None:
            try:
                if self._delivery_verification is None:
                    raise ValueError("delivery verification material reader is unavailable")
                report_revision = projection["report_record_revision"]
                if (type(report_revision) is not int or report_revision < 1
                        or view.submission is None or view.verification_report_id is None):
                    raise ValueError("delivery verification reference is unavailable")
                current, _ = self._delivery_verification.read(
                    project_id, view.verification_report_id, report_revision,
                    view.submission.content_identity, view.submission.acceptance_item_ids,
                )
                if current != projection:
                    raise ValueError("delivery verification material differs")
            except (OSError, ValueError, KeyError, TypeError):
                return view.model_copy(update={
                    "verified_in_scope": (),
                    "unverified_scope": (view.submission.acceptance_item_ids
                                         if view.submission else ()),
                    "verification_state": "material_invalid",
                })
        return view
