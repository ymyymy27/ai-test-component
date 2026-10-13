"""Save independent business reads before publishing their verification facts."""

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from aitest.application.errors import CapabilityUnavailable
from aitest.application.evidence.evidence_review import (
    EvidenceReviewService,
    freeze_verification_request,
)
from aitest.application.evidence.query_materials import (
    BusinessVerificationBlocked as BusinessVerificationBlocked,
)
from aitest.application.evidence.query_materials import (
    SavedBusinessQueryReader,
    _bytes,
    _evidence_record,
    _polling,
    _request,
    _request_payload,
)
from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.application.execution.commands import ExecutionCommands, InvalidExecutionCommand
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import execution_payload_digest
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.application.ports import (
    BusinessVerificationCapturePort,
    BusinessVerificationResolver,
    CapturedBusinessVerification,
    EvidenceObjectStore,
    RecordRepository,
    VerificationRequest,
)
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import (
    CodeIdentityFact,
    ExecutionFacts,
    SourceBindingKindFact,
)
from aitest.domain.evidence.evidence import StoredObjectRef, VerificationObservation
from aitest.domain.evidence.polling import derive_polled_query, query_polling_policy
from aitest.domain.evidence.verification import compare_business_fields
from aitest.domain.execution.assertions import freeze_json_value, json_equal


class SavedBusinessVerification(SavedBusinessQueryReader):
    def __init__(
        self,
        coordinator: ExecutionCommitCoordinator,
        authorizations: ExecutionAuthorizationService,
        objects: EvidenceObjectStore,
        *,
        workspace_id: str,
        instance_id: str,
        resolver: BusinessVerificationResolver | None,
        verifier: BusinessVerificationCapturePort | None,
        protector: Callable[[Mapping[str, object]], Mapping[str, object]],
    ) -> None:
        self.coordinator, self.authorizations, self.objects = coordinator, authorizations, objects
        self.workspace_id, self.instance_id = workspace_id, instance_id
        self.resolver, self.verifier, self.protector = resolver, verifier, protector
        self.unit = coordinator._uow
        super().__init__(
            cast(RecordRepository, coordinator._records or self.unit),
            objects,
            workspace_id=workspace_id,
            protector=protector,
        )

    def _evidence_inputs(self, request: VerificationRequest, facts: ExecutionFacts) -> None:
        by_id = {item.evidence_id: item for item in facts.evidence_refs}
        for identity in request.evidence_refs:
            item = by_id.get(identity)
            if item is None or (item.project_id, item.run_id, item.attempt_id) != (
                facts.project_id,
                facts.run_id,
                request.verification_of,
            ):
                raise BusinessVerificationBlocked(
                    "requested evidence is outside the saved operation"
                )
            self.objects.read_bytes(
                StoredObjectRef(
                    item.project_id,
                    item.object_digest,
                    item.object_size,
                    item.media_type or "application/octet-stream",
                    f"objects/{item.project_id}/{item.object_digest.removeprefix('sha256:')}",
                )
            )

    def _missing_verification_declarations(self, scope: Mapping[str, object]) -> str:
        """C-03 缺口**自述**：说明哪些解析来源已冻结、哪些没有声明点。

        默认装配没有核验解析器/适配器（`resolver`/`verifier` 为 None）。只报"端口未配置"
        无法区分"能力没装"与"合同没定义声明点"，因此这里从**已在手的冻结材料**（准备投影）
        列出事实，并把分册没定义的部分点名（出处见消息里的引用）：

        - 已冻结：独立核验方式文本（`FrozenCase.independent_verification`，发布必填）、
          环境身份（`EnvironmentRefFact`）、目标部署身份与按用途凭据的**声明**位置
          （架构 01 §15 / 功能 §4）、只读查询配置形状（`HttpBusinessQuerySpec`：
          `target_deployment_ref`/`url_template` 的 `{business_object_id}` 槽/`object_id_field`/
          `credential_reference`/预算）；
        - **未定义声明点**按路径细分（能力清单 §6 与 §5）：

          * HTTP／数据库独立核验：`business_object_id`（业务对象标识）、`url_template`
            的登记来源、`expected_facts`（独立预期）；
          * 纯函数独立预期比对：只需 `expected_facts`——能力清单 §5 明确「python-library 的
            纯函数没有持久化时，数据库核验条目不适用但**独立预期比对仍适用**」。

          功能 §4 要求「数据来源/隔离/复位、**业务对象ID**、目标部署身份须可追溯」，而架构 01 §15
          的环境声明清单并列了"目标服务身份/部署版本"却**没有业务对象ID**——两册清单不一致，
          故本方法只如实点名，不自行选一种承载。

        读不到准备投影时退回基础消息；本方法只构造**诊断文本**，不改变拒绝类型与错误码。
        """

        base = "a trusted business query resolver/adapter is not configured"
        try:
            project, run, step = (
                str(scope[key]) for key in ("project_id", "run_id", "step_id")
            )
            _, _, prepared, _ = self.authorizations._context(project, run, step)
            case = next(
                (
                    item
                    for item in prepared.frozen_cases
                    if any(frozen.step_id == step for frozen in item.steps)
                ),
                None,
            )
        except Exception:
            return base
        environment = prepared.environment
        facts = {
            "case_id": None if case is None else case.case_id,
            "independent_verification_registered": (
                None if case is None else bool(case.independent_verification)
            ),
            "environment": f"{environment.environment_id}@{environment.revision}",
            "frozen_environment_fields": sorted(environment.model_fields),
            "http_or_database_verification_missing_declarations": [
                "business_object_id",
                "url_template",
                "expected_facts",
            ],
            "pure_function_comparison_missing_declarations": ["expected_facts"],
            "declaration_point_evidence": [
                "功能 §4：数据来源/隔离/复位、业务对象ID、目标部署身份须可追溯",
                "架构 01 §1.5：环境声明清单未列业务对象ID（只有目标服务身份/部署版本）",
                "能力与模板清单 §6 独立核验：输入同一业务对象标识和独立预期",
                "能力与模板清单 §5：纯函数仍适用独立预期比对",
            ],
        }
        return f"{base}；冻结材料核对：{json.dumps(facts, ensure_ascii=False, sort_keys=True)}"

    def _claim(
        self, identity: str, scope: Mapping[str, object], request_id: str
    ) -> Mapping[str, object]:
        if self.resolver is None or self.verifier is None:
            raise CapabilityUnavailable(self._missing_verification_declarations(scope))
        project, run, step, attempt_id = (
            str(scope[key]) for key in ("project_id", "run_id", "step_id", "attempt_id")
        )
        facts = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
        if (
            facts.run.origin_workspace_id,
            facts.snapshot_commit_id,
            facts.current_attempt_by_step.get(step),
        ) != (self.workspace_id, scope["base_snapshot_commit_id"], attempt_id):
            raise BusinessVerificationBlocked(
                "verification needs the exact current attempt/snapshot"
            )
        checkpoint = self.coordinator.read_checkpoint(project_id=project, attempt_id=attempt_id)
        if (
            checkpoint is None
            or checkpoint.attempt.run_id != run
            or checkpoint.attempt.step_id != step
        ):
            raise BusinessVerificationBlocked("the actual operation checkpoint is unavailable")
        attempt = checkpoint.attempt
        if attempt.authorization_ref is None:
            raise BusinessVerificationBlocked("the original operation authorization is unavailable")
        _, action = self.authorizations._origin(project, attempt.authorization_ref.authorization_id)
        if (
            self.coordinator.find_start(
                project_id=project,
                intent_id=attempt.intent_id,
                fingerprint=execution_start_fingerprint(action.attempt, action.request),
            )
            != attempt
        ):
            raise BusinessVerificationBlocked(
                "operation differs from its original execution intent"
            )
        _, _, prepared, _ = self.authorizations._context(project, run, step)
        request = freeze_verification_request(
            self.resolver.resolve(
                facts=facts.model_copy(deep=True), step_id=step, attempt_id=attempt_id
            )
        )
        if request.verification_of != attempt_id:
            raise BusinessVerificationBlocked(
                "resolved query does not verify the original operation"
            )
        self._evidence_inputs(request, facts)
        admission: dict[str, object] = {
            "schema_version": "aitest.business-query-intent/1.0",
            **scope,
            "snapshot_digest": execution_payload_digest(facts.model_dump(mode="json")),
            "request": _request_payload(request),
            "code_identity": CodeIdentityFact(
                binding_kind=SourceBindingKindFact(prepared.binding_form.value),
                workspace_ref=self.workspace_id,
                commit_id=prepared.git_base_commit,
                file_manifest_digest=prepared.plain_manifest_digest,
                revision_ref=prepared.snapshot.source_snapshot_id,
            ).model_dump(mode="json"),
            "source_instance_id": self.instance_id,
        }
        self._unchanged_safe(admission)
        self.unit.begin(request_id + "-admission", project)
        try:
            if (
                self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
                != facts
            ):
                raise BusinessVerificationBlocked(
                    "verification basis changed before query admission"
                )
            self.unit.stage_record(
                aggregate_kind="execution_intent",
                record_id=identity + "-admission",
                expected_revision=0,
                payload=admission,
            )
            self.unit.commit()
        except BaseException:
            self.unit.rollback()
            raise
        return admission

    def _material(
        self, admission: Mapping[str, object], capture: CapturedBusinessVerification
    ) -> dict[str, object]:
        request = _request(admission["request"])
        if not isinstance(capture, CapturedBusinessVerification):
            raise BusinessVerificationBlocked("query returned no actual capture material")
        result = EvidenceReviewService.validate(request, capture.verification)
        actual = freeze_json_value(capture.actual_fields)
        if actual is not None and not isinstance(actual, dict):
            raise BusinessVerificationBlocked("business query actual fields require a JSON object")
        if actual is None:
            if result.observation in {
                VerificationObservation.MATCHED,
                VerificationObservation.MISMATCHED,
            }:
                raise BusinessVerificationBlocked(
                    "a claimed observation lacks the actual query JSON"
                )
            unavailable: tuple[str, ...] = ()
            safe_actual = None
        else:
            observed, gaps = compare_business_fields(actual, request.expected_facts)
            if not capture.query_observations and (
                result.observation != observed or result.gap_ids != gaps
            ):
                raise BusinessVerificationBlocked(
                    "claimed observation disagrees with actual query JSON"
                )
            safe_actual = self._safe(actual)
            # Entire required field is unavailable if any nested part was filtered.
            unavailable = tuple(
                sorted(
                    key
                    for key in request.expected_facts
                    if key in actual
                    and (key not in safe_actual or not json_equal(actual[key], safe_actual[key]))
                )
            )
        material: dict[str, object] = {
            "schema_version": "aitest.business-query-material/1.0",
            "admission_digest": execution_payload_digest(admission),
            "actual_fields": safe_actual,
            "redacted": actual is not None and not json_equal(actual, safe_actual),
            "unavailable_fields": list(unavailable),
            "unavailable_observation": result.observation.value if actual is None else None,
            "unavailable_gap_ids": list(result.gap_ids) if actual is None else [],
            "captured_at": datetime.now(UTC).isoformat(),
        }
        if capture.query_observations:
            policy = query_polling_policy(request.query_interval, request.deadline_condition)
            if policy is None or type(capture.query_elapsed_ms) is not int:
                raise BusinessVerificationBlocked("query capture lacks its frozen polling policy")
            observation, gaps = derive_polled_query(
                policy,
                capture.query_observations,
                capture.query_elapsed_ms,
                request.expected_facts,
            )
            if (
                result.observation != observation
                or result.gap_ids != gaps
                or _bytes({"actual": capture.query_observations[-1].actual_fields})
                != _bytes({"actual": actual})
            ):
                raise BusinessVerificationBlocked("query capture contradicts its actual timeline")
            entries = []
            for item in capture.query_observations:
                fields = freeze_json_value(item.actual_fields)
                safe = self._safe(fields) if isinstance(fields, dict) else None
                redacted = not json_equal(fields, safe)
                missing = sorted(
                    key
                    for key in request.expected_facts
                    if isinstance(fields, dict)
                    and key in fields
                    and (safe is None or key not in safe or not json_equal(fields[key], safe[key]))
                )
                entries.append(
                    {
                        "started_after_ms": item.started_after_ms,
                        "completed_after_ms": item.completed_after_ms,
                        "actual_fields": safe,
                        "error_code": item.error_code,
                        "unavailable_fields": missing,
                        "redacted": redacted,
                    }
                )
            material["schema_version"] = "aitest.business-query-material/1.1"
            material["polling"] = {"observations": entries, "elapsed_ms": capture.query_elapsed_ms}
            material["redacted"] = any(item["redacted"] for item in entries)
            # Validate the safe material with the same reader before publication.
            policy, observations, elapsed = _polling(request, material["polling"])
            derive_polled_query(policy, observations, elapsed, request.expected_facts)
        elif capture.query_elapsed_ms is not None:
            raise BusinessVerificationBlocked("query capture has timing but no observations")
        elif actual is not None and request.deadline_condition.startswith("poll_deadline_ms:"):
            raise BusinessVerificationBlocked("polled query lacks its actual observation timeline")
        self._unchanged_safe(material)
        return material

    def _publish(
        self, identity: str, admission: Mapping[str, object], material: Mapping[str, object]
    ) -> Mapping[str, object]:
        content = _bytes(material)
        ref = self.objects.publish_bytes(
            str(admission["project_id"]), content, media_type="application/json"
        )
        if self.objects.read_bytes(ref) != content:
            raise BusinessVerificationBlocked("saved query bytes changed during publication")
        evidence, verification = self._derive(identity, admission, material, ref)
        project, run = str(admission["project_id"]), str(admission["run_id"])
        self.unit.begin("business-query-result-" + uuid4().hex, project)
        try:
            current = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
            snapshot = None
            if current == self._base(admission):
                updated = current.model_copy(
                    update={
                        "facts_id": identity + "-facts",
                        "committed_at": datetime.now(UTC),
                        "evidence_refs": (*current.evidence_refs, evidence),
                        "verifications": (*current.verifications, verification),
                    }
                )
                _, snapshot = self.coordinator._stage_snapshot(updated)
            self.unit.stage_record(
                aggregate_kind="evidence_ref",
                record_id=evidence.evidence_id,
                expected_revision=0,
                payload=_evidence_record(evidence),
            )
            self.unit.stage_record(
                aggregate_kind="verification",
                record_id=identity,
                expected_revision=0,
                payload={
                    "schema_version": "aitest.saved-business-query/1.0",
                    "project_id": project,
                    "admission_digest": execution_payload_digest(admission),
                    "object_ref": asdict(ref),
                    "evidence": evidence.model_dump(mode="json"),
                    "verification": verification.model_dump(mode="json"),
                    "status": "attached" if snapshot is not None else "historical_only",
                    "result_snapshot": snapshot.snapshot_commit_id
                    if snapshot is not None
                    else None,
                    "result_snapshot_digest": execution_payload_digest(
                        snapshot.model_dump(mode="json")
                    )
                    if snapshot is not None
                    else None,
                },
            )
            self.unit.commit()
        except BaseException:
            self.unit.rollback()
            raise
        result = self._recall(identity, admission)
        assert result is not None
        return result

    def apply(self, command: Command) -> Mapping[str, object]:
        project, intent = ExecutionCommands._identity(command)
        values = command.parameters
        if (
            command.action != "verify_pending"
            or set(values) != {"run_id", "step_id", "attempt_id", "base_snapshot_commit_id"}
            or any(not isinstance(x, str) or not x.strip() for x in values.values())
            or command.target != values.get("step_id")
        ):
            raise InvalidExecutionCommand(
                "verification requires only the exact saved operation and snapshot"
            )
        scope = {
            "workspace_id": self.workspace_id,
            "project_id": project,
            "intent_id": intent,
            **values,
        }
        identity = (
            "business-query-"
            + execution_payload_digest(
                {"workspace_id": self.workspace_id, "project_id": project, "intent_id": intent}
            )[7:]
        )
        admission = self._admission(identity, scope)
        if admission is not None:
            result = self._recall(identity, admission)
            if result is not None:
                return result
            raise BusinessVerificationBlocked(
                "original query outcome is unverified; a new query requires a new intent"
            )
        admission = self._claim(identity, scope, command.request_id)
        assert self.verifier is not None
        # Even a trusted adapter gets a separate copy of the frozen request.
        capture = self.verifier.capture(_request(admission["request"]))
        return self._publish(identity, admission, self._material(admission, capture))
