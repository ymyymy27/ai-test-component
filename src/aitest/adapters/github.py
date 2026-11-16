"""Read-only GitHub adapter backed by the ``gh`` CLI.

Only ``gh api`` GET requests are issued. GitHub responses are untrusted input:
the returned change record is rebuilt from a small whitelist of fields, and
error messages never echo command output (which may contain credentials).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote

from aitest.errors import AiTestError

Runner = Callable[..., subprocess.CompletedProcess[str]]

_TIMEOUT_SECONDS = 30

_HOST_HINT = re.compile(r"Logged in to ([A-Za-z0-9][A-Za-z0-9.-]*)")
_HOST_FALLBACK = re.compile(r"\b([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")


def _unavailable(message: str) -> AiTestError:
    return AiTestError(
        "GITHUB_UNAVAILABLE",
        message,
        "安装 gh 并完成登录后重试",
    )


def _run(
    runner: Runner, args: list[str], *, quiet: bool = False
) -> subprocess.CompletedProcess[str] | None:
    try:
        result = runner(args, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        if quiet:
            return None
        raise _unavailable("无法执行 gh，请检查安装") from exc
    if result.returncode != 0:
        if quiet:
            return None
        raise _unavailable("gh 读取失败，请检查登录状态与仓库权限")
    return result


def _api_json(runner: Runner, path: str, *, paginate: bool = False) -> object:
    args = ["gh", "api"]
    if paginate:
        args.extend(["--paginate", "--slurp"])
    args.append(path)
    result = _run(runner, args)
    assert result is not None
    try:
        return cast(object, json.loads(result.stdout))
    except (TypeError, json.JSONDecodeError) as exc:
        raise _unavailable("gh 返回内容无法解析") from exc


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _nested_string(value: object, first: str, second: str) -> str:
    return _string(_mapping(_mapping(value).get(first)).get(second))


def _pull(item: object) -> dict[str, Any]:
    data = _mapping(item)
    return {
        "number": _integer(data.get("number")),
        "title": _string(data.get("title")),
        "state": _string(data.get("state")),
        "url": _string(data.get("html_url")),
        "head": _nested_string(data, "head", "ref"),
        "base": _nested_string(data, "base", "ref"),
        "author": _nested_string(data, "user", "login"),
        "merged": data.get("merged_at") is not None,
        "updated_at": _string(data.get("updated_at")),
    }


def _check(item: object) -> dict[str, Any]:
    data = _mapping(item)
    return {
        "name": _string(data.get("name")),
        "status": _string(data.get("status")),
        "conclusion": _string(data.get("conclusion")),
        "started_at": _string(data.get("started_at")),
        "completed_at": _string(data.get("completed_at")),
        "url": _string(data.get("details_url")) or _string(data.get("html_url")),
    }


def _deployment(item: object) -> dict[str, Any]:
    data = _mapping(item)
    return {
        "id": _integer(data.get("id")),
        "environment": _string(data.get("environment")),
        "ref": _string(data.get("ref")),
        "sha": _string(data.get("sha")),
        "created_at": _string(data.get("created_at")),
        "creator": _nested_string(data, "creator", "login"),
    }


def _check_rows(payload: object) -> list[object]:
    if isinstance(payload, dict):
        return _items(_mapping(payload).get("check_runs"))
    rows: list[object] = []
    for page in _items(payload):
        rows.extend(_items(_mapping(page).get("check_runs")))
    return rows


def _probe(runner: Runner, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    return _run(runner, args, quiet=True)


def _account(runner: Runner) -> str:
    result = _probe(runner, ["gh", "api", "user"])
    if result is None:
        return ""
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return ""
    return _string(_mapping(payload).get("login"))


def _host(runner: Runner) -> str:
    result = _probe(runner, ["gh", "auth", "status"])
    if result is None:
        return ""
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    text = f"{stdout}\n{stderr}"
    hinted = _HOST_HINT.search(text)
    if hinted is not None:
        return hinted.group(1)
    fallback = _HOST_FALLBACK.search(text)
    return fallback.group(1) if fallback is not None else ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_github(repo: str, ref: str, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Read a GitHub change record without writing anything.

    ``repo`` is ``owner/name`` and ``ref`` is a branch, tag, or commit. Only
    ``gh api`` GET calls are made; account and host probes are best effort.
    """

    repo_path = quote(repo, safe="/")
    ref_path = quote(ref, safe="/")
    ref_query = quote(ref, safe="")

    repo_payload = _mapping(_api_json(runner, f"repos/{repo_path}"))
    commit_payload = _mapping(_api_json(runner, f"repos/{repo_path}/commits/{ref_path}"))
    pulls_payload = _items(_api_json(runner, f"repos/{repo_path}/pulls?state=all"))
    sha = _string(commit_payload.get("sha"))
    if not sha:
        raise _unavailable("gh 返回的提交信息缺少 sha")
    checks_payload = _api_json(
        runner,
        f"repos/{repo_path}/commits/{quote(sha, safe='')}/check-runs",
        paginate=True,
    )
    deployments_payload = _items(
        _api_json(runner, f"repos/{repo_path}/deployments?ref={ref_query}")
    )

    prs = [_pull(item) for item in pulls_payload if isinstance(item, dict)]
    checks = [_check(item) for item in _check_rows(checks_payload) if isinstance(item, dict)]
    deployment = _deployment(deployments_payload[0]) if deployments_payload else None

    return {
        "repo": _string(repo_payload.get("full_name")) or repo,
        "branch": ref,
        "commit": sha,
        "prs": prs,
        "checks": checks,
        "deployment": deployment,
        "account": _account(runner),
        "host": _host(runner),
        "fetched_at": _now(),
    }
