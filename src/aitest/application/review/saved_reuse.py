"""整用例复用**选择**的持久登记（裁定 DEC-014 / CORE-001 1.82）。

依据：

- 架构 01《复用、回归与模型任务矩阵》：「`ReuseReference` 登记 `target_run`/`case_id`、
  `source_run`/`step`/`attempt`、来源计划与源码/环境/入口参数/规则/适配器修订、证据引用、
  `validity_checked_at_commit` 及验证依据。**它是已有运行记录的引用关系，不创建虚假 Attempt**，
  不增加执行器或独立服务。」——本模块只登记这条引用关系，**不授予复用资格**；
- 功能 §15 / BC-001「持久选择」：本轮选择"复用哪个源运行/用例"必须持久、可审计、参与幂等；
- CORE-001 1.69/1.72：复用核对输入固定为 `case_id` + `source_run_id`/`source_snapshot` +
  `target_run_id`/`target_snapshot`（`SnapshotContentRef` 三字段），`target` 固定 `case_id`——
  保存侧**沿用同一组引用**，并由**同一个核对器**（`CaseReuseInspection.verify`）派生来源身份，
  不接受调用方自报映射或修订摘要。

落盘形状（`reuse_decision@1`，每个 (项目, 目标运行) 一条记录，追加修订）：

.. code-block:: json

    {"schema_version": "aitest.reuse-decision/1.0", "project_id": "...",
     "target_run_id": "...", "status": "selected",
     "references": [ { ...ReuseReference... } ]}

幂等：`reuse_intent@1` 记录按 `intent_id` 冻结输入摘要与结果；同意图同输入回读原结果，
同意图异输入报冲突（与报告/问题等受控写动作同一模式）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from aitest.application.approval_service import _digest
from aitest.application.execution.reuse_inspection import (
    CaseReuseInspection,
    CaseReuseUnverified,
)
from aitest.contracts.reuse_reference import (
    ReuseReference,
    parse_reuse_reference,
    reuse_reference_payload,
)

REUSE_DECISION_AGGREGATE = "reuse_decision"
REUSE_DECISION_SCHEMA = "aitest.reuse-decision/1.0"
REUSE_INTENT_AGGREGATE = "reuse_intent"
REUSE_INTENT_SCHEMA = "aitest.reuse-intent/1.0"

#: 核对参数（与只读 `inspect_case_reuse` 同一组引用，CORE-001 1.69）。
_PARAMETERS = frozenset(
    {"case_id", "source_run_id", "source_snapshot", "target_run_id", "target_snapshot"}
)


class ReuseSelectionBlocked(ValueError):
    """选择被拒绝；错误码取已登记集合。"""

    code = "REUSE_SELECTION_FAILED"


class ReuseIntentConflict(ReuseSelectionBlocked):
    code = "INTENT_CONFLICT"


class ReuseActorRequired(ReuseSelectionBlocked):
    code = "AWAITING_USER_CONFIRMATION"


def reuse_decision_record_id(project_id: str, target_run_id: str) -> str:
    """每个 (项目, 目标运行) 一条选择记录。"""

    return "reuse-" + _digest([project_id, target_run_id])[7:31]


def reuse_intent_record_id(project_id: str, intent_id: str) -> str:
    return "reuse-intent-" + _digest([project_id, intent_id])[7:31]


@dataclass(frozen=True, slots=True)
class SavedCaseReuseSelection:
    """受控的复用选择登记入口 + 报告侧候选来源读取。"""

    unit: Any
    records: Any
    actors: Any
    inspection: CaseReuseInspection

    # ------------------------------------------------------------ 写入口

    def apply(self, command: Any) -> Mapping[str, object]:
        if getattr(command, "action", None) != "select_case_reuse":
            raise ReuseSelectionBlocked("reuse selection entry only serves select_case_reuse")
        project_id = _text(getattr(command, "project_id", None), "project_id")
        intent_id = _text(getattr(command, "intent_id", None), "intent_id")
        request_id = _text(getattr(command, "request_id", None), "request_id")
        expected = getattr(command, "expected_revision", None)
        if type(expected) is not int or expected < 0:
            raise ReuseSelectionBlocked("expected_revision must be a nonnegative integer")
        parameters = getattr(command, "parameters", None)
        if not isinstance(parameters, Mapping) or set(parameters) != _PARAMETERS:
            raise ReuseSelectionBlocked(
                "reuse selection requires exactly case_id and the two run/snapshot pairs"
            )
        if getattr(command, "target", None) != parameters.get("case_id"):
            raise ReuseSelectionBlocked("reuse selection target must be the case identity")
        self._require_human()

        fingerprint = _digest(
            [
                str(parameters["case_id"]),
                str(parameters["source_run_id"]),
                _snapshot_digest(parameters["source_snapshot"]),
                str(parameters["target_run_id"]),
                _snapshot_digest(parameters["target_snapshot"]),
            ]
        )
        intent_record_id = reuse_intent_record_id(project_id, intent_id)
        recalled = self._recall(project_id, intent_record_id, intent_id, fingerprint)
        if recalled is not None:
            return recalled

        reference = self._reference(command, project_id, parameters)
        record_id = reuse_decision_record_id(project_id, str(parameters["target_run_id"]))
        current_revision = self._current_revision(REUSE_DECISION_AGGREGATE, record_id) or 0
        current = self._decision_payload(record_id)
        references = [item for item in current if item.case_id != reference.case_id] + [reference]
        references.sort(key=lambda item: item.case_id)
        revision = current_revision + 1
        payload = {
            "schema_version": REUSE_DECISION_SCHEMA,
            "project_id": project_id,
            "target_run_id": str(parameters["target_run_id"]),
            "status": "selected",
            "references": [_json_mapping(item) for item in references],
        }
        result = {
            "aggregate_kind": REUSE_DECISION_AGGREGATE,
            "record_id": record_id,
            "revision": revision,
        }
        self._commit(
            request_id=request_id,
            project_id=project_id,
            intent_id=intent_id,
            record_id=record_id,
            expected_revision=current_revision,
            payload=payload,
            intent_record_id=intent_record_id,
            fingerprint=fingerprint,
            result=result,
        )
        return dict(result)

    def _reference(
        self, command: Any, project_id: str, parameters: Mapping[str, object]
    ) -> ReuseReference:
        """用**同一个核对器**派生来源身份，再把引用关系登记成 `ReuseReference`。"""

        try:
            verified = self.inspection.verify(command)
        except CaseReuseUnverified as error:
            raise ReuseSelectionBlocked(str(error)) from error
        if verified.status != "unverified":
            raise ReuseSelectionBlocked(
                "case reuse material is incompatible: "
                + ",".join(verified.denial_reasons)
            )
        inputs = verified.reference_inputs
        mapping = [
            (str(item["source_step_id"]), str(item["source_attempt_id"]))
            for item in verified.step_mapping
            if item.get("source_attempt_id")
        ]
        if any(item.get("source_attempt_id") is None for item in verified.step_mapping):
            raise ReuseSelectionBlocked("case reuse selection requires source attempts")
        if not mapping:
            raise ReuseSelectionBlocked("case reuse selection requires a step mapping")
        evidence_refs = tuple(str(item) for item in _sequence(inputs.get("evidence_refs")))
        if not evidence_refs:
            raise ReuseSelectionBlocked("case reuse selection requires saved source evidence")
        anchor_step, anchor_attempt = mapping[0]
        return ReuseReference(
            schema_version="aitest.reuse-reference/1.0",
            target_run_id=str(parameters["target_run_id"]),
            case_id=str(parameters["case_id"]),
            source_run_id=str(parameters["source_run_id"]),
            source_step_id=anchor_step,
            source_attempt_id=anchor_attempt,
            source_attempt_by_step=tuple(mapping),
            source_plan_revision_digest=_digest_or_none(inputs.get("source_plan_revision_digest")),
            source_content_identity=_text(
                inputs.get("source_content_identity"), "source_content_identity"
            ),
            environment_revision_digest=_digest_or_none(
                inputs.get("environment_revision_digest")
            ),
            entry_input_digest=_digest_or_none(inputs.get("entry_input_digest")),
            rules_revision_digest=_digest_or_none(inputs.get("rules_revision_digest")),
            adapter_digest=_digest_or_none(inputs.get("adapter_digest")),
            evidence_refs=evidence_refs,
            validity_checked_at_commit=str(self._next_commit_sequence()),
            verification_basis=(
                "case reuse inspection matched the frozen source/target material; "
                f"project={project_id}",
            ),
        )

    # ------------------------------------------------------------ 读取

    def references_for_run(self, project_id: str, run_id: str) -> tuple[ReuseReference, ...]:
        """报告侧候选来源：该目标运行当前修订里的复用引用（无记录即空元组）。"""

        if not isinstance(project_id, str) or not project_id.strip():
            raise ReuseSelectionBlocked("reuse selection requires a project identity")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ReuseSelectionBlocked("reuse selection requires a run identity")
        return self._decision_payload(reuse_decision_record_id(project_id, run_id))

    def _decision_payload(self, record_id: str) -> tuple[ReuseReference, ...]:
        revision = self._current_revision(REUSE_DECISION_AGGREGATE, record_id)
        if revision is None:
            return ()
        payload = self._read_payload(REUSE_DECISION_AGGREGATE, record_id, revision)
        if (
            payload.get("schema_version") != REUSE_DECISION_SCHEMA
            or payload.get("status") != "selected"
        ):
            raise ReuseSelectionBlocked("saved reuse selection is unreadable")
        raw = payload.get("references")
        if not isinstance(raw, list):
            raise ReuseSelectionBlocked("saved reuse selection requires its references")
        return tuple(_parse(item) for item in raw)

    # ------------------------------------------------------------ 幂等与提交

    def _recall(
        self, project_id: str, intent_record_id: str, intent_id: str, fingerprint: str
    ) -> Mapping[str, object] | None:
        revision = self._current_revision(REUSE_INTENT_AGGREGATE, intent_record_id)
        if revision is None:
            return None
        payload = self._read_payload(REUSE_INTENT_AGGREGATE, intent_record_id, revision)
        if (
            payload.get("schema_version") != REUSE_INTENT_SCHEMA
            or payload.get("project_id") != project_id
            or payload.get("intent_id") != intent_id
        ):
            raise ReuseIntentConflict("reuse intent belongs to different inputs")
        if payload.get("input_digest") != fingerprint:
            raise ReuseIntentConflict("reuse intent has different frozen input")
        result = payload.get("result")
        if not isinstance(result, Mapping) or set(result) != {
            "aggregate_kind",
            "record_id",
            "revision",
        }:
            raise ReuseSelectionBlocked("saved reuse intent result is unreadable")
        return dict(result)

    def _commit(
        self,
        *,
        request_id: str,
        project_id: str,
        intent_id: str,
        record_id: str,
        expected_revision: int,
        payload: Mapping[str, object],
        intent_record_id: str,
        fingerprint: str,
        result: Mapping[str, object],
    ) -> None:
        self.unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            staged = self.unit.stage_record(
                aggregate_kind=REUSE_DECISION_AGGREGATE,
                record_id=record_id,
                expected_revision=expected_revision,
                payload=dict(payload),
            )
            staged_revision = getattr(staged, "revision", staged)
            if type(staged_revision) is not int or staged_revision != result["revision"]:
                raise ReuseSelectionBlocked("staged reuse selection revision cannot be verified")
            self.unit.stage_record(
                aggregate_kind=REUSE_INTENT_AGGREGATE,
                record_id=intent_record_id,
                expected_revision=0,
                payload={
                    "schema_version": REUSE_INTENT_SCHEMA,
                    "project_id": project_id,
                    "intent_id": intent_id,
                    "action": "select_case_reuse",
                    "record_id": record_id,
                    "input_digest": fingerprint,
                    "result": dict(result),
                },
            )
            self.unit.commit(request_id)
        except BaseException:
            self.unit.rollback(request_id)
            raise

    # ------------------------------------------------------------ 只读与守卫

    def _require_human(self) -> None:
        try:
            actor = self.actors.current()
        except (LookupError, RuntimeError, ValueError) as error:
            raise ReuseActorRequired(
                "reuse selection requires a controlled interactive session"
            ) from error
        entry_kind = getattr(getattr(actor, "entry_kind", None), "value", None)
        session_id = getattr(actor, "session_id", None)
        if entry_kind == "agent_relay" or not getattr(actor, "interactive", False):
            raise ReuseActorRequired("reuse selection cannot come from an AI relay session")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ReuseActorRequired("reuse selection requires a session identity")

    def _next_commit_sequence(self) -> int:
        try:
            return int(self.unit.next_commit_seq())
        except (TypeError, ValueError) as error:
            raise ReuseSelectionBlocked("commit sequence cannot be verified") from error

    def _current_revision(self, aggregate_kind: str, record_id: str) -> int | None:
        revision = self.records.current_revision(aggregate_kind, record_id)
        if revision is None or (type(revision) is int and revision == 0):
            return None
        if type(revision) is not int or revision < 1:
            raise ReuseSelectionBlocked("saved record revision cannot be verified")
        return revision

    def _read_payload(
        self, aggregate_kind: str, record_id: str, revision: int
    ) -> Mapping[str, Any]:
        try:
            record = self.records.read(
                aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ReuseSelectionBlocked("saved reuse record cannot be read") from error
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping):
            raise ReuseSelectionBlocked("saved reuse record payload is unreadable")
        return payload


def _parse(item: object) -> ReuseReference:
    if not isinstance(item, Mapping):
        raise ReuseSelectionBlocked("saved reuse reference must be a mapping")
    try:
        return parse_reuse_reference(item)
    except ValueError as error:
        raise ReuseSelectionBlocked("saved reuse reference is invalid") from error


def _json_mapping(reference: ReuseReference) -> dict[str, object]:
    import json

    decoded = json.loads(reuse_reference_payload(reference).decode("utf-8"))
    if not isinstance(decoded, dict):  # pragma: no cover - 规范字节一定是对象
        raise ReuseSelectionBlocked("reuse reference payload is not an object")
    return {str(key): value for key, value in decoded.items()}


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReuseSelectionBlocked(f"reuse selection requires {name}")
    return value


def _digest_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ReuseSelectionBlocked("reuse selection digest must be an exact sha256 digest")
    return value


def _snapshot_digest(value: object) -> str:
    if not isinstance(value, Mapping):
        raise ReuseSelectionBlocked("reuse selection requires an exact snapshot reference")
    return _digest([str(value.get("snapshot_id")), str(value.get("record_revision")),
                    str(value.get("content_identity"))])


def _sequence(value: object) -> Iterable[object]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ReuseSelectionBlocked("reuse selection requires a sequence")
    return value


__all__ = [
    "REUSE_DECISION_AGGREGATE",
    "REUSE_DECISION_SCHEMA",
    "REUSE_INTENT_AGGREGATE",
    "REUSE_INTENT_SCHEMA",
    "ReuseActorRequired",
    "ReuseIntentConflict",
    "ReuseSelectionBlocked",
    "SavedCaseReuseSelection",
    "reuse_decision_record_id",
    "reuse_intent_record_id",
]
