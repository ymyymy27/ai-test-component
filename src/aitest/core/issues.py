"""Issue creation and automatic drafting, with one signature-based dedupe rule."""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from aitest.config import known_secret_values
from aitest.core.evidence import evidence_path
from aitest.errors import AiTestError
from aitest.model_config import build_client, load_project_config
from aitest.models import (
    ConfidenceTier,
    Issue,
    IssueOccurrence,
    Plan,
    Run,
    case_item_ids,
    new_id,
)
from aitest.security import redact
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

Severity = Literal["P0", "P1", "P2", "P3"]

_ERROR_HINT = re.compile(
    r"(?i)(assertionerror|attributeerror|typeerror|valueerror|keyerror|runtimeerror"
    r"|assert\w*|traceback|error|exception|failed|timeout|失败|超时|异常)"
)
_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?"
)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]{4,}\b")
_RUN_PREFIX_RE = re.compile(r"aitest_run_[0-9a-fA-F_]+")
_TMP_DIR_RE = re.compile(
    r"(?:[A-Za-z]:)?[^\s\"']*(?:pytest-of-[^\s\\/]+|Temp[/\\][^\s\\/]+|runs[/\\]run_[0-9a-fA-F_]+)[/\\]"
)
_PORT_RE = re.compile(r"(127\.0\.0\.1|localhost):\d{4,5}")
_DURATION_RE = re.compile(r"\bin\s+\d+(?:\.\d+)?\s*s\b", re.IGNORECASE)
_LONG_HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
_DOC_ID_RE = re.compile(r"\bdoc_[0-9a-f]{6,}\b", re.IGNORECASE)
_LONG_NUM_RE = re.compile(r"\b\d{9,}\b")
_SUMMARY_RE = re.compile(
    r"^\d+\s+(?:passed|failed|error|errors|skipped|warning|warnings)\b", re.IGNORECASE
)
_EXC_RE = re.compile(
    r"\b(AssertionError|AttributeError|TypeError|ValueError|KeyError|RuntimeError"
    r"|TimeoutError|OSError|ImportError|ModuleNotFoundError|ConnectionError"
    r"|\w*(?:Error|Exception))\b"
)
_TRACEBACK_FILE_RE = re.compile(r'File "(?P<file>[^"]+?\.py)", line (?P<line>\d+)')
_PYTEST_FILE_RE = re.compile(
    r"(?<![\w./\\-])(?P<file>[A-Za-z0-9_][A-Za-z0-9_.\\/-]*\.py):(?P<line>\d+)"
)
_IGNORED_FRAME_PARTS = (
    "_stage_runner.py",
    "site-packages",
    "dist-packages",
    "\\pytest\\",
    "/pytest/",
    "_pytest",
    "<frozen",
)


def issues_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "issues"


def _normalize_stack_key(stack_key: str) -> str:
    """去除时间、UUID、内存地址、临时路径与随机端口，保障签名跨运行可复用。"""
    text = " ".join(stack_key.split())
    text = _TS_RE.sub("<TIME>", text)
    text = _UUID_RE.sub("<UUID>", text)
    text = _HEX_RE.sub("<ADDR>", text)
    text = _RUN_PREFIX_RE.sub("<RUN_DATA>", text)
    text = _TMP_DIR_RE.sub("<TMP>/", text)
    text = _PORT_RE.sub(r"\1:<PORT>", text)
    text = _LONG_HEX_RE.sub("<HEX>", text)
    text = _DOC_ID_RE.sub("<DOC_ID>", text)
    text = _LONG_NUM_RE.sub("<NUM>", text)
    text = _DURATION_RE.sub("in <DURATION>", text)
    return text.strip()


def _signature(
    case_id: str, error_type: str, stack_key: str, target_key: str = ""
) -> str:
    normalized = _normalize_stack_key(stack_key)
    prefix = target_key or case_id
    if not normalized:
        return f"{prefix}:{error_type}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{error_type}:{digest}"


def _is_summary_line(line: str) -> bool:
    """pytest 汇总行/分隔线不是稳定根因，不能进入问题签名。"""
    return bool(
        _SUMMARY_RE.match(line)
        or line.startswith(("=", "-", "short test summary", "FAILED ", "ERROR "))
    )


def _pick_error_line(lines: list[str]) -> str:
    # pytest 的 ``E + where`` 只是断言展开，真正的断言在更早的 ``E assert`` 行。
    for line in reversed(lines):
        if _is_summary_line(line):
            continue
        if line.startswith("E ") and (
            "assert" in line.lower() or _EXC_RE.search(line)
        ):
            return line
    for line in reversed(lines):
        if _is_summary_line(line):
            continue
        if line.startswith(("E ", "AssertionError", "Traceback", "Error:")) or _EXC_RE.search(
            line
        ):
            return line
    for line in reversed(lines):
        if _is_summary_line(line):
            continue
        if _ERROR_HINT.search(line):
            return line
    return ""


def _location_key(texts: list[str]) -> str:
    """用 traceback / pytest 的文件行做稳定签名，避免断言文案里的随机值。"""
    traceback_hits: list[tuple[str, str]] = []
    for text in texts:
        for match in _TRACEBACK_FILE_RE.finditer(text):
            path = match.group("file")
            if any(part in path.lower() for part in _IGNORED_FRAME_PARTS):
                continue
            traceback_hits.append((path, match.group("line")))
    if traceback_hits:
        path, line = traceback_hits[-1]
        return f"{Path(path).name}:{line}"
    for text in texts:
        for match in _PYTEST_FILE_RE.finditer(text):
            path = match.group("file")
            if any(part in path.lower() for part in _IGNORED_FRAME_PARTS):
                continue
            normalized_path = path.replace("\\", "/")
            return f"{normalized_path}:{match.group('line')}"
    return ""


def _case_error(run_dir: Path, case_id: str, run: Run) -> tuple[str, str]:
    """从失败用例证据中提取稳定的根因异常/断言行，用于问题签名去重。"""
    run_case = next((item for item in run.cases if item.case_id == case_id), None)
    if run_case is None:
        return "unknown", ""
    messages = [attempt.message for attempt in run_case.attempts if attempt.message]
    evidence: list[str] = []
    texts: list[str] = []
    for ref in run_case.evidence_refs[:5]:
        path = evidence_path(run_dir, ref)
        if path is None or not path.is_file():
            continue
        if path.suffix.lower() not in {".log", ".txt", ".json", ".out", ".err", ""}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        texts.append(text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        chosen = _pick_error_line(lines)
        if chosen:
            evidence.append(chosen)
    location = _location_key(texts)
    key = location or next((line for line in reversed(evidence) if line), "")
    if not key and messages:
        key = next(
            (line for line in reversed(messages) if line and not _is_summary_line(line)),
            "",
        )
    error_type = "unknown"
    for candidate in [*reversed(evidence), *reversed(messages)]:
        match = _EXC_RE.search(candidate or "")
        if match:
            error_type = match.group(1).lower()
            break
    return error_type, key[:400]


def _case_location(
    run_dir: Path, case_id: str, run: Run, project_root: Path
) -> tuple[str | None, int | None]:
    """从证据中提取项目内源码坐标；生成脚本位于运行目录，不当作源码坐标。"""
    run_case = next((item for item in run.cases if item.case_id == case_id), None)
    if run_case is None:
        return None, None
    try:
        root = project_root.resolve()
    except OSError:
        return None, None
    for ref in run_case.evidence_refs[:5]:
        path = evidence_path(run_dir, ref)
        if path is None or not path.is_file():
            continue
        if path.suffix.lower() not in {".log", ".txt", ".json", ".out", ".err", ""}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches: list[dict[str, Any]] = []
        for pattern in (_TRACEBACK_FILE_RE, _PYTEST_FILE_RE):
            matches = [match.groupdict() for match in pattern.finditer(text)]
            if matches:
                break
        for match in reversed(matches):
            raw = str(match.get("file") or "").strip()
            if not raw:
                continue
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = root / raw
            try:
                resolved = candidate.resolve()
                relative = resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if not resolved.is_file():
                continue
            return relative.as_posix(), int(match.get("line") or 0)
    return None, None


def add_issue(
    ws: Workspace,
    project_id: str,
    run_id: str,
    case_id: str,
    title: str,
    *,
    severity: Severity = "P2",
    error_type: str = "unknown",
    stack_key: str = "",
    evidence_refs: list[str] | None = None,
    run_dir: Path | None = None,
    steps: list[str] | None = None,
    expected: str = "",
    actual: str = "",
    item_no: str = "",
    item_text: str = "",
    unstable: bool = False,
    target_key: str = "",
    issue_index: dict[str, tuple[Path, Issue]] | None = None,
    confidence_tier: ConfidenceTier = "suspected",
    confidence_reason: str = "",
) -> dict[str, Any]:
    run: Run | None = None
    if run_dir is not None:
        try:
            run = read_model(run_dir / "run.json", Run)
        except AiTestError:
            run = None
    if run is not None and run_dir is not None and error_type == "unknown" and not stack_key:
        error_type, stack_key = _case_error(run_dir, case_id, run)
    source_file: str | None = None
    source_line: int | None = None
    if run is not None and run_dir is not None:
        try:
            project_root = Path(ws.load_project(project_id).root)
            source_file, source_line = _case_location(
                run_dir, case_id, run, project_root
            )
        except (AiTestError, OSError):
            source_file, source_line = None, None
    if run_dir is not None and not target_key and (run_dir / "plan.json").is_file():
        try:
            plan_snapshot = read_model(run_dir / "plan.json", Plan)
            plan_case = next(
                (item for item in plan_snapshot.cases if item.case_id == case_id), None
            )
            if plan_case is not None:
                ids = case_item_ids(plan_case)
                target_key = ids[0] if ids else ""
        except AiTestError:
            pass
    secrets = known_secret_values([load_project_config(ws, project_id)])
    title = redact(title, secrets)
    item_no = redact(item_no, secrets)
    item_text = redact(item_text, secrets)
    expected = redact(expected, secrets)
    actual = redact(actual, secrets)
    confidence_reason = redact(confidence_reason, secrets)
    steps = [redact(step, secrets) for step in (steps or [])]
    signature = _signature(case_id, error_type, stack_key, target_key=target_key)
    refs = list(evidence_refs or [])
    directory = issues_dir(ws, project_id)
    matched: tuple[Path, Issue] | None = None
    if issue_index is not None:
        matched = issue_index.get(signature)
    elif directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            if issue.signature == signature:
                matched = (path, issue)
                break
    if matched is not None:
        path, issue = matched
        issue.occurrences.append(
            IssueOccurrence(
                run_id=run_id, case_id=case_id, at=iso_now(), evidence_refs=refs
            )
        )
        issue.last_result = "failed"
        if issue.status == "closed":
            # A closed issue that fails again is no longer closed; keep the
            # occurrence and require a fresh retest before closing again.
            issue.status = "retest"
            issue.disposition = None
            issue.reason = "关闭后再次复现，待复测"
        if unstable:
            issue.unstable = True
        if issue.file is None and source_file:
            issue.file = source_file
            issue.line = source_line
        if confidence_tier == "confirmed" or not issue.confidence_reason:
            if confidence_tier == "confirmed" or issue.confidence_tier != "confirmed":
                issue.confidence_tier = confidence_tier
            if confidence_reason:
                issue.confidence_reason = confidence_reason
        issue.revision += 1
        write_model(path, issue)
        if issue_index is not None:
            issue_index[signature] = (path, issue)
        return {"issue_id": issue.issue_id, "existing": True, "revision": issue.revision}
    issue = Issue(
        issue_id=new_id("issue"),
        project_id=project_id,
        signature=signature,
        title=title,
        item_no=item_no,
        item_text=item_text,
        steps=list(steps or []),
        expected=expected,
        actual=actual,
        file=source_file,
        line=source_line,
        severity=severity,
        occurrences=[
            IssueOccurrence(run_id=run_id, case_id=case_id, at=iso_now(), evidence_refs=refs)
        ],
        last_result="failed",
        unstable=unstable,
        confidence_tier=confidence_tier,
        confidence_reason=confidence_reason,
    )
    issue_path = directory / f"{issue.issue_id}.json"
    write_model(issue_path, issue)
    if issue_index is not None:
        issue_index[signature] = (issue_path, issue)
    return {"issue_id": issue.issue_id, "existing": False, "revision": issue.revision}


def _load_issue_index(ws: Workspace, project_id: str) -> dict[str, tuple[Path, Issue]]:
    """单次扫描 issues/*.json 构造签名索引，避免多次创建/核查时 O(N^2) 重复读盘。"""
    index: dict[str, tuple[Path, Issue]] = {}
    directory = issues_dir(ws, project_id)
    if not directory.is_dir():
        return index
    for path in sorted(directory.glob("*.json")):
        try:
            issue = read_model(path, Issue)
        except AiTestError:
            continue
        index[issue.signature] = (path, issue)
    return index


def _mark_unstable(
    ws: Workspace,
    project_id: str,
    case_id: str,
    target_key: str = "",
    issue_index: dict[str, tuple[Path, Issue]] | None = None,
) -> None:
    """后续运行通过先前失败的用例/验收项时，标记疑似不稳定而不是静默消除。"""
    prefixes = [f"{case_id}:"]
    if target_key:
        prefixes.append(f"{target_key}:")
    items: list[tuple[Path, Issue]]
    if issue_index is not None:
        items = list(issue_index.values())
    else:
        directory = issues_dir(ws, project_id)
        if not directory.is_dir():
            return
        items = []
        for path in sorted(directory.glob("*.json")):
            try:
                items.append((path, read_model(path, Issue)))
            except AiTestError:
                continue
    for path, issue in items:
        if issue.status == "closed" or not any(
            issue.signature.startswith(prefix) for prefix in prefixes
        ):
            continue
        if issue.last_result != "passed":
            issue.last_result = "passed"
            issue.unstable = True
            issue.revision += 1
            write_model(path, issue)


def _excerpt(run_dir: Path, ref: str, secrets: list[str]) -> str:
    path = evidence_path(run_dir, ref)
    if path is None or not path.is_file():
        return ""
    try:
        return redact(path.read_text(encoding="utf-8", errors="replace")[:2000], secrets)
    except OSError:
        return ""


def _draft_issue(
    ws: Workspace,
    project_id: str,
    run_dir: Path,
    run: Run,
    plan: Plan,
    run_case: Any,
    secrets: list[str],
    client: Any = None,
) -> dict[str, Any]:
    case = next((item for item in plan.cases if item.case_id == run_case.case_id), None)
    messages = [attempt.message for attempt in run_case.attempts if attempt.message]
    context = {
        "case": (
            {
                "case_id": case.case_id,
                "title": case.title,
                "steps": case.steps,
                "expected": case.expected,
            }
            if case is not None
            else {"case_id": run_case.case_id}
        ),
        "result": run_case.result,
        "messages": messages,
        "evidence": [
            _excerpt(run_dir, ref, secrets) for ref in run_case.evidence_refs[:3]
        ],
    }
    resolved_client = build_client(ws, project_id) if client is None else client
    if resolved_client is not None:
        try:
            draft = resolved_client.draft_issue(context)
            if draft.get("title"):
                return draft
        except AiTestError:
            pass
    title = (
        (case.title if case is not None and case.title else "")
        or (f"用例失败：{case.expected}" if case is not None else f"用例失败：{run_case.case_id}")
    )
    return {
        "title": title,
        "steps": list(case.steps) if case is not None else [],
        "expected": case.expected if case is not None else "",
        "actual": messages[-1] if messages else "",
    }


def _previous_results_map(
    ws: Workspace, project_id: str, current_run_id: str
) -> dict[str, str]:
    """一次扫描历史运行，按时间选出每个 case_id 最近一次的运行结果。"""
    runs_dir = ws.project_dir(project_id) / "runs"
    if not runs_dir.is_dir():
        return {}
    latest_by_case: dict[str, tuple[str, str]] = {}
    for path in runs_dir.glob("*/run.json"):
        try:
            data = read_model(path, Run)
        except AiTestError:
            continue
        if data.run_id == current_run_id:
            continue
        for item in data.cases:
            prev = latest_by_case.get(item.case_id)
            if prev is None or data.started_at > prev[0]:
                latest_by_case[item.case_id] = (data.started_at, item.result)
    return {case_id: result for case_id, (_at, result) in latest_by_case.items()}


def prepare_issue_drafts(
    ws: Workspace,
    project_id: str,
    run_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """无锁并发预生成失败问题草稿与历史对照结果，避免在写锁内阻塞。"""
    run = read_model(run_dir / "run.json", Run)
    plan = read_model(run_dir / "plan.json", Plan)
    secrets = known_secret_values([load_project_config(ws, project_id)])
    previous = _previous_results_map(ws, project_id, run.run_id)
    failed = [rc for rc in run.cases if rc.result == "failed"]
    if not failed:
        return {}, previous
    client = build_client(ws, project_id)
    drafts: dict[str, dict[str, Any]] = {}
    workers = min(4, len(failed))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aitest-issue") as pool:
        future_map = {
            pool.submit(
                _draft_issue, ws, project_id, run_dir, run, plan, rc, secrets, client
            ): rc.case_id
            for rc in failed
        }
        for future, case_id in future_map.items():
            drafts[case_id] = future.result()
    return drafts, previous


def ensure_run_issues(ws: Workspace, project_id: str, run_dir: Path) -> list[str]:
    """Draft issues for a run's failed cases if post-processing has not done it.

    Report generation can race the worker's best-effort post-processing. Without
    this guard a report could truthfully say "failed" while its issue list is
    empty, which reads like no problem was recorded.
    """
    run = read_model(run_dir / "run.json", Run)
    if not any(case.result == "failed" for case in run.cases):
        return []
    directory = issues_dir(ws, project_id)
    covered: set[tuple[str, str]] = set()
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            for item in issue.occurrences:
                if item.run_id == run.run_id:
                    covered.add((item.run_id, item.case_id))
    return draft_failed_issues(ws, project_id, run_dir, covered)


def draft_failed_issues(
    ws: Workspace,
    project_id: str,
    run_dir: Path,
    covered: set[tuple[str, str]] | None = None,
    *,
    precomputed_drafts: dict[str, dict[str, Any]] | None = None,
    previous_results: dict[str, str] | None = None,
) -> list[str]:
    """Create draft issues for failed cases and flag inconsistent later passes."""
    run = read_model(run_dir / "run.json", Run)
    plan = read_model(run_dir / "plan.json", Plan)
    secrets = known_secret_values([load_project_config(ws, project_id)])
    issue_index = _load_issue_index(ws, project_id)
    prev_map = (
        previous_results
        if previous_results is not None
        else (
            _previous_results_map(ws, project_id, run.run_id)
            if any(rc.result == "failed" for rc in run.cases)
            else {}
        )
    )
    client = build_client(ws, project_id) if precomputed_drafts is None else None
    issue_ids: list[str] = []
    for run_case in run.cases:
        plan_case = next(
            (item for item in plan.cases if item.case_id == run_case.case_id), None
        )
        ids = case_item_ids(plan_case) if plan_case is not None else []
        item_id = ids[0] if ids else ""
        if run_case.result == "passed":
            _mark_unstable(
                ws,
                project_id,
                run_case.case_id,
                target_key=item_id,
                issue_index=issue_index,
            )
            continue
        if run_case.result != "failed":
            continue
        if covered and (run.run_id, run_case.case_id) in covered:
            continue
        draft = (
            precomputed_drafts[run_case.case_id]
            if precomputed_drafts is not None and run_case.case_id in precomputed_drafts
            else _draft_issue(ws, project_id, run_dir, run, plan, run_case, secrets, client)
        )
        previous = prev_map.get(run_case.case_id)
        item_text = plan.item_texts.get(item_id, "") if item_id else ""
        tier = run_case.confidence_tier or "suspected"
        result = add_issue(
            ws,
            project_id,
            run.run_id,
            run_case.case_id,
            draft["title"],
            item_text=item_text,
            steps=draft["steps"],
            expected=draft["expected"],
            actual=draft["actual"],
            evidence_refs=list(run_case.evidence_refs),
            run_dir=run_dir,
            unstable=previous == "passed",
            target_key=item_id,
            issue_index=issue_index,
            confidence_tier=tier,
            confidence_reason=run_case.confidence_reason,
        )
        issue_ids.append(str(result["issue_id"]))
    return issue_ids
