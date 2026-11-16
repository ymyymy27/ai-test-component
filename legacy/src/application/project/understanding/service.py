"""项目理解服务：扫描、持久化与按源码读取。"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from aitest.application.planning.substrate import (
    IndexMaintenanceRequired,
    RecordQuery,
    RecordReader,
    UnitOfWork,
    current_record,
    transaction,
)
from aitest.application.ports import SourceSnapshotPort
from aitest.application.project.understanding.codec import (
    _rebase,
    _require_text,
    understanding_from_payload,
    understanding_identity,
    understanding_summary,
    understanding_to_payload,
)
from aitest.application.project.understanding.errors import ProjectUnderstandingError
from aitest.application.project.understanding.scanner import scan_sources
from aitest.application.project.understanding.shared import (
    _DIGEST_PREFIX,
    PROJECT_UNDERSTANDING_AGGREGATE,
)
from aitest.domain.planning.understanding import (
    ProjectUnderstanding,
)


class ProjectUnderstandingService:
    """Scan one pinned snapshot and persist the versioned understanding."""

    def __init__(
        self,
        *,
        reader: RecordReader,
        unit_of_work: UnitOfWork,
        snapshots: SourceSnapshotPort,
        workspace_id: str | None = None,
    ) -> None:
        self.reader = reader
        self.unit = unit_of_work
        self.snapshots = snapshots
        self.workspace_id = workspace_id

    def understand(
        self,
        *,
        project_id: str,
        source_snapshot_id: str,
        pinned_snapshot_id: str,
        scope: str,
        selected_paths: Sequence[str] = (),
    ) -> Mapping[str, object]:
        """Return the summary for this source snapshot, writing only if new.

        The same ``source_snapshot_id`` is an idempotent read. A different
        source snapshot writes a new revision. Scanning happens before the
        short write transaction so no half-written understanding can exist.
        """
        try:
            return self._understand(
                project_id=project_id,
                source_snapshot_id=source_snapshot_id,
                pinned_snapshot_id=pinned_snapshot_id,
                scope=scope,
                selected_paths=selected_paths,
            )
        except ProjectUnderstandingError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            raise ProjectUnderstandingError(
                f"project understanding could not be verified: {error}"
            ) from error

    def _understand(
        self,
        *,
        project_id: str,
        source_snapshot_id: str,
        pinned_snapshot_id: str,
        scope: str,
        selected_paths: Sequence[str],
    ) -> Mapping[str, object]:
        for value, name in (
            (project_id, "project_id"),
            (source_snapshot_id, "source_snapshot_id"),
            (pinned_snapshot_id, "pinned_snapshot_id"),
            (scope, "scope"),
        ):
            _require_text(value, name)
        workspace_id = self._workspace_id(project_id)
        identity = understanding_identity(project_id, scope)
        current = self._current(project_id, identity, workspace_id)
        if current is not None and current.source_snapshot_id == source_snapshot_id:
            return understanding_summary(current)
        historical = self._by_source(
            project_id, identity, workspace_id, source_snapshot_id
        )
        if historical is not None:
            # Replaying an older source intent must not rewrite today's current
            # understanding; the historical revision is the exact replay.
            return understanding_summary(historical)
        base = self._scan(
            project_id=project_id,
            source_snapshot_id=source_snapshot_id,
            pinned_snapshot_id=pinned_snapshot_id,
            scope=scope,
            selected_paths=selected_paths,
        )
        with transaction(self.unit, project_id) as tx:
            latest = self._current(project_id, identity, workspace_id)
            if latest is not None and latest.source_snapshot_id == source_snapshot_id:
                # Another writer finished the same understanding while this
                # process was scanning; return that exact revision.
                return understanding_summary(latest)
            revision = 1 if latest is None else latest.revision + 1
            understanding = _rebase(base, revision)
            payload = understanding_to_payload(
                understanding, project_id=project_id, workspace_id=workspace_id
            )
            tx.stage_record(
                aggregate_kind=PROJECT_UNDERSTANDING_AGGREGATE,
                record_id=identity,
                expected_revision=0 if latest is None else latest.revision,
                payload=payload,
            )
            tx.commit()
        return understanding_summary(understanding)

    def _workspace_id(self, project_id: str) -> str:
        if self.workspace_id is not None and self.workspace_id.strip():
            return self.workspace_id
        record = current_record(
            self.reader,
            project_id=project_id,
            aggregate_kind="project",
            record_id=project_id,
        )
        if record is None:
            raise ProjectUnderstandingError("project understanding requires a registered project")
        workspace = record.payload.get("workspace_id")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ProjectUnderstandingError("project workspace identity cannot be verified")
        return workspace

    def latest(self, *, project_id: str, scope: str) -> ProjectUnderstanding | None:
        """读取该 (project, scope) 的当前 understanding；没有则返回 None。

        只用于已经在同一工作空间保存过的项目；读取会校验记录摘要与归属，
        损坏记录不会被当成空目录。
        """

        _require_text(project_id, "project_id")
        _require_text(scope, "scope")
        workspace_id = self._workspace_id(project_id)
        return self._current(
            project_id, understanding_identity(project_id, scope), workspace_id
        )

    def _current(
        self, project_id: str, identity: str, workspace_id: str
    ) -> ProjectUnderstanding | None:
        record = current_record(
            self.reader,
            project_id=project_id,
            aggregate_kind=PROJECT_UNDERSTANDING_AGGREGATE,
            record_id=identity,
        )
        if record is None:
            return None
        return understanding_from_payload(
            record.payload,
            project_id=project_id,
            workspace_id=workspace_id,
            record_revision=record.revision,
        )

    def _by_source(
        self,
        project_id: str,
        identity: str,
        workspace_id: str,
        source_snapshot_id: str,
    ) -> ProjectUnderstanding | None:
        """Find the already-saved revision for this exact source snapshot.

        A source snapshot can be replayed long after a newer one became
        current (the retained source intent is the authority). The immutable
        revision list is queried instead of treating the newest revision as
        the only possible answer.
        """
        cursor: str | None = None
        seen: set[str] = set()
        matches: list[ProjectUnderstanding] = []
        while True:
            try:
                page = self.reader.query(
                    RecordQuery(
                        project_id=project_id,
                        aggregate_kind=PROJECT_UNDERSTANDING_AGGREGATE,
                        record_id=identity,
                        limit=100,
                        cursor=cursor,
                    )
                )
            except IndexMaintenanceRequired as error:
                raise ProjectUnderstandingError(
                    "historical project understanding revisions cannot be queried"
                ) from error
            for item in page.items:
                if item.payload.get("source_snapshot_id") != source_snapshot_id:
                    continue
                matches.append(
                    understanding_from_payload(
                        item.payload,
                        project_id=project_id,
                        workspace_id=workspace_id,
                        record_revision=item.revision,
                    )
                )
            if page.next_cursor is None:
                break
            if page.next_cursor in seen:
                raise ProjectUnderstandingError(
                    "project understanding query repeated its continuation"
                )
            seen.add(page.next_cursor)
            cursor = page.next_cursor
        if not matches:
            return None
        revisions = {match.revision for match in matches}
        if len(revisions) > 1:
            raise ProjectUnderstandingError(
                "one source snapshot has conflicting project understanding revisions"
            )
        return matches[0]

    def _scan(
        self,
        *,
        project_id: str,
        source_snapshot_id: str,
        pinned_snapshot_id: str,
        scope: str,
        selected_paths: Sequence[str],
    ) -> ProjectUnderstanding:
        with tempfile.TemporaryDirectory(prefix="aitest-understanding-") as directory:
            materialized = self.snapshots.materialize(pinned_snapshot_id, directory)
            if (
                not isinstance(materialized, Mapping)
                or materialized.get("state") != "materialized"
                or materialized.get("verified") is not True
            ):
                raise ProjectUnderstandingError(
                    "pinned source material could not be materialized for scanning"
                )
            raw_paths = materialized.get("paths")
            if not isinstance(raw_paths, list):
                raise ProjectUnderstandingError("materialized source has no readable file list")
            root = Path(directory).resolve()
            files: dict[str, bytes] = {}
            all_paths: list[str] = []
            for item in raw_paths:
                if not isinstance(item, Mapping):
                    raise ProjectUnderstandingError("materialized source file entry is invalid")
                relative = item.get("relative_path")
                actual = item.get("actual_path")
                if not isinstance(relative, str) or not relative.strip():
                    raise ProjectUnderstandingError("materialized source path is empty")
                if not isinstance(actual, str) or not actual.strip():
                    raise ProjectUnderstandingError(
                        f"materialized source path is unreadable: {relative}"
                    )
                relative = relative.replace("\\", "/")
                if relative in all_paths:
                    raise ProjectUnderstandingError(
                        f"materialized source repeats a file: {relative}"
                    )
                all_paths.append(relative)
                if not relative.lower().endswith(".py"):
                    continue
                path = Path(actual)
                try:
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root):
                        raise ProjectUnderstandingError(
                            f"materialized source escapes its scan directory: {relative}"
                        )
                    content = resolved.read_bytes()
                except OSError as error:
                    raise ProjectUnderstandingError(
                        f"pinned Python source could not be read: {relative}"
                    ) from error
                expected = item.get("sha256")
                if isinstance(expected, str) and expected:
                    actual_digest = _DIGEST_PREFIX + hashlib.sha256(content).hexdigest()
                    if actual_digest != expected:
                        raise ProjectUnderstandingError(
                            f"pinned Python source digest changed: {relative}"
                        )
                files[relative] = content
            return scan_sources(
                files=files,
                all_paths=all_paths,
                source_snapshot_id=source_snapshot_id,
                understanding_id=understanding_identity(project_id, scope),
                revision=1,
                scope=scope,
                selected_paths=selected_paths,
            )
