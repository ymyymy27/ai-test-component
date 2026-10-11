"""Resolve actual carrier observations and recheck exact saved input authority."""

from __future__ import annotations

from typing import Any

from aitest.application.planning.publish import payload_digest
from aitest.application.planning.substrate import AggregateKind, RecordReader, require_scoped_record
from aitest.application.ports import EnvironmentResolutionRequest, EnvironmentResolver
from aitest.application.project.serialization import binding_from_payload, environment_from_payload
from aitest.contracts.prepared_run import EnvironmentRefFact, PreparedRun


class EnvironmentResolutionBlocked(ValueError):
    """环境未登记/探测不可用时**不可重试**阻塞（code 取 DEC-012 已登记值）。"""

    code = "SOURCE_BINDING_UNVERIFIED"
    reason = "environment_unregistered"


class EnvironmentResolutionService:
    def __init__(self, *, reader: RecordReader, resolver: EnvironmentResolver) -> None:
        self.reader, self.resolver = reader, resolver

    def _read(
        self, project: str, kind: AggregateKind, identity: str, revision: int
    ) -> dict[str, Any]:
        saved = self.reader.read(aggregate_kind=kind, record_id=identity, revision=revision)
        if (
            (saved.aggregate_kind, saved.record_id, saved.revision) != (kind, identity, revision)
            or type(saved.revision) is not int
            or saved.payload.get("project_id") != project
        ):
            raise ValueError("environment resolution input envelope or project differs")
        require_scoped_record(
            saved, project_id=project, aggregate_kind=kind, record_id=identity, revision=revision
        )
        return dict(saved.payload)

    def resolve(
        self,
        *,
        project_id: str,
        binding_id: str,
        binding_revision: int,
        environment_id: str,
        environment_revision: int,
    ) -> EnvironmentRefFact:
        binding_payload = self._read(project_id, "binding", binding_id, binding_revision)
        environment_payload = self._read(
            project_id, "environment", environment_id, environment_revision
        )
        binding = binding_from_payload(binding_payload)
        environment = environment_from_payload(environment_payload)
        if not binding.confirmed or binding.binding_id != binding_id:
            raise ValueError("environment requires an exact confirmed binding")
        if environment.environment_id != environment_id:
            raise ValueError("environment declaration identity differs")
        try:
            observed = self.resolver.resolve(
                EnvironmentResolutionRequest(
                    project_id,
                    environment_id,
                    environment.isolation_mode.value,
                    environment.interpreter_requirement,
                )
            )
        except ValueError as error:
            # 载体未登记/探测不可用属"环境未登记"阻塞（DEC-012 已登记该错误码与 reason），
            # 不能让没有 code 的 ValueError 经协议层显示为 INTERNAL_ERROR。
            raise EnvironmentResolutionBlocked(str(error)) from error
        detail = observed.resolution
        if (
            detail is None
            or observed.environment_id != environment_id
            or observed.isolation_mode.value != environment.isolation_mode.value
            or observed.interpreter_identity != detail.interpreter_identity
            or observed.dependency_set_digest != detail.dependency_set_digest
        ):
            raise ValueError("registered environment returned inconsistent resolution facts")
        detail = detail.model_copy(
            update={
                "project_id": project_id,
                "binding_id": binding_id,
                "binding_revision": binding_revision,
                "binding_record_digest": payload_digest(binding_payload),
                "environment_id": environment_id,
                "environment_revision": environment_revision,
                "environment_record_digest": payload_digest(environment_payload),
            }
        )
        fact = observed.model_copy(
            update={
                "revision": environment_revision,
                "resolution": detail,
                "interpreter_identity": detail.interpreter_identity,
            }
        )
        self.validate_saved_inputs(
            project_id=project_id,
            binding_id=binding_id,
            binding_revision=binding_revision,
            environment=fact,
        )
        return fact

    def validate_saved_inputs(
        self,
        *,
        project_id: str,
        binding_id: str,
        binding_revision: int,
        environment: EnvironmentRefFact,
    ) -> None:
        """No process or filesystem probe under the UOW lock."""
        detail = environment.resolution
        if (
            detail is None
            or (
                detail.project_id,
                detail.binding_id,
                detail.binding_revision,
                detail.environment_id,
                detail.environment_revision,
            )
            != (
                project_id,
                binding_id,
                binding_revision,
                environment.environment_id,
                environment.revision,
            )
            or environment.interpreter_identity != detail.interpreter_identity
            or environment.dependency_set_digest != detail.dependency_set_digest
            or detail.binding_record_digest
            != payload_digest(self._read(project_id, "binding", binding_id, binding_revision))
            or detail.environment_record_digest
            != payload_digest(
                self._read(
                    project_id, "environment", environment.environment_id, environment.revision
                )
            )
        ):
            raise ValueError("saved inputs do not prove the exact environment resolution")

    def validate_current(self, prepared: PreparedRun) -> None:
        self.validate_saved_inputs(
            project_id=prepared.project_id,
            binding_id=prepared.binding_id,
            binding_revision=prepared.binding_revision,
            environment=prepared.environment,
        )
        observed = self.resolve(
            project_id=prepared.project_id,
            binding_id=prepared.binding_id,
            binding_revision=prepared.binding_revision,
            environment_id=prepared.environment.environment_id,
            environment_revision=prepared.environment.revision,
        )
        if observed != prepared.environment:
            raise ValueError("actual environment changed; prepare again")
