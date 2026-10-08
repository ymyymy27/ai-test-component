"""Frozen source binding and actual output bytes for default command evidence."""

import hashlib
from collections.abc import Mapping
from dataclasses import replace

from pydantic import TypeAdapter

from aitest.application.evidence.publication import EvidencePublicationContext, EvidencePublisher
from aitest.application.evidence.redaction_materials import (
    SavedRedactionMaterials,
    verify_redaction_material,
)
from aitest.application.execution.output_material import require_saved_output_material
from aitest.application.execution.run_record import read_run_record
from aitest.application.planning.preparation_origin import (
    load_saved_preparation,
    validate_preparation_origin,
)
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import (
    CollectedRedactionSummary,
    EvidenceObjectStore,
    RecordRepository,
    SpoolStore,
)
from aitest.domain.evidence.evidence import (
    CodeIdentity,
    EvidenceRef,
    RedactionState,
    StoredObjectRef,
)
from aitest.domain.execution.runs import RecoveryRecord
from aitest.domain.execution.sources import SourceBindingKind

_REF = TypeAdapter(EvidenceRef)


class SavedExecutionEvidence:
    def __init__(
        self, records: RecordRepository, preparations: RecordReader, spool: SpoolStore,
        objects: EvidenceObjectStore, *, workspace_id: str, instance_id: str,
    ) -> None:
        self.records, self.preparations, self.spool, self.objects = (
            records, preparations, spool, objects,
        )
        self.workspace_id, self.instance_id = workspace_id, instance_id
        self.publisher = EvidencePublisher(spool, objects)

    def _materials(
        self, project_id: str, checkpoint: RecoveryRecord, *, saved_only: bool,
        context: EvidencePublicationContext | None = None,
    ) -> tuple[CollectedRedactionSummary, ...]:
        if not any(block.redaction_summary_id for block in checkpoint.attempt.output_block_refs):
            return ()
        return SavedRedactionMaterials(
            self.records, self.spool, self.objects, self.workspace_id,
        ).materials(
            context or self._context(project_id, checkpoint), checkpoint, saved_only=saved_only,
        )

    @staticmethod
    def _summary_gaps(
        ref: EvidenceRef, materials: tuple[CollectedRedactionSummary, ...],
    ) -> EvidenceRef:
        if ref.redaction_summary_ref is None:
            return ref
        material = next(
            (item for item in materials if item.summary_id == ref.redaction_summary_ref), None,
        )
        if material is None:
            gap = "redaction_summary_provenance_unverified"
        elif material.summary.completeness != "complete":
            gap = "redaction_summary_incomplete"
        else:
            return ref
        return replace(ref, gap_ids=(*ref.gap_ids, gap))

    def redaction_materials(
        self, project_id: str, checkpoint: RecoveryRecord, references: tuple[EvidenceRef, ...],
    ) -> tuple[CollectedRedactionSummary, ...]:
        materials = self._materials(project_id, checkpoint, saved_only=False)
        for ref in references:
            base = replace(ref, gap_ids=tuple(gap for gap in ref.gap_ids if gap not in {
                "redaction_summary_provenance_unverified", "redaction_summary_incomplete",
            }))
            if self._summary_gaps(base, materials) != ref:
                raise ValueError("collected redaction summary changed before publication")
        return materials

    def validate_redaction_materials(
        self, materials: tuple[CollectedRedactionSummary, ...],
    ) -> None:
        for material in materials:
            verify_redaction_material(
                identity=material.summary_id, raw=material.payload, objects=self.objects,
                basis={key: value for key, value in material.payload.items()
                       if key not in {"summary", "object_ref"}},
            )

    def _context(self, project: str, checkpoint: RecoveryRecord) -> EvidencePublicationContext:
        from aitest.application.execution.registration import _initial_domain

        attempt = checkpoint.attempt
        record = self.records.read(aggregate_kind="run", record_id=attempt.run_id, revision=1)
        if (getattr(record, "aggregate_kind", None), getattr(record, "record_id", None),
            getattr(record, "revision", None)) != ("run", attempt.run_id, 1) or (
                type(getattr(record, "revision", None)) is not int
        ):
            raise ValueError("output evidence original run envelope cannot be verified")
        run = read_run_record(getattr(record, "payload", {}))
        if (run.project_id, run.origin_workspace_id, checkpoint.project_id) != (
            project, self.workspace_id, project,
        ) or len(run.frozen_input_refs) != 1:
            raise ValueError("output evidence original source scope cannot be verified")
        prepared, digest = load_saved_preparation(
            reader=self.preparations, project_id=project, workspace_id=self.workspace_id,
            prepared_run_id=run.frozen_input_refs[0].input_ref_id,
        )
        validate_preparation_origin(prepared, reader=self.preparations)
        original, steps = _initial_domain(prepared, run.run_id, digest)
        if run != original or attempt.step_id not in {step.step_id for step in steps}:
            raise ValueError("output evidence differs from its frozen original run")
        # This binds evidence to frozen code; source verification remains a separate fact.
        identity = CodeIdentity(
            binding_kind=SourceBindingKind(prepared.binding_form.value),
            workspace_ref=f"{prepared.binding_id}@{prepared.binding_revision}",
            commit_id=prepared.git_base_commit,
            file_manifest_digest=prepared.snapshot.content_identity,
            revision_ref=(
                f"source_snapshot:{prepared.snapshot.source_snapshot_id}"
                f"@{prepared.snapshot.record_revision}"
            ),
        )
        return EvidencePublicationContext(
            project, self.instance_id, attempt.run_id,
            attempt.step_id, attempt.attempt_id, identity,
        )

    def _saved(self, identity: str) -> EvidenceRef | None:
        revision = self.records.current_revision(aggregate_kind="evidence_ref", record_id=identity)
        if type(revision) is not int or revision < 0:
            raise ValueError("output evidence warehouse revision cannot be verified")
        if not revision:
            return None
        record = self.records.read(
            aggregate_kind="evidence_ref", record_id=identity, revision=revision,
        )
        if (getattr(record, "aggregate_kind", None), getattr(record, "record_id", None),
            getattr(record, "revision", None)) != ("evidence_ref", identity, revision) or (
                type(getattr(record, "revision", None)) is not int
        ):
            raise ValueError("output evidence saved envelope cannot be verified")
        raw = getattr(record, "payload", None)
        if not isinstance(raw, Mapping):
            raise ValueError("output evidence saved body cannot be verified")
        try:
            ref = _REF.validate_python(raw)
        except ValueError as exc:
            raise ValueError("output evidence saved body cannot be verified") from exc
        if (
            ref.evidence_id != identity or ref.evidence_revision != revision
            or payload_digest(raw) != payload_digest(_REF.dump_python(ref, mode="json"))
        ):
            raise ValueError("output evidence saved body cannot be verified")
        return ref

    def collect(self, project_id: str, checkpoint: RecoveryRecord) -> tuple[EvidenceRef, ...]:
        attempt = checkpoint.attempt
        if not attempt.output_block_refs:
            return ()
        context = self._context(project_id, checkpoint)
        require_saved_output_material(self.spool, attempt, attempt.output_block_refs)
        materials = self._materials(project_id, checkpoint, saved_only=False, context=context)
        result = []
        for ref in self.publisher.publish_blocks(context, attempt.output_block_refs):
            ref = self._summary_gaps(ref, materials)
            old = self._saved(ref.evidence_id)
            if old is not None:
                ref = replace(
                    ref, source_instance_id=old.source_instance_id, created_at=old.created_at,
                )
                if replace(ref, evidence_revision=old.evidence_revision) == old:
                    result.append(old)
                    continue
                allowed = replace(
                    ref, evidence_revision=old.evidence_revision,
                    integrity=old.integrity, gap_ids=old.gap_ids,
                )
                if (
                    ref.redaction_summary_ref is None and old.redaction_summary_ref is None
                    and old.redaction_state is RedactionState.NOT_REQUIRED
                    and ref.redaction_state is RedactionState.UNKNOWN
                ):
                    allowed = replace(allowed, redaction_state=old.redaction_state)
                if allowed != old:
                    raise ValueError("output evidence identity or saved byte basis conflicts")
                ref = replace(ref, evidence_revision=old.evidence_revision + 1)
            result.append(ref)
        return tuple(result)

    def existing(
        self, project_id: str, checkpoint: RecoveryRecord,
    ) -> tuple[EvidenceRef, ...] | None:
        if not checkpoint.attempt.output_block_refs:
            return ()
        context = self._context(project_id, checkpoint)
        materials = self._materials(project_id, checkpoint, saved_only=True, context=context)
        # A reliable old checkpoint may gain its actual summary. Force the same-commit
        # repair instead of treating a still-unknown old projection as complete replay.
        ids = {block.redaction_summary_id for block in checkpoint.attempt.output_block_refs
               if block.redaction_summary_id is not None}
        if (
            ids != {material.summary_id for material in materials}
            and self._materials(
                project_id, checkpoint, saved_only=False, context=context,
            ) != materials
        ):
            return None
        result = []
        for block in checkpoint.attempt.output_block_refs:
            desired = self.publisher.reference_for_block(context, block)
            desired = self._summary_gaps(desired, materials)
            old = self._saved(desired.evidence_id)
            if old is None:
                return None
            desired = replace(
                desired, source_instance_id=old.source_instance_id, created_at=old.created_at,
                evidence_revision=old.evidence_revision,
            )
            if desired != old:
                return None
            result.append(old)
        references = tuple(result)
        self.validate(references)
        return references

    def validate(self, references: tuple[EvidenceRef, ...]) -> None:
        for ref in references:
            content = self.objects.read_bytes(StoredObjectRef(
                ref.project_id, ref.object_digest, ref.object_size,
                ref.media_type or "application/octet-stream",
                f"objects/{ref.project_id}/{ref.object_digest.removeprefix('sha256:')}",
            ))
            if (
                type(content) is not bytes or len(content) != ref.object_size
                or "sha256:" + hashlib.sha256(content).hexdigest() != ref.object_digest
            ):
                raise ValueError("saved output evidence bytes cannot be verified")
