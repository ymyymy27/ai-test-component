"""One-shot run worker: executes cases, writes evidence and run state, then exits."""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
from pathlib import Path

from aitest.models import Attempt, Case, Plan, Run
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

MAX_FILE_BYTES = 20 * 1024 * 1024
_LOCK = threading.Lock()


def _save(run_path: Path, run: Run) -> None:
    with _LOCK:
        write_model(run_path, run)


def _touch(run_path: Path) -> None:
    with _LOCK:
        current = read_model(run_path, Run)
        current.heartbeat_at = iso_now()
        write_model(run_path, current)


def _save_evidence(run_dir: Path, name: str, data: bytes) -> str:
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    truncated = len(data) > MAX_FILE_BYTES
    payload = data[:MAX_FILE_BYTES] if truncated else data
    path = evidence_dir / name
    path.write_bytes(payload)
    if truncated:
        (evidence_dir / f"{name}.truncated").write_text(
            f"原始大小 {len(data)} 字节，已截断\n", encoding="utf-8"
        )
    return str(path.relative_to(run_dir)).replace("\\", "/")


def _execute(case: Case) -> tuple[str, list[bytes], str]:
    method = case.verify_method or ""
    if not method.startswith("command:"):
        return "error", [], f"暂不支持的验证方式：{method or '空'}"
    command = method[len("command:") :].strip()
    if not command:
        return "error", [], "命令为空"
    try:
        completed = subprocess.run(command, shell=True, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "blocked", [], "执行超时"
    except OSError as exc:
        return "error", [], f"命令无法执行：{exc}"
    result = "passed" if completed.returncode == 0 else "failed"
    outputs = [item for item in (completed.stdout, completed.stderr) if item]
    return result, outputs, ""


def _heartbeat(run_path: Path, stop: threading.Event) -> None:
    while not stop.wait(3.0):
        try:
            _touch(run_path)
        except Exception:
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)

    ws = Workspace(Path(args.workspace))
    run_dir = ws.project_dir(args.project) / "runs" / args.run
    run_path = run_dir / "run.json"
    plan = read_model(run_dir / "plan.json", Plan)
    run = read_model(run_path, Run)
    cases = {case.case_id: case for case in plan.cases}

    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(run_path, stop), daemon=True)
    beat.start()
    try:
        for index, run_case in enumerate(run.cases):
            if (run_dir / "cancel.requested").exists():
                for rest in run.cases[index:]:
                    rest.result = "not_run"
                run.status = "cancelled"
                break
            case = cases.get(run_case.case_id)
            if case is None:
                run_case.result = "error"
                run_case.attempts.append(
                    Attempt(attempt=1, result="error", message="计划中找不到该用例")
                )
                continue
            result, outputs, message = _execute(case)
            refs = [
                _save_evidence(run_dir, f"{case.case_id}-{position}.log", data)
                for position, data in enumerate(outputs)
            ]
            run_case.attempts.append(
                Attempt(
                    attempt=len(run_case.attempts) + 1,
                    result=result,  # type: ignore[arg-type]
                    started_at=iso_now(),
                    finished_at=iso_now(),
                    evidence_refs=refs,
                    message=message,
                )
            )
            run_case.result = result  # type: ignore[assignment]
            run_case.evidence_refs.extend(refs)
            run.evidence_refs.extend(refs)
            run.heartbeat_at = iso_now()
            _save(run_path, run)
        else:
            run.status = "completed"
    except Exception as exc:  # noqa: BLE001 - record unexpected failure, do not crash silently
        run.status = "execution_error"
        (run_dir / "worker-error.txt").write_text(str(exc), encoding="utf-8")
    finally:
        stop.set()
        run.heartbeat_at = iso_now()
        _save(run_path, run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
