"""Deterministic project understanding: scan, decide, persist, never call a model.

This module is the production path behind ``analyze_project``. It scans the
**already pinned** source snapshot (never today's working directory), builds a
``BusinessFunctionCatalog``, ``TestIntent`` records, ``GoalCandidate``
projections, ``CapabilityGap`` records and explicit ``unknown`` areas, and
persists them as one immutable ``project_understanding`` record.

The scan is deliberately conservative:

- only Python files are parsed; every other source-like file becomes a
  ``CapabilityGap`` with ``blocks_full=True``;
- a Python file that cannot be parsed becomes an ``unknown`` area instead of
  being silently skipped;
- recognised entry points are HTTP route decorators, ``argparse``
  ``add_parser`` commands and ``click.command``/``click.group``; a module with
  no explicit entry falls back to its public top-level functions/classes;
- required dimensions are derived by rules, not decided per plan.

The service persists one record per ``(project, scope)``. The same
``source_snapshot_id`` is idempotent; a different source snapshot produces a
new revision. Goal candidates are only a view over ``TestIntent``; this module
never creates a second acceptance object.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Final

from aitest.application.planning.substrate import (
    IndexMaintenanceRequired,
    RecordQuery,
    RecordReader,
    UnitOfWork,
    current_record,
    transaction,
)
from aitest.application.ports import SourceSnapshotPort
from aitest.domain.planning.understanding import (
    BusinessFunction,
    BusinessFunctionCatalog,
    BusinessFunctionKind,
    BusinessFunctionOrigin,
    CapabilityGap,
    FunctionTrigger,
    GoalCandidate,
    ProjectSignalOrigin,
    ProjectSignals,
    ProjectUnderstanding,
    ReviewState,
    TestIntent,
    TestIntentOrigin,
)

PROJECT_UNDERSTANDING_AGGREGATE: Final = "project_understanding"
PROJECT_UNDERSTANDING_SCHEMA: Final = "aitest.project-understanding/1.0"
SCANNER_REVISION: Final = "python-ast-scanner/1"
GOAL_CANDIDATE_LIMIT: Final = 5

_DIGEST_PREFIX: Final = "sha256:"
_PLACEHOLDER_DIGEST: Final = _DIGEST_PREFIX + ("0" * 64)

_DIMENSION_ORDER: Final = (
    "normal",
    "boundary",
    "error",
    "permission",
    "state_transition",
    "persistence",
    "dependency_failure",
    "concurrency",
)

_HTTP_METHOD_DECORATORS: Final = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options"}
)
_ROUTE_DECORATORS: Final = frozenset({"route", "api_route"})
_ROUTE_ROOTS: Final = frozenset({"app", "router", "api", "bp", "blueprint"})

_SUPPORTED_FRAMEWORKS: Final = frozenset({"fastapi", "flask", "click"})
_UNSUPPORTED_FRAMEWORKS: Final = frozenset(
    {
        "aiohttp",
        "bottle",
        "django",
        "falcon",
        "litestar",
        "pyramid",
        "sanic",
        "starlette",
        "tornado",
    }
)
_NON_PYTHON_SOURCE_SUFFIXES: Final = frozenset(
    {
        ".bat",
        ".c",
        ".cc",
        ".cmd",
        ".cpp",
        ".cs",
        ".go",
        ".graphql",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".m",
        ".mm",
        ".php",
        ".proto",
        ".ps1",
        ".pyi",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)
_IGNORED_DIRECTORY_NAMES: Final = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)
_TEST_DIRECTORY_NAMES: Final = frozenset({"test", "tests", "__tests__"})

_WRITE_TOKENS: Final = frozenset(
    {
        "add",
        "approve",
        "archive",
        "cancel",
        "close",
        "commit",
        "create",
        "delete",
        "disable",
        "enable",
        "insert",
        "migrate",
        "patch",
        "persist",
        "publish",
        "put",
        "refund",
        "register",
        "reject",
        "remove",
        "restore",
        "save",
        "ship",
        "submit",
        "transition",
        "update",
        "upload",
        "write",
    }
)
_STATE_TOKENS: Final = frozenset(
    {
        "activate",
        "archive",
        "cancel",
        "close",
        "deactivate",
        "disable",
        "enable",
        "pay",
        "publish",
        "refund",
        "reject",
        "restore",
        "ship",
        "state",
        "status",
        "submit",
        "transition",
    }
)
_PERMISSION_TOKENS: Final = frozenset(
    {
        "admin",
        "authorize",
        "auth",
        "current_user",
        "guard",
        "jwt",
        "login",
        "permission",
        "protected",
        "requires",
        "role",
        "token",
    }
)
_EXTERNAL_TOKENS: Final = frozenset(
    {
        "aiohttp",
        "boto3",
        "dependency",
        "external",
        "grpc",
        "http_client",
        "httpx",
        "kafka",
        "pika",
        "redis",
        "remote",
        "requests",
        "socket",
        "third_party",
        "upstream",
        "urllib",
        "webhook",
    }
)
_RISK_WORDS: Final = frozenset(
    {
        "account",
        "auth",
        "cancel",
        "delete",
        "invoice",
        "money",
        "order",
        "pay",
        "payment",
        "permission",
        "refund",
        "role",
        "user",
    }
)
_OBJECT_SKIP_TOKENS: Final = frozenset(
    {
        "add",
        "api",
        "build",
        "calculate",
        "compute",
        "create",
        "delete",
        "do",
        "fetch",
        "find",
        "get",
        "handle",
        "health",
        "list",
        "load",
        "main",
        "make",
        "parse",
        "process",
        "read",
        "register",
        "remove",
        "run",
        "save",
        "search",
        "send",
        "serve",
        "set",
        "show",
        "start",
        "stop",
        "submit",
        "update",
        "v1",
        "v2",
        "v3",
        "validate",
    }
)


class ProjectUnderstandingError(ValueError):
    """The scan or its saved record cannot be verified as the same source."""

    code = "B_UNDERSTANDING_UNVERIFIED"


# --------------------------------------------------------------------- values


@dataclass(frozen=True, slots=True)
class _RouteSpec:
    path: str | None
    methods: tuple[str, ...]
    line: int


@dataclass(frozen=True, slots=True)
class _Facts:
    parameters: tuple[str, ...]
    call_names: tuple[str, ...]
    decorator_names: tuple[str, ...]
    is_async: bool


@dataclass(frozen=True, slots=True)
class _Entry:
    trigger: FunctionTrigger
    kind: BusinessFunctionKind
    path: str
    qualname: str
    name: str
    business_object: str
    expected_outcome: str
    conditions: tuple[str, ...]
    methods: tuple[str, ...]
    dimensions: tuple[str, ...]
    source_refs: tuple[str, ...]
    confidence: float
    explicit: bool


@dataclass(frozen=True, slots=True)
class _PublicCandidate:
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    qualname: str
    is_class: bool


def understanding_identity(project_id: str, scope: str) -> str:
    """Stable identity of one ``(project, scope)`` understanding.

    A different source snapshot is a new ``revision`` of the same identity;
    a different scope is a different understanding.
    """
    _require_text(project_id, "project_id")
    _require_text(scope, "scope")
    raw = f"{project_id}\x1f{scope}".encode()
    return "und-" + hashlib.sha256(raw).hexdigest()[:16]


def understanding_summary(understanding: ProjectUnderstanding) -> dict[str, object]:
    """The response summary returned by ``analyze_project``.

    ``goal_candidates`` is the UI projection itself (not a count): callers need
    the candidate statements to show a goal picker. The other entries are
    counts so the response stays small even for a large catalog.
    """
    return {
        "understanding_id": understanding.understanding_id,
        "understanding_revision": understanding.revision,
        "function_count": len(understanding.catalog.functions),
        "intent_count": len(understanding.test_intents),
        "goal_candidates": [_goal_payload(goal) for goal in understanding.goal_candidates],
        "unknown_count": len(understanding.unknown_areas),
        "capability_gap_count": len(understanding.capability_gaps),
    }


def understanding_to_payload(
    understanding: ProjectUnderstanding, *, project_id: str, workspace_id: str
) -> dict[str, object]:
    """Serialise the immutable record body; revisions live in the body, too."""
    _require_text(project_id, "project_id")
    _require_text(workspace_id, "workspace_id")
    return {
        "schema_version": PROJECT_UNDERSTANDING_SCHEMA,
        "project_id": project_id,
        "workspace_id": workspace_id,
        "understanding_id": understanding.understanding_id,
        "revision": understanding.revision,
        "source_snapshot_id": understanding.source_snapshot_id,
        "scope": understanding.scope,
        "body": _body_payload(understanding, include_revision=True),
        "digest": understanding.digest,
    }


def understanding_from_payload(
    payload: Mapping[str, object],
    *,
    project_id: str,
    workspace_id: str,
    record_revision: int | None = None,
) -> ProjectUnderstanding:
    """Re-read a saved understanding and verify its content digest.

    A record whose digest no longer matches its body is rejected rather than
    interpreted, so a damaged record cannot become a silent empty catalog.
    """
    _require_text(project_id, "project_id")
    _require_text(workspace_id, "workspace_id")
    schema = _text(payload.get("schema_version"), "schema_version")
    if schema != PROJECT_UNDERSTANDING_SCHEMA:
        raise ProjectUnderstandingError("project understanding schema is unsupported")
    if _text(payload.get("project_id"), "project_id") != project_id:
        raise ProjectUnderstandingError("project understanding belongs to another project")
    if _text(payload.get("workspace_id"), "workspace_id") != workspace_id:
        raise ProjectUnderstandingError("project understanding belongs to another workspace")
    understanding_id = _text(payload.get("understanding_id"), "understanding_id")
    revision = _integer(payload.get("revision"), "revision")
    if revision < 1:
        raise ProjectUnderstandingError("project understanding revision must be >= 1")
    if record_revision is not None and revision != record_revision:
        raise ProjectUnderstandingError(
            "project understanding revision differs from its warehouse record"
        )
    source_snapshot_id = _text(payload.get("source_snapshot_id"), "source_snapshot_id")
    scope = _text(payload.get("scope"), "scope")
    if understanding_id != understanding_identity(project_id, scope):
        raise ProjectUnderstandingError("project understanding identity differs from its scope")
    body = _mapping(payload.get("body"), "body")
    signals = _signals_from_payload(_mapping(body.get("project_signals"), "project_signals"))
    functions = tuple(
        _function_from_payload(item, revision=revision, source_snapshot_id=source_snapshot_id)
        for item in _mappings(body.get("catalog"), "catalog")
    )
    catalog = BusinessFunctionCatalog(
        understanding_id=understanding_id,
        revision=revision,
        functions=functions,
    )
    intents = tuple(
        _intent_from_payload(item, revision=revision)
        for item in _mappings(body.get("test_intents"), "test_intents")
    )
    goals = tuple(
        _goal_from_payload(item)
        for item in _mappings(body.get("goal_candidates"), "goal_candidates")
    )
    gaps = tuple(
        _gap_from_payload(item)
        for item in _mappings(body.get("capability_gaps"), "capability_gaps")
    )
    unknown = tuple(_text_list(body.get("unknown_areas"), "unknown_areas"))
    saved_digest = _text(payload.get("digest"), "digest")
    try:
        understanding = ProjectUnderstanding(
            understanding_id=understanding_id,
            revision=revision,
            source_snapshot_id=source_snapshot_id,
            scope=scope,
            project_signals=signals,
            catalog=catalog,
            test_intents=intents,
            goal_candidates=goals,
            capability_gaps=gaps,
            unknown_areas=unknown,
            digest=saved_digest,
        )
    except ValueError as error:
        raise ProjectUnderstandingError(
            f"saved project understanding is invalid: {error}"
        ) from error
    if understanding.digest != understanding_content_digest(understanding):
        raise ProjectUnderstandingError(
            "saved project understanding digest does not match its body"
        )
    return understanding


def understanding_content_digest(understanding: ProjectUnderstanding) -> str:
    """Content digest that deliberately excludes revision numbers.

    The same source snapshot must replay byte-identically before it becomes a
    new revision. Revision is a warehouse fact, not scan content.
    """
    return _digest(
        {
            "understanding_id": understanding.understanding_id,
            "source_snapshot_id": understanding.source_snapshot_id,
            "scope": understanding.scope,
            **_body_payload(understanding, include_revision=False),
        }
    )


# --------------------------------------------------------------------- scanner


def scan_sources(
    *,
    files: Mapping[str, bytes | str],
    source_snapshot_id: str,
    understanding_id: str,
    revision: int,
    scope: str,
    selected_paths: Sequence[str] = (),
    all_paths: Sequence[str] | None = None,
) -> ProjectUnderstanding:
    """Build one understanding from already-read source bytes.

    ``files`` holds Python source bytes; ``all_paths`` is the complete selected
    file list (non-Python files are read only as paths, never as content).
    When ``all_paths`` is omitted the keys of ``files`` are used.
    """
    _require_text(source_snapshot_id, "source_snapshot_id")
    _require_text(understanding_id, "understanding_id")
    _require_text(scope, "scope")
    if type(revision) is not int or revision < 1:
        raise ProjectUnderstandingError("project understanding revision must be >= 1")
    selection = _selection(selected_paths)
    declared = tuple(all_paths) if all_paths is not None else tuple(files)
    for path in (*declared, *files):
        _require_safe_relative(path)
    normalised_files = {
        _normalise_relative(path): content for path, content in files.items()
    }
    paths = tuple(
        sorted({_normalise_relative(path) for path in (*declared, *normalised_files)})
    )
    selected = tuple(path for path in paths if _matches_selection(path, selection))

    entries: list[_Entry] = []
    unknown: list[str] = []
    gaps: list[CapabilityGap] = []
    frameworks: set[str] = set()
    stack: set[str] = set()
    entry_files: set[str] = set()
    python_files: list[str] = []
    for relative in selected:
        if _ignored_path(relative):
            continue
        if not relative.lower().endswith(".py"):
            suffix = PurePosixPath(relative).suffix.lower()
            if suffix in _NON_PYTHON_SOURCE_SUFFIXES and not _is_test_path(relative):
                gaps.append(_non_python_gap(relative, suffix))
            continue
        python_files.append(relative)
        raw = normalised_files.get(relative)
        if raw is None:
            raise ProjectUnderstandingError(f"Python source bytes are missing: {relative}")
        try:
            tree = ast.parse(raw, filename=relative)
        except (SyntaxError, ValueError, TypeError, RecursionError) as error:
            line = getattr(error, "lineno", None)
            location = f"{relative}:{line}" if type(line) is int else relative
            unknown.append(f"{location}: Python 源码无法解析（{type(error).__name__}）")
            continue
        if _is_test_path(relative):
            continue
        scanner = _ModuleScanner(relative)
        scanner.visit(tree)
        scanner.finish()
        entries.extend(scanner.entries)
        unknown.extend(scanner.unknown)
        gaps.extend(scanner.gaps)
        frameworks.update(scanner.frameworks)
        stack.update(scanner.stack)
        if scanner.entries:
            entry_files.add(relative)

    merged = _merge_entries(entries)
    merged.sort(key=lambda entry: (entry.path, entry.qualname, entry.name))
    functions = _build_functions(
        merged,
        understanding_id=understanding_id,
        revision=revision,
        source_snapshot_id=source_snapshot_id,
    )
    intents = _build_intents(functions, revision)
    goals = _build_goals(intents, functions)
    if python_files:
        stack.add("python")
    stack.update(frameworks)
    capabilities = sorted({entry.trigger.value for entry in merged})
    risk_signals = sorted(
        {
            dimension
            for entry in merged
            for dimension in entry.dimensions
            if dimension not in {"normal", "error"}
        }
    )
    if any(gap.missing_capability.startswith("scan:non_python") for gap in gaps):
        capabilities.append("non_python_source")
    capabilities = sorted(set(capabilities))
    evidence = sorted(
        {
            ref
            for entry in merged
            for ref in entry.source_refs
        }
        | {ref for gap in gaps for ref in gap.source_refs}
    )
    confidence: dict[str, float] = {}
    for name in sorted(stack):
        confidence[name] = 0.9
    for name in capabilities:
        confidence.setdefault(name, 0.8)
    for name in risk_signals:
        confidence.setdefault(name, 0.7)
    signals = ProjectSignals(
        detected_stack=tuple(sorted(stack)),
        entry_candidates=tuple(sorted(entry_files)),
        capabilities=tuple(capabilities),
        risk_signals=tuple(risk_signals),
        confidence=confidence,
        evidence_refs=tuple(evidence),
        origin=ProjectSignalOrigin.DETERMINISTIC,
    )
    if not merged:
        if not python_files:
            unknown.append(f"{scope}: 未发现可扫描的 Python 文件")
        elif not unknown:
            unknown.append(f"{scope}: 扫描完成但未识别到任何业务入口")
    unique_unknown = tuple(sorted(set(unknown)))
    unique_gaps = tuple(
        sorted({gap.gap_id: gap for gap in gaps}.values(), key=lambda gap: gap.gap_id)
    )
    placeholder = ProjectUnderstanding(
        understanding_id=understanding_id,
        revision=revision,
        source_snapshot_id=source_snapshot_id,
        scope=scope,
        project_signals=signals,
        catalog=BusinessFunctionCatalog(
            understanding_id=understanding_id,
            revision=revision,
            functions=functions,
        ),
        test_intents=intents,
        goal_candidates=goals,
        capability_gaps=unique_gaps,
        unknown_areas=unique_unknown,
        digest=_PLACEHOLDER_DIGEST,
    )
    return replace(placeholder, digest=understanding_content_digest(placeholder))


# ---------------------------------------------------------------------- service


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


# ------------------------------------------------------------- module scanner


class _ModuleScanner(ast.NodeVisitor):
    """One-file deterministic scan; no imports are executed and no model is called."""

    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.entries: list[_Entry] = []
        self.unknown: list[str] = []
        self.gaps: list[CapabilityGap] = []
        self.frameworks: set[str] = set()
        self.stack: set[str] = set()
        self.scope: list[str] = []
        self.public: list[_PublicCandidate] = []
        self.click_modules: set[str] = set()
        self.click_names: set[str] = set()
        self.click_groups: set[str] = set()
        self.has_urlpatterns = False
        self.has_main_guard = False
        self._gap_ids: set[str] = set()

    # -- imports -------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._import_name(alias.name, alias.asname)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._import_name(module, None)
        root = module.split(".")[0].lower()
        if root == "click":
            for alias in node.names:
                if alias.name in {"command", "group"}:
                    self.click_names.add(alias.asname or alias.name)
        if node.level == 0 and module.split(".")[0].lower() == "click" and any(
            alias.name == "*" for alias in node.names
        ):
            self.click_names.update({"command", "group"})
        self.generic_visit(node)

    def _import_name(self, name: str, asname: str | None) -> None:
        root = name.split(".")[0].lower()
        if not root:
            return
        if root in _SUPPORTED_FRAMEWORKS:
            self.frameworks.add(root)
            self.stack.add(root)
        if root == "argparse":
            self.stack.add("argparse")
        if root == "click":
            self.frameworks.add("click")
            self.stack.add("click")
            self.click_modules.add(asname or "click")
        if root in _UNSUPPORTED_FRAMEWORKS:
            self._add_framework_gap(root)

    # -- functions / classes ------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._qualname(node.name)
        if not self.scope and not node.name.startswith("_"):
            self.public.append(_PublicCandidate(node, qualname, True))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = self._qualname(node.name)
        facts = _function_facts(node)
        for decorator in node.decorator_list:
            route_shape = _looks_like_route_decorator(decorator)
            if route_shape:
                spec = _route_spec(decorator)
                if spec is None:
                    self.unknown.append(
                        f"{self.relative}:{_line(decorator)}: 无法识别的路由装饰器"
                    )
                    continue
                self._add_http_entry(qualname, spec, facts)
                continue
            click = self._click_spec(decorator)
            if click is not None:
                command, is_group = click
                if is_group:
                    self.click_groups.add(node.name)
                self._add_click_entry(
                    qualname, command or node.name, facts, _line(decorator)
                )
        if not self.scope and not node.name.startswith("_"):
            self.public.append(_PublicCandidate(node, qualname, False))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "urlpatterns":
                self.has_urlpatterns = True
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if not self.scope and _is_main_guard(node.test):
            self.has_main_guard = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_parser":
            command = _first_string(node) or _keyword_string(node, "name")
            if command is None:
                self.unknown.append(
                    f"{self.relative}:{_line(node)}: argparse add_parser 缺少可解析的命令名"
                )
            else:
                self._add_argparse_entry(node, command)
        self.generic_visit(node)

    # -- finish --------------------------------------------------------

    def finish(self) -> None:
        if self.has_urlpatterns and (
            "django" in self.frameworks or self.relative.endswith("urls.py")
        ):
            self._add_framework_gap("django")
        if self.entries:
            return
        for candidate in self.public:
            self._add_fallback_entry(candidate)

    # -- entry builders -------------------------------------------------

    def _add_http_entry(
        self,
        qualname: str,
        spec: _RouteSpec,
        facts: _Facts,
    ) -> None:
        if spec.path is None:
            self.unknown.append(
                f"{self.relative}:{spec.line}: 路由装饰器缺少可解析的路径"
            )
        path = spec.path or ""
        methods = spec.methods
        name = _http_name(methods, path, qualname)
        business_object = _object_from_route(path) or _object_from_name(qualname)
        dimensions = _derive_dimensions(
            name=name,
            path=path,
            trigger=FunctionTrigger.HTTP,
            methods=methods,
            facts=facts,
        )
        self.entries.append(
            _Entry(
                trigger=FunctionTrigger.HTTP,
                kind=BusinessFunctionKind.OPERATION,
                path=path,
                qualname=qualname,
                name=name,
                business_object=business_object,
                expected_outcome=_http_outcome(methods, business_object),
                conditions=tuple(f"http_method={method}" for method in methods),
                methods=methods,
                dimensions=dimensions,
                source_refs=(f"{self.relative}:{spec.line}",),
                confidence=0.9,
                explicit=True,
            )
        )

    def _add_click_entry(
        self,
        qualname: str,
        command: str,
        facts: _Facts,
        line: int,
    ) -> None:
        name = command
        path = f"cli:{command}"
        dimensions = _derive_dimensions(
            name=name,
            path=path,
            trigger=FunctionTrigger.CLI,
            methods=(),
            facts=facts,
        )
        self.entries.append(
            _Entry(
                trigger=FunctionTrigger.CLI,
                kind=BusinessFunctionKind.OPERATION,
                path=path,
                qualname=qualname,
                name=name,
                business_object=_object_from_name(command),
                expected_outcome="命令执行成功并返回结果",
                conditions=(f"cli_command={command}",),
                methods=(),
                dimensions=dimensions,
                source_refs=(f"{self.relative}:{line}",),
                confidence=0.9,
                explicit=True,
            )
        )

    def _add_argparse_entry(self, node: ast.Call, command: str) -> None:
        qualname = ".".join((*self.scope, command)) if self.scope else command
        path = f"cli:{command}"
        facts = _Facts(
            parameters=(),
            call_names=(),
            decorator_names=(),
            is_async=False,
        )
        dimensions = _derive_dimensions(
            name=command,
            path=path,
            trigger=FunctionTrigger.CLI,
            methods=(),
            facts=facts,
        )
        self.entries.append(
            _Entry(
                trigger=FunctionTrigger.CLI,
                kind=BusinessFunctionKind.OPERATION,
                path=path,
                qualname=qualname,
                name=command,
                business_object=_object_from_name(command),
                expected_outcome="命令执行成功并返回结果",
                conditions=(f"cli_command={command}",),
                methods=(),
                dimensions=dimensions,
                source_refs=(f"{self.relative}:{_line(node)}",),
                confidence=0.9,
                explicit=True,
            )
        )

    def _add_fallback_entry(self, candidate: _PublicCandidate) -> None:
        node = candidate.node
        facts = _class_facts(node) if isinstance(node, ast.ClassDef) else _function_facts(node)
        trigger = (
            FunctionTrigger.CLI
            if not candidate.is_class
            and (node.name == "main" or self.has_main_guard)
            else FunctionTrigger.LIBRARY
        )
        kind = (
            BusinessFunctionKind.CAPABILITY
            if candidate.is_class
            else BusinessFunctionKind.OPERATION
        )
        path = f"module:{candidate.qualname}"
        name = candidate.qualname
        dimensions = _derive_dimensions(
            name=name,
            path=path,
            trigger=trigger,
            methods=(),
            facts=facts,
        )
        self.entries.append(
            _Entry(
                trigger=trigger,
                kind=kind,
                path=path,
                qualname=candidate.qualname,
                name=name,
                business_object=_object_from_name(candidate.qualname),
                expected_outcome=(
                    "命令执行成功并返回结果"
                    if trigger is FunctionTrigger.CLI
                    else "返回预期业务结果"
                ),
                conditions=(),
                methods=(),
                dimensions=dimensions,
                source_refs=(f"{self.relative}:{_line(node)}",),
                confidence=0.5,
                explicit=False,
            )
        )

    # -- helpers --------------------------------------------------------

    def _add_framework_gap(self, framework: str) -> None:
        gap = _framework_gap(self.relative, framework)
        if gap.gap_id not in self._gap_ids:
            self._gap_ids.add(gap.gap_id)
            self.gaps.append(gap)

    def _qualname(self, name: str) -> str:
        return ".".join((*self.scope, name)) if self.scope else name

    def _click_spec(self, decorator: ast.expr) -> tuple[str | None, bool] | None:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            roles = _root_parts(target)
            root = roles[0].lower() if roles else ""
            attribute = target.attr.lower()
            if attribute not in {"command", "group"}:
                return None
            known_module = root in {name.lower() for name in self.click_modules}
            known_group = attribute == "command" and root in self.click_groups
            if not (known_module or known_group):
                return None
            is_group = attribute == "group"
        elif isinstance(target, ast.Name):
            if target.id not in self.click_names:
                return None
            is_group = target.id.lower() == "group"
        else:
            return None
        explicit: str | None = None
        if isinstance(decorator, ast.Call):
            explicit = _first_string(decorator) or _keyword_string(decorator, "name")
        return explicit, is_group


# -------------------------------------------------------------------- helpers


def _body_payload(
    understanding: ProjectUnderstanding, *, include_revision: bool
) -> dict[str, object]:
    return {
        "project_signals": _signals_payload(understanding.project_signals),
        "catalog": [
            _function_payload(function, include_revision=include_revision)
            for function in understanding.catalog.functions
        ],
        "test_intents": [
            _intent_payload(intent, include_revision=include_revision)
            for intent in understanding.test_intents
        ],
        "goal_candidates": [_goal_payload(goal) for goal in understanding.goal_candidates],
        "capability_gaps": [
            _gap_payload(gap) for gap in understanding.capability_gaps
        ],
        "unknown_areas": list(understanding.unknown_areas),
    }


def _signals_payload(signals: ProjectSignals) -> dict[str, object]:
    return {
        "detected_stack": list(signals.detected_stack),
        "entry_candidates": list(signals.entry_candidates),
        "capabilities": list(signals.capabilities),
        "risk_signals": list(signals.risk_signals),
        "confidence": dict(sorted(signals.confidence.items())),
        "evidence_refs": list(signals.evidence_refs),
        "origin": signals.origin.value,
    }


def _function_payload(function: BusinessFunction, *, include_revision: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "function_id": function.function_id,
        "name": function.name,
        "parent_function_id": function.parent_function_id,
        "kind": function.kind.value,
        "trigger": function.trigger.value,
        "business_object": function.business_object,
        "expected_outcome": function.expected_outcome,
        "conditions": list(function.conditions),
        "required_dimensions": list(function.required_dimensions),
        "source_refs": list(function.source_refs),
        "origin": function.origin.value,
        "confidence": function.confidence,
        "review_state": function.review_state.value,
        "canonical_function_id": function.canonical_function_id,
        "aliases": list(function.aliases),
        "source_snapshot_id": function.source_snapshot_id,
    }
    if function.dimension_origins:
        payload["dimension_origins"] = [
            {"dimension": dimension, "source": source, "reason": reason}
            for dimension, source, reason in function.dimension_origins
        ]
    if include_revision:
        payload["understanding_revision"] = function.understanding_revision
    return payload


def _intent_payload(intent: TestIntent, *, include_revision: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "intent_id": intent.intent_id,
        "statement": intent.statement,
        "risk": intent.risk,
        "function_ids": sorted(intent.function_ids),
        "required_dimensions": list(intent.required_dimensions),
        "acceptance_condition": intent.acceptance_condition,
        "rationale": intent.rationale,
        "origin": intent.origin.value,
        "confidence": intent.confidence,
    }
    if include_revision:
        payload["understanding_revision"] = intent.understanding_revision
    return payload


def _goal_payload(goal: GoalCandidate) -> dict[str, object]:
    return {
        "intent_id": goal.intent_id,
        "statement": goal.statement,
        "why": goal.why,
        "function_ids": sorted(goal.function_ids),
        "risk": goal.risk,
    }


def _gap_payload(gap: CapabilityGap) -> dict[str, object]:
    return {
        "gap_id": gap.gap_id,
        "scope": gap.scope,
        "missing_capability": gap.missing_capability,
        "capability_revision": gap.capability_revision,
        "reason": gap.reason,
        "workaround": gap.workaround,
        "blocks_full": gap.blocks_full,
        "source_refs": list(gap.source_refs),
    }


def _signals_from_payload(payload: Mapping[str, object]) -> ProjectSignals:
    confidence = _mapping(payload.get("confidence"), "confidence")
    values: dict[str, float] = {}
    for key, raw in confidence.items():
        values[_text(key, "confidence signal")] = _float(raw, "confidence")
    return ProjectSignals(
        detected_stack=_text_list(payload.get("detected_stack"), "detected_stack"),
        entry_candidates=_text_list(payload.get("entry_candidates"), "entry_candidates"),
        capabilities=_text_list(payload.get("capabilities"), "capabilities"),
        risk_signals=_text_list(payload.get("risk_signals"), "risk_signals"),
        confidence=values,
        evidence_refs=_text_list(payload.get("evidence_refs"), "evidence_refs"),
        origin=ProjectSignalOrigin(_text(payload.get("origin"), "origin")),
    )


def _function_from_payload(
    payload: Mapping[str, object], *, revision: int, source_snapshot_id: str
) -> BusinessFunction:
    return BusinessFunction(
        function_id=_text(payload.get("function_id"), "function_id"),
        name=_text(payload.get("name"), "name"),
        parent_function_id=_optional_text(payload.get("parent_function_id"), "parent_function_id"),
        kind=BusinessFunctionKind(_text(payload.get("kind"), "kind")),
        trigger=FunctionTrigger(_text(payload.get("trigger"), "trigger")),
        business_object=_text(payload.get("business_object"), "business_object"),
        expected_outcome=_text(payload.get("expected_outcome"), "expected_outcome"),
        conditions=_text_list(payload.get("conditions"), "conditions"),
        required_dimensions=_text_list(
            payload.get("required_dimensions"), "required_dimensions"
        ),
        source_refs=_text_list(payload.get("source_refs"), "source_refs"),
        origin=BusinessFunctionOrigin(_text(payload.get("origin"), "origin")),
        confidence=_float(payload.get("confidence"), "confidence"),
        review_state=ReviewState(_text(payload.get("review_state"), "review_state")),
        understanding_revision=_integer(
            payload.get("understanding_revision"), "understanding_revision"
        ),
        canonical_function_id=_optional_text(
            payload.get("canonical_function_id"), "canonical_function_id"
        ),
        aliases=_text_list(payload.get("aliases"), "aliases"),
        source_snapshot_id=_text(payload.get("source_snapshot_id"), "source_snapshot_id"),
        dimension_origins=tuple(
            (
                _text(item.get("dimension"), "dimension"),
                _text(item.get("source"), "source"),
                _reason_text(item.get("reason")),
            )
            for item in _mappings(payload.get("dimension_origins", []), "dimension_origins")
        ),
    )


def _intent_from_payload(payload: Mapping[str, object], *, revision: int) -> TestIntent:
    understanding_revision = _integer(
        payload.get("understanding_revision"), "understanding_revision"
    )
    if understanding_revision != revision:
        raise ProjectUnderstandingError("test intent revision differs from its understanding")
    return TestIntent(
        intent_id=_text(payload.get("intent_id"), "intent_id"),
        statement=_text(payload.get("statement"), "statement"),
        risk=_text(payload.get("risk"), "risk"),
        function_ids=frozenset(_text_list(payload.get("function_ids"), "function_ids")),
        required_dimensions=_text_list(
            payload.get("required_dimensions"), "required_dimensions"
        ),
        acceptance_condition=_text(
            payload.get("acceptance_condition"), "acceptance_condition"
        ),
        rationale=_text(payload.get("rationale"), "rationale"),
        origin=TestIntentOrigin(_text(payload.get("origin"), "origin")),
        confidence=_float(payload.get("confidence"), "confidence"),
        understanding_revision=understanding_revision,
    )


def _goal_from_payload(payload: Mapping[str, object]) -> GoalCandidate:
    return GoalCandidate(
        intent_id=_text(payload.get("intent_id"), "intent_id"),
        statement=_text(payload.get("statement"), "statement"),
        why=_text(payload.get("why"), "why"),
        function_ids=frozenset(_text_list(payload.get("function_ids"), "function_ids")),
        risk=_text(payload.get("risk"), "risk"),
    )


def _gap_from_payload(payload: Mapping[str, object]) -> CapabilityGap:
    workaround = payload.get("workaround")
    return CapabilityGap(
        gap_id=_text(payload.get("gap_id"), "gap_id"),
        scope=_text(payload.get("scope"), "scope"),
        missing_capability=_text(
            payload.get("missing_capability"), "missing_capability"
        ),
        capability_revision=_text(
            payload.get("capability_revision"), "capability_revision"
        ),
        reason=_text(payload.get("reason"), "reason"),
        workaround=None if workaround is None else _text(workaround, "workaround"),
        blocks_full=_boolean(payload.get("blocks_full"), "blocks_full"),
        source_refs=_text_list(payload.get("source_refs"), "source_refs"),
    )


def _build_functions(
    entries: Sequence[_Entry],
    *,
    understanding_id: str,
    revision: int,
    source_snapshot_id: str,
) -> tuple[BusinessFunction, ...]:
    functions = [
        BusinessFunction(
            function_id=_function_id(entry.path, entry.qualname),
            name=entry.name,
            parent_function_id=None,
            kind=entry.kind,
            trigger=entry.trigger,
            business_object=entry.business_object,
            expected_outcome=entry.expected_outcome,
            conditions=entry.conditions,
            required_dimensions=entry.dimensions,
            source_refs=entry.source_refs,
            origin=BusinessFunctionOrigin.DETERMINISTIC,
            confidence=entry.confidence,
            review_state=ReviewState.UNREVIEWED,
            understanding_revision=revision,
            canonical_function_id=None,
            aliases=(),
            source_snapshot_id=source_snapshot_id,
            dimension_origins=tuple(
                (dimension, "rule", "") for dimension in entry.dimensions
            ),
        )
        for entry in entries
    ]
    return tuple(sorted(functions, key=lambda function: function.function_id))


def _build_intents(
    functions: Sequence[BusinessFunction], revision: int
) -> tuple[TestIntent, ...]:
    intents = [
        TestIntent(
            intent_id="intent-" + function.function_id.removeprefix("fn-"),
            statement=f"验证「{function.name}」的业务结果",
            risk=_risk_statement(function),
            function_ids=frozenset({function.function_id}),
            required_dimensions=function.required_dimensions,
            acceptance_condition=f"可观察：{function.expected_outcome}",
            rationale=(
                f"确定性扫描：{function.trigger.value} 入口 "
                f"{function.source_refs[0] if function.source_refs else 'source'}"
            ),
            origin=TestIntentOrigin.AI_SUGGESTED,
            confidence=function.confidence,
            understanding_revision=revision,
        )
        for function in functions
    ]
    return tuple(sorted(intents, key=lambda intent: intent.intent_id))


def _build_goals(
    intents: Sequence[TestIntent], functions: Sequence[BusinessFunction]
) -> tuple[GoalCandidate, ...]:
    by_id = {function.function_id: function for function in functions}
    scored: list[tuple[int, int, str, TestIntent, BusinessFunction]] = []
    for intent in intents:
        function = next(
            (by_id[function_id] for function_id in intent.function_ids if function_id in by_id),
            None,
        )
        if function is None:
            continue
        scored.append(
            (
                _goal_score(function),
                _trigger_priority(function.trigger),
                function.function_id,
                intent,
                function,
            )
        )
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return tuple(
        GoalCandidate(
            intent_id=intent.intent_id,
            statement=intent.statement,
            why=(
                f"入口 {function.trigger.value}；必要维度 "
                f"{', '.join(function.required_dimensions)}"
            ),
            function_ids=intent.function_ids,
            risk=intent.risk,
        )
        for _score, _priority, _identity, intent, function in scored[:GOAL_CANDIDATE_LIMIT]
    )


def _goal_score(function: BusinessFunction) -> int:
    dimensions = set(function.required_dimensions)
    score = 0
    if "permission" in dimensions:
        score += 30
    if "state_transition" in dimensions:
        score += 25
    if "persistence" in dimensions:
        score += 25
    if "dependency_failure" in dimensions:
        score += 15
    if "boundary" in dimensions:
        score += 5
    score += 10 if function.trigger is FunctionTrigger.HTTP else 0
    score += 5 if function.trigger is FunctionTrigger.CLI else 0
    tokens = set(_tokens(function.name))
    if tokens & _RISK_WORDS:
        score += 8
    return score


def _trigger_priority(trigger: FunctionTrigger) -> int:
    if trigger is FunctionTrigger.HTTP:
        return 3
    if trigger is FunctionTrigger.CLI:
        return 2
    return 1


def _risk_statement(function: BusinessFunction) -> str:
    dimensions = set(function.required_dimensions)
    if "permission" in dimensions:
        return "越权或未授权访问"
    if "state_transition" in dimensions and "persistence" in dimensions:
        return "数据写入错误或状态不一致"
    if "dependency_failure" in dimensions:
        return "外部依赖失败导致业务不可用"
    if "boundary" in dimensions:
        return "边界输入处理错误"
    return "核心业务结果错误"


def _derive_dimensions(
    *,
    name: str,
    path: str,
    trigger: FunctionTrigger,
    methods: Sequence[str],
    facts: _Facts,
) -> tuple[str, ...]:
    tokens: set[str] = set(_tokens(f"{name} {path}"))
    for call in facts.call_names:
        tokens.update(_tokens(call.replace(".", " ")))
    for decorator in facts.decorator_names:
        tokens.update(_tokens(decorator.replace(".", " ")))
    for parameter in facts.parameters:
        tokens.update(_tokens(parameter))
    dimensions = {"normal", "error"}
    path_parameter = trigger is FunctionTrigger.HTTP and ("{" in path or "<" in path)
    if facts.parameters or path_parameter:
        dimensions.add("boundary")
    method_tokens = {method.upper() for method in methods}
    write = bool(method_tokens & {"POST", "PUT", "PATCH", "DELETE"}) or bool(
        tokens & _WRITE_TOKENS
    )
    if write:
        dimensions.update({"state_transition", "persistence"})
    elif tokens & _STATE_TOKENS:
        dimensions.add("state_transition")
    if tokens & _PERMISSION_TOKENS:
        dimensions.add("permission")
    if tokens & _EXTERNAL_TOKENS:
        dimensions.add("dependency_failure")
    return tuple(dimension for dimension in _DIMENSION_ORDER if dimension in dimensions)


def _http_name(methods: Sequence[str], path: str, qualname: str) -> str:
    label = "/".join(methods) if methods else "HTTP"
    if path:
        return f"{label} {path}"
    return f"{label} {qualname}"


def _http_outcome(methods: Sequence[str], business_object: str) -> str:
    upper = {method.upper() for method in methods}
    if "DELETE" in upper:
        return f"删除{business_object}成功"
    if "POST" in upper:
        return f"创建或完成{business_object}操作并返回结果"
    if upper & {"PUT", "PATCH"}:
        return f"更新{business_object}成功"
    if upper & {"GET", "HEAD", "OPTIONS"}:
        return f"返回{business_object}信息"
    return f"{business_object}操作成功"


def _object_from_route(path: str) -> str:
    for segment in path.strip("/").split("/"):
        lowered = segment.lower()
        if not segment or segment.startswith(("{", "<", ":")):
            continue
        if lowered.isdigit() or (lowered.startswith("v") and lowered[1:].isdigit()):
            continue
        if lowered in {"api", "rest", "internal", "public"}:
            continue
        value = re.sub(r"[^\w ]", " ", segment.replace("-", " ").replace("_", " "))
        value = " ".join(value.split())
        if value:
            return value
    return ""


def _object_from_name(name: str) -> str:
    tokens = [token for token in _tokens(name) if token.lower() not in _OBJECT_SKIP_TOKENS]
    if not tokens:
        original = _tokens(name)
        return original[-1] if original else name
    return " ".join(tokens)


def _merge_entries(entries: Iterable[_Entry]) -> list[_Entry]:
    merged: dict[tuple[str, str], _Entry] = {}
    for entry in entries:
        key = (entry.path, entry.qualname)
        current = merged.get(key)
        if current is None:
            merged[key] = entry
            continue
        methods = tuple(sorted(set(current.methods) | set(entry.methods)))
        conditions = tuple(sorted(set(current.conditions) | set(entry.conditions)))
        dimensions = tuple(
            dimension
            for dimension in _DIMENSION_ORDER
            if dimension in set(current.dimensions) | set(entry.dimensions)
        )
        source_refs = tuple(sorted(set(current.source_refs) | set(entry.source_refs)))
        name = (
            _http_name(methods, current.path, current.qualname)
            if current.trigger is FunctionTrigger.HTTP
            else current.name
        )
        merged[key] = replace(
            current,
            methods=methods,
            conditions=conditions,
            dimensions=dimensions,
            source_refs=source_refs,
            name=name,
            expected_outcome=(
                _http_outcome(methods, current.business_object)
                if current.trigger is FunctionTrigger.HTTP
                else current.expected_outcome
            ),
            confidence=max(current.confidence, entry.confidence),
            explicit=current.explicit or entry.explicit,
        )
    return list(merged.values())


def _function_id(path: str, qualname: str) -> str:
    raw = f"{path}\x1f{qualname}".encode()
    return "fn-" + hashlib.sha256(raw).hexdigest()[:16]


def _framework_gap(relative: str, framework: str) -> CapabilityGap:
    return _gap(
        scope=relative,
        missing_capability=f"scan:{framework}",
        reason=f"当前确定性扫描器不识别 {framework} 的入口声明",
        source_refs=(relative,),
    )


def _non_python_gap(relative: str, suffix: str) -> CapabilityGap:
    return _gap(
        scope=relative,
        missing_capability="scan:non_python",
        reason=f"当前扫描器只解析 Python AST，无法识别 {suffix} 文件中的业务入口",
        source_refs=(relative,),
    )


def _gap(
    *, scope: str, missing_capability: str, reason: str, source_refs: tuple[str, ...]
) -> CapabilityGap:
    raw = f"{scope}\x1f{missing_capability}\x1f{SCANNER_REVISION}".encode()
    return CapabilityGap(
        gap_id="gap-" + hashlib.sha256(raw).hexdigest()[:16],
        scope=scope,
        missing_capability=missing_capability,
        capability_revision=SCANNER_REVISION,
        reason=reason,
        workaround="人工补充业务功能清单",
        blocks_full=True,
        source_refs=source_refs,
    )


def _function_facts(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Facts:
    arguments = node.args
    parameters = tuple(
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *([arguments.vararg] if arguments.vararg is not None else []),
            *([arguments.kwarg] if arguments.kwarg is not None else []),
        )
    )
    calls = _call_names(node.body)
    calls += _call_names([*arguments.defaults, *arguments.kw_defaults])
    calls += tuple(
        _call_name(dec.func)
        for dec in node.decorator_list
        if isinstance(dec, ast.Call)
    )
    return _Facts(
        parameters=parameters,
        call_names=tuple(dict.fromkeys(call for call in calls if call)),
        decorator_names=tuple(_dotted(dec) for dec in node.decorator_list),
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )


def _class_facts(node: ast.ClassDef) -> _Facts:
    parameters: list[str] = []
    calls: list[str] = []
    decorators: list[str] = []
    is_async = False
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts = _function_facts(child)
            parameters.extend(facts.parameters)
            calls.extend(facts.call_names)
            decorators.extend(facts.decorator_names)
            is_async = is_async or facts.is_async
    return _Facts(
        parameters=tuple(dict.fromkeys(parameters)),
        call_names=tuple(dict.fromkeys(calls)),
        decorator_names=tuple(dict.fromkeys(decorators)),
        is_async=is_async,
    )


def _call_names(nodes: Iterable[ast.AST | None]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        if node is None:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _call_name(child.func)
            if name:
                names.append(name)
            for argument in child.args:
                argument_name = _call_name(argument)
                if argument_name:
                    names.append(argument_name)
            for keyword in child.keywords:
                keyword_name = _call_name(keyword.value)
                if keyword_name:
                    names.append(keyword_name)
    return names


def _call_name(node: ast.expr) -> str:
    return _dotted(node)


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    if isinstance(node, ast.Subscript):
        return _dotted(node.value)
    return ""


def _root_parts(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_root_parts(node.value), node.attr)
    if isinstance(node, ast.Call):
        return _root_parts(node.func)
    if isinstance(node, ast.Subscript):
        return _root_parts(node.value)
    return ()


def _looks_like_route_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not isinstance(target, ast.Attribute):
        return False
    return target.attr.lower() in _HTTP_METHOD_DECORATORS | _ROUTE_DECORATORS


def _route_spec(decorator: ast.expr) -> _RouteSpec | None:
    call = decorator if isinstance(decorator, ast.Call) else None
    target = call.func if call is not None else decorator
    if not isinstance(target, ast.Attribute):
        return None
    attribute = target.attr.lower()
    if attribute not in _HTTP_METHOD_DECORATORS | _ROUTE_DECORATORS:
        return None
    parts = tuple(part.lower() for part in _root_parts(target))
    if not any(
        part in _ROUTE_ROOTS
        or part.endswith("router")
        or part.endswith("_app")
        or part.endswith("app")
        or part.endswith("blueprint")
        for part in parts
    ):
        return None
    path: str | None = None
    methods: tuple[str, ...]
    if attribute in _HTTP_METHOD_DECORATORS:
        methods = (attribute.upper(),)
    else:
        methods = _methods_keyword(call) if call is not None else ()
        if not methods:
            methods = ("GET",)
    if call is not None:
        path = _first_string(call) or _keyword_string(call, "path") or _keyword_string(
            call, "rule"
        )
    return _RouteSpec(path=path, methods=methods, line=_line(decorator))


def _methods_keyword(call: ast.Call | None) -> tuple[str, ...]:
    if call is None:
        return ()
    for keyword in call.keywords:
        if keyword.arg != "methods":
            continue
        value = keyword.value
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            methods = tuple(
                element.value.upper()
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
            return tuple(dict.fromkeys(methods))
    return ()


def _first_string(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _keyword_string(node: ast.Call, name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None


def _is_main_guard(node: ast.expr) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    if len(node.ops) != 1 or not isinstance(node.comparators[0], ast.Constant):
        return False
    left = node.left
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(node.ops[0], ast.Eq)
        and node.comparators[0].value == "__main__"
    )


def _line(node: ast.AST) -> int:
    line = getattr(node, "lineno", None)
    return line if type(line) is int and line > 0 else 0


def _tokens(value: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("_", " ")
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", text) if token]


def _selection(values: Sequence[str]) -> tuple[str, ...]:
    selection: list[str] = []
    for value in values:
        _require_text(value, "selected_path")
        selection.append(_normalise_relative(value).rstrip("/"))
    return tuple(dict.fromkeys(selection))


def _matches_selection(relative: str, selection: Sequence[str]) -> bool:
    return not selection or any(
        item in {".", ""} or relative == item or relative.startswith(item + "/")
        for item in selection
    )


def _normalise_relative(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ProjectUnderstandingError("source path must be a nonempty string")
    return path.replace("\\", "/")


def _require_safe_relative(path: str) -> None:
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ProjectUnderstandingError("source path must be a nonempty string")
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise ProjectUnderstandingError(f"source path escapes its snapshot root: {path}")


def _ignored_path(relative: str) -> bool:
    parts = relative.lower().split("/")
    return any(part in _IGNORED_DIRECTORY_NAMES for part in parts[:-1])


def _is_test_path(relative: str) -> bool:
    parts = relative.lower().split("/")
    if any(part in _TEST_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


# ------------------------------------------------------------------ primitives


def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return _DIGEST_PREFIX + hashlib.sha256(raw).hexdigest()


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProjectUnderstandingError(f"{name} must be a nonempty string")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectUnderstandingError(f"{name} must be a nonempty string")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _reason_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProjectUnderstandingError("reason must be a string")
    return value


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ProjectUnderstandingError(f"{name} must be an integer")
    return value


def _float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectUnderstandingError(f"{name} must be a number")
    return float(value)


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ProjectUnderstandingError(f"{name} must be a boolean")
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProjectUnderstandingError(f"{name} must be an object")
    return value


def _mappings(value: object, name: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        raise ProjectUnderstandingError(f"{name} must be a list")
    items: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ProjectUnderstandingError(f"{name} must contain objects")
        items.append(item)
    return tuple(items)


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProjectUnderstandingError(f"{name} must be a list")
    return tuple(_text(item, name) for item in value)


def _rebase(understanding: ProjectUnderstanding, revision: int) -> ProjectUnderstanding:
    if revision == understanding.revision:
        return understanding
    functions = tuple(
        replace(function, understanding_revision=revision)
        for function in understanding.catalog.functions
    )
    catalog = replace(
        understanding.catalog, revision=revision, functions=functions
    )
    intents = tuple(
        replace(intent, understanding_revision=revision)
        for intent in understanding.test_intents
    )
    return replace(understanding, revision=revision, catalog=catalog, test_intents=intents)


__all__ = [
    "GOAL_CANDIDATE_LIMIT",
    "PROJECT_UNDERSTANDING_AGGREGATE",
    "PROJECT_UNDERSTANDING_SCHEMA",
    "ProjectUnderstandingError",
    "ProjectUnderstandingService",
    "SCANNER_REVISION",
    "scan_sources",
    "understanding_content_digest",
    "understanding_from_payload",
    "understanding_identity",
    "understanding_summary",
    "understanding_to_payload",
]
