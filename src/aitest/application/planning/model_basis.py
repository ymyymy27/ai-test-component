"""Frozen model source/manual identities; external source observation stays outside UOW."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from aitest.application.planning.draft import text_digest
from aitest.application.planning.substrate import RecordReader, current_record
from aitest.application.project.persistence import load_source_snapshot
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.contracts.prepared_run import SnapshotRef
from aitest.domain.planning.model_outbound import ResponseCurrency, response_currency_from_facts
from aitest.domain.project.context import source_content_identity


def _digest(value: Mapping[str, object]) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class ModelGenerationBasis:
    """A source integer alone is not a source identity or a currentness proof."""

    def __init__(
        self,
        *,
        reader: RecordReader,
        project_id: str,
        source_revision: int,
        base_manual_revision: int,
        source_ref: object = None,
        manual_ref: object = None,
        sources: SourceAnalysisService | None = None,
    ) -> None:
        if (
            type(source_revision) is not int
            or source_revision < 0
            or type(base_manual_revision) is not int
            or base_manual_revision < 0
        ):
            raise ValueError("model basis revisions must be nonnegative integers")
        self.reader, self.project_id, self.sources = reader, project_id, sources
        self.source: SnapshotRef | None = None
        self.source_digest: str | None = None
        self.binding_id: str | None = None
        self.binding_revision: int | None = None
        self.binding_digest: str | None = None
        self.manual: dict[str, object] | None = None
        self.manual_digest: str | None = None
        self.manual_revision = base_manual_revision
        self.source_unchanged = source_revision == 0
        if source_revision == 0:
            if source_ref is not None:
                raise ValueError("a non-applicable source cannot carry a source reference")
        else:
            if (
                not isinstance(source_ref, Mapping)
                or type(source_ref.get("record_revision")) is not int
            ):
                raise ValueError("a model source requires an exact frozen snapshot reference")
            ref = SnapshotRef.model_validate(source_ref)
            if ref.record_revision != source_revision or ref.purpose not in {"analysis", "prepare"}:
                raise ValueError("model source reference revision/purpose differs")
            saved = reader.read(
                aggregate_kind="source_snapshot",
                record_id=ref.source_snapshot_id,
                revision=ref.record_revision,
            )
            if (
                saved.payload.get("project_id") != project_id
                or saved.payload.get("purpose") != ref.purpose
            ):
                raise ValueError("model source ownership/purpose cannot be verified")
            manifest = load_source_snapshot(
                reader,
                project_id=project_id,
                snapshot_id=ref.source_snapshot_id,
                revision=ref.record_revision,
            )
            if (
                saved.payload.get("content_identity") != ref.content_identity
                or source_content_identity(manifest) != ref.content_identity
            ):
                raise ValueError("model source content identity cannot be verified")
            binding_id, binding_revision = (
                saved.payload.get("binding_id"),
                saved.payload.get("binding_revision"),
            )
            if (
                not isinstance(binding_id, str)
                or type(binding_revision) is not int
                or binding_revision < 1
            ):
                raise ValueError("model source binding cannot be verified")
            bound = reader.read(
                aggregate_kind="binding", record_id=binding_id, revision=binding_revision
            )
            if bound.payload.get("project_id") != project_id:
                raise ValueError("model source binding belongs to another project")
            self.source, self.source_digest = ref, _digest(saved.payload)
            self.binding_id, self.binding_revision, self.binding_digest = (
                binding_id,
                binding_revision,
                _digest(bound.payload),
            )
        if manual_ref is None:
            if base_manual_revision != 0:
                raise ValueError("a manual basis requires an exact saved content reference")
        else:
            if not isinstance(manual_ref, Mapping) or set(manual_ref) != {
                "record_id",
                "record_revision",
                "content_digest",
            }:
                raise ValueError("manual basis reference fields cannot be verified")
            record_id, revision = manual_ref.get("record_id"), manual_ref.get("record_revision")
            if (
                not isinstance(record_id, str)
                or not record_id.strip()
                or type(revision) is not int
                or revision != base_manual_revision
            ):
                raise ValueError("manual basis identity/revision differs")
            self.manual = dict(manual_ref)
            if revision == 0:
                if manual_ref.get("content_digest") is not None:
                    raise ValueError("an absent manual basis cannot have a content digest")
            else:
                saved_manual = reader.read(
                    aggregate_kind="generated_content", record_id=record_id, revision=revision
                )
                text = saved_manual.payload.get("draft_text")
                if (
                    saved_manual.payload.get("project_id") != project_id
                    or saved_manual.payload.get("generated_content_id") != record_id
                    or not isinstance(text, str)
                    or text_digest(text) is None
                    or text_digest(text) != manual_ref.get("content_digest")
                    or saved_manual.payload.get("content_digest")
                    != manual_ref.get("content_digest")
                ):
                    raise ValueError("manual basis ownership/content cannot be verified")
                self.manual_digest = _digest(saved_manual.payload)

    def identity(self) -> dict[str, object]:
        return {
            "schema_version": "aitest.model-generation-basis/1.0",
            "source_ref": self.source.model_dump(mode="json") if self.source else None,
            "source_record_digest": self.source_digest,
            "binding_ref": {
                "record_id": self.binding_id,
                "record_revision": self.binding_revision,
                "body_digest": self.binding_digest,
            }
            if self.source
            else None,
            "manual_ref": self.manual,
            "manual_record_digest": self.manual_digest,
        }

    def refresh_source(self) -> None:
        """Perform actual file/Git and pinned-material reads outside a write transaction."""
        if self.source is None:
            return
        self.source_unchanged = False
        if self.sources is None:
            return
        try:
            result = self.sources.check(
                project_id=self.project_id,
                snapshot_id=self.source.source_snapshot_id,
                revision=self.source.record_revision,
            )
            changes = result.get("changes")
            self.source_unchanged = (
                result.get("content_identity") == self.source.content_identity
                and isinstance(changes, Mapping)
                and changes.get("state") == "unchanged"
                and result.get("binding_state") == "unchanged"
                and result.get("git_state") in {"unchanged", "not_applicable"}
            )
        except (ValueError, TypeError, OSError, RuntimeError):
            # Capability/read/ownership uncertainty never makes the response current.
            self.source_unchanged = False

    def currency(self, *, other_basis_current: bool = True) -> ResponseCurrency:
        """Only saved records are checked here; safe to repeat under the short UOW."""
        try:
            if self.manual is not None:
                current = current_record(
                    self.reader,
                    project_id=self.project_id,
                    aggregate_kind="generated_content",
                    record_id=str(self.manual["record_id"]),
                )
                expected = self.manual_revision
                if current is not None and current.revision > expected:
                    return response_currency_from_facts(manual_advanced=True, source_matches=False)
                if expected == 0 and current is None:
                    pass
                elif (
                    current is None
                    or current.revision != expected
                    or _digest(current.payload) != self.manual_digest
                ):
                    return response_currency_from_facts(manual_advanced=False, source_matches=False)
            if self.source is not None:
                current = current_record(
                    self.reader,
                    project_id=self.project_id,
                    aggregate_kind="source_snapshot",
                    record_id=self.source.source_snapshot_id,
                )
                bound = current_record(
                    self.reader,
                    project_id=self.project_id,
                    aggregate_kind="binding",
                    record_id=str(self.binding_id),
                )
                if (
                    current is None
                    or current.revision != self.source.record_revision
                    or _digest(current.payload) != self.source_digest
                    or bound is None
                    or bound.revision != self.binding_revision
                    or _digest(bound.payload) != self.binding_digest
                ):
                    return response_currency_from_facts(manual_advanced=False, source_matches=False)
        except (ValueError, TypeError, OSError, RuntimeError):
            return response_currency_from_facts(manual_advanced=False, source_matches=False)
        return response_currency_from_facts(
            manual_advanced=False, source_matches=self.source_unchanged and other_basis_current
        )
