"""Bind actual pinned bytes to B's saved, project-owned source identity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from aitest.application.errors import CapabilityUnavailable
from aitest.application.planning.substrate import RecordReader, current_record
from aitest.application.ports import (
    SourceControlPort,
    SourceSnapshotPort,
    StageableWorkspaceUnitOfWork,
)
from aitest.domain.project.context import (
    LocalProjectBinding,
    SourceFileDigest,
    SourceForm,
    SourceManifest,
)

from .persistence import load_source_snapshot
from .serialization import binding_from_payload, source_manifest_to_payload


class SourceAnalysisError(ValueError):
    code = "B_SOURCE_UNVERIFIED"


class SourceIntentConflict(SourceAnalysisError):
    code = "INTENT_CONFLICT"


def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _strings(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or any(not isinstance(v, str) or not v for v in values):
        raise SourceAnalysisError("source selection must contain nonempty strings")
    return tuple(values)


def _metadata_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or any(not isinstance(v, str) for v in value):
        raise SourceAnalysisError("source metadata string list is invalid")
    return tuple(value)


class SourceAnalysisService:
    def __init__(
        self,
        *,
        reader: RecordReader,
        unit_of_work: StageableWorkspaceUnitOfWork,
        snapshots: SourceSnapshotPort,
        source_control: SourceControlPort | None,
        source_available: Callable[[], bool] | None = None,
    ) -> None:
        self.reader, self.unit = reader, unit_of_work
        self.snapshots, self.source_control = snapshots, source_control
        self.source_available = source_available

    def analyze(
        self,
        *,
        project_id: str,
        request_id: str,
        intent_id: str,
        binding_id: str,
        binding_revision: int,
        expected_revision: int,
        purpose: str,
        source_scope: str,
        selected_paths: Sequence[str] = (),
        exclusion_rules: Sequence[str] = (),
        refetch_dependencies: Sequence[str] = (),
        refetch_scope: str | None = None,
    ) -> Mapping[str, object]:
        if purpose not in {"analysis", "prepare"} or not source_scope:
            raise SourceAnalysisError("source purpose or scope is invalid")
        selection, exclusions = _strings(selected_paths), _strings(exclusion_rules)
        dependencies = _strings(refetch_dependencies)
        inputs = {
            "project_id": project_id,
            "binding_id": binding_id,
            "binding_revision": binding_revision,
            "expected_revision": expected_revision,
            "purpose": purpose,
            "source_scope": source_scope,
            "selected_paths": selection,
            "exclusion_rules": exclusions,
            "refetch_dependencies": dependencies,
            "refetch_scope": refetch_scope,
        }
        fingerprint = _digest(inputs)
        operation_id = "source-intent-" + _digest([project_id, intent_id])[7:]
        previous = self._original(project_id, operation_id, fingerprint)
        if previous is not None:
            return previous
        self._require_source()
        if (
            current_record(
                self.reader, project_id=project_id, aggregate_kind="project", record_id=project_id
            )
            is None
        ):
            raise SourceAnalysisError("source analysis requires a registered project")
        saved_binding = current_record(
            self.reader, project_id=project_id, aggregate_kind="binding", record_id=binding_id
        )
        if saved_binding is None or saved_binding.revision != binding_revision:
            raise SourceAnalysisError("current binding repository revision differs")
        binding = binding_from_payload(saved_binding.payload)
        if purpose == "prepare" and not binding.confirmed:
            raise SourceAnalysisError("preparation source requires confirmed binding")
        git = self._git_identity(binding)
        try:
            pinned = self.snapshots.pin(
                canonical_path=binding.canonical_path,
                purpose=purpose,
                selected_paths=selection,
                exclusion_rules=exclusions,
            )
            technical_id = pinned.get("snapshot_id")
            if not isinstance(technical_id, str) or not technical_id:
                raise SourceAnalysisError("source port returned no stable snapshot identity")
            reread = self._verified_pinned(technical_id)
            if (
                _digest(reread) != _digest(pinned)
                or self.snapshots.detect_changes(technical_id).get("state") != "unchanged"
            ):
                raise SourceAnalysisError("source changed during pinning")
            if (
                pinned.get("purpose") != purpose
                or Path(str(pinned.get("canonical_path"))).resolve()
                != Path(binding.canonical_path).resolve()
                or _metadata_strings(pinned.get("selected_paths")) != tuple(sorted(set(selection)))
            ):
                raise SourceAnalysisError("source port returned a different binding or selection")
        except (OSError, RuntimeError, ValueError) as error:
            raise SourceAnalysisError("actual source material could not be verified") from error
        if self._git_identity(binding) != git:
            raise SourceAnalysisError("git source changed during pinning")
        files = self._files(pinned)
        file_digest = _digest(
            [
                {
                    "relative_path": f.relative_path,
                    "size": f.size,
                    "content_digest": f.content_digest,
                }
                for f in sorted(files, key=lambda f: f.relative_path)
            ]
        )
        manifest = SourceManifest(
            source_scope=source_scope,
            source_form=SourceForm.GIT if git else SourceForm.PLAIN,
            manifest_digest=None if git else file_digest,
            git_base_commit=git["head_commit"] if git else None,
            git_diff_digest=git["git_diff_digest"] if git else None,
            files=files,
            exclusion_rules=_metadata_strings(pinned["exclusion_rules"]),
            refetch_dependencies=dependencies,
            refetch_scope=refetch_scope,
        )
        snapshot_id = (
            "source-" + _digest([project_id, binding_id, binding_revision, technical_id])[7:]
        )
        payload = source_manifest_to_payload(
            manifest, project_id=project_id, snapshot_id=snapshot_id, purpose=purpose
        )
        payload.update(
            binding_id=binding_id,
            binding_revision=binding_revision,
            pinned_snapshot_id=technical_id,
            pinned_manifest_digest=_digest(pinned),
            selected_paths=list(selection),
        )
        current_id = "source-current-" + _digest([project_id, binding_id])[7:]
        result = {
            "snapshot_id": snapshot_id,
            "record_revision": 1,
            "content_identity": payload["content_identity"],
            "pinned_snapshot_id": technical_id,
            "binding_id": binding_id,
            "binding_revision": binding_revision,
            "purpose": purpose,
        }
        self.unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            previous = self._original(project_id, operation_id, fingerprint)
            if previous is not None:
                self.unit.rollback(request_id)
                return previous
            latest_binding = current_record(
                self.reader, project_id=project_id, aggregate_kind="binding", record_id=binding_id
            )
            if latest_binding != saved_binding:
                raise SourceAnalysisError("binding changed before source publication")
            current = current_record(
                self.reader,
                project_id=project_id,
                aggregate_kind="source_binding_current",
                record_id=current_id,
            )
            revision = current.revision if current is not None else 0
            if revision != expected_revision:
                raise SourceAnalysisError("current source pointer revision changed")
            saved = current_record(
                self.reader,
                project_id=project_id,
                aggregate_kind="source_snapshot",
                record_id=snapshot_id,
            )
            if saved is None:
                self.unit.stage_record(
                    aggregate_kind="source_snapshot",
                    record_id=snapshot_id,
                    expected_revision=0,
                    payload=payload,
                )
            elif saved.revision != 1 or dict(saved.payload) != payload:
                raise SourceAnalysisError("immutable source identity conflicts")
            self.unit.stage_record(
                aggregate_kind="source_binding_current",
                record_id=current_id,
                expected_revision=revision,
                payload={"project_id": project_id, **result},
            )
            self.unit.stage_record(
                aggregate_kind="source_pin_intent",
                record_id=operation_id,
                expected_revision=0,
                payload={
                    "project_id": project_id,
                    "digest": fingerprint,
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "result": result,
                },
            )
            self.unit.commit(request_id)
        except BaseException:
            self.unit.rollback(request_id)
            raise
        return result

    def _original(
        self,
        project_id: str,
        record_id: str,
        fingerprint: str,
    ) -> Mapping[str, object] | None:
        saved = current_record(
            self.reader,
            project_id=project_id,
            aggregate_kind="source_pin_intent",
            record_id=record_id,
        )
        if saved is None:
            return None
        if saved.revision != 1 or saved.payload.get("digest") != fingerprint:
            raise SourceIntentConflict("source intent has different frozen input")
        result = saved.payload.get("result")
        if not isinstance(result, Mapping):
            raise SourceAnalysisError("original source intent result is unreadable")
        return {**result, "reused": True}

    def _git_identity(self, binding: LocalProjectBinding) -> Mapping[str, Any] | None:
        if binding.is_plain:
            return None
        if self.source_control is None:
            raise SourceAnalysisError("git source identity capability is unavailable")
        try:
            facts = self.source_control.snapshot_identity(Path(binding.canonical_path))
        except (OSError, RuntimeError, ValueError) as error:
            raise SourceAnalysisError("git source identity could not be verified") from error
        if (
            facts.get("is_repository") is not True
            or facts.get("branch") != binding.branch
            or facts.get("head_commit") != binding.base_commit
            or not isinstance(facts.get("git_diff_digest"), str)
        ):
            raise SourceAnalysisError("actual Git branch or HEAD differs from frozen binding")
        # Local repository identity is the exact bound root; optional remotes are
        # advisory and cannot replace it or silently turn the binding into plain.
        return facts

    @staticmethod
    def _files(pinned: Mapping[str, object]) -> tuple[SourceFileDigest, ...]:
        raw = pinned.get("files")
        if not isinstance(raw, list):
            raise SourceAnalysisError("pinned source lacks its file manifest")
        files = []
        for item in raw:
            if (
                not isinstance(item, Mapping)
                or type(item.get("size")) is not int
                or not isinstance(item.get("relative_path"), str)
                or not isinstance(item.get("sha256"), str)
                or len(item["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in item["sha256"])
            ):
                raise SourceAnalysisError("pinned source file identity is invalid")
            files.append(
                SourceFileDigest(item["relative_path"], item["size"], "sha256:" + item["sha256"])
            )
        return tuple(files)

    def check(self, *, project_id: str, snapshot_id: str, revision: int) -> Mapping[str, object]:
        self._require_source()
        try:
            return self._check(project_id=project_id, snapshot_id=snapshot_id, revision=revision)
        except SourceAnalysisError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            raise SourceAnalysisError("saved source material cannot be verified") from error

    def _check(self, *, project_id: str, snapshot_id: str, revision: int) -> Mapping[str, object]:
        saved = self.reader.read(
            aggregate_kind="source_snapshot", record_id=snapshot_id, revision=revision
        )
        if saved.payload.get("project_id") != project_id:
            raise SourceAnalysisError("source snapshot belongs to another project")
        technical_id = saved.payload.get("pinned_snapshot_id")
        if not isinstance(technical_id, str):
            raise SourceAnalysisError("legacy snapshot requires new preparation")
        pinned = self._verified_pinned(technical_id)
        if _digest(pinned) != saved.payload.get("pinned_manifest_digest"):
            raise SourceAnalysisError("pinned source manifest differs from saved binding")
        manifest = load_source_snapshot(
            self.reader, project_id=project_id, snapshot_id=snapshot_id, revision=revision
        )
        if manifest.normalized_files() != tuple(
            sorted(self._files(pinned), key=lambda f: f.relative_path)
        ):
            raise SourceAnalysisError("business source manifest differs from actual pinned bytes")
        binding_id = saved.payload.get("binding_id")
        binding_revision = saved.payload.get("binding_revision")
        if not isinstance(binding_id, str) or type(binding_revision) is not int:
            raise SourceAnalysisError("source binding reference is unverified")
        bound = self.reader.read(
            aggregate_kind="binding", record_id=binding_id, revision=binding_revision
        )
        if bound.payload.get("project_id") != project_id:
            raise SourceAnalysisError("source binding belongs to another project")
        binding = binding_from_payload(bound.payload)
        current = current_record(
            self.reader, project_id=project_id, aggregate_kind="binding", record_id=binding_id
        )
        binding_state = "unchanged" if current == bound else "changed"
        git_state = "not_applicable"
        if binding.is_git:
            try:
                actual = self._git_identity(binding)
                git_state = (
                    "unchanged"
                    if actual and actual["git_diff_digest"] == manifest.git_diff_digest
                    else "changed"
                )
            except SourceAnalysisError:
                git_state = "unverified"
        return {
            "snapshot_id": snapshot_id,
            "record_revision": revision,
            "content_identity": saved.payload["content_identity"],
            "changes": dict(self.snapshots.detect_changes(technical_id)),
            "binding_state": binding_state,
            "git_state": git_state,
        }

    def _verified_pinned(self, snapshot_id: str) -> Mapping[str, object]:
        verify = getattr(self.snapshots, "verify_pinned", None)
        if not callable(verify):
            raise SourceAnalysisError("pinned source byte verification capability is unavailable")
        result = verify(snapshot_id)
        if not isinstance(result, Mapping):
            raise SourceAnalysisError("pinned source verifier returned no original manifest")
        return result

    def _require_source(self) -> None:
        if self.source_available is not None and not self.source_available():
            raise CapabilityUnavailable("actual source capability is unavailable or paused")
