"""G3b recheck driver: AC04/AC06/AC07/AC12 on the current code.

Runs the real CLI against a dedicated workspace and a fixture project, and
writes every command response next to this file. The assertions at the end are
the evidence summary; a failed assertion exits non-zero and no acceptance result
may be registered as verified.
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PROJECT_ROOT = HERE / "project"
WORKSPACE = Path(os.environ["APPDATA"]) / "aitest" / "workspaces" / "g3b"
REQUESTS = HERE / ".requests"
SCRIPT = (HERE / "playwright_shot.py").read_text(encoding="utf-8")


def call(
    label: str,
    command: str,
    payload: dict[str, Any],
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    REQUESTS.mkdir(parents=True, exist_ok=True)
    envelope = {
        "command": command,
        "request_id": f"g3b-{label}-{uuid.uuid4().hex[:8]}",
        "payload": payload,
    }
    request = REQUESTS / f"{label}.json"
    request.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    argv = [
        sys.executable,
        "-m",
        "aitest.cli",
        command,
        "--workspace",
        str(WORKSPACE),
        "--input",
        str(request),
    ]
    if confirm:
        argv.append("--confirm")
    completed = subprocess.run(
        argv, cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=900
    )
    try:
        response = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        response = {
            "ok": False,
            "error": {"code": "NO_JSON", "message": completed.stdout or completed.stderr},
        }
    response["_returncode"] = completed.returncode
    (HERE / f"{label}.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{label}: ok={response.get('ok')} rc={completed.returncode}")
    if not response.get("ok"):
        print(json.dumps(response, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    return response["data"]


def wait_for_run(project_id: str, run_id: str, label: str) -> dict[str, Any]:
    deadline = time.time() + 600
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = call(label, "run.status", {"project_id": project_id, "run_id": run_id})
        except SystemExit:
            time.sleep(1.0)
            continue
        if last["run"]["status"] != "running":
            return last
        time.sleep(1.0)
    raise SystemExit(f"{run_id} 未在 600 秒内结束")


def task(project_id: str, title: str, items: list[str]) -> dict[str, Any]:
    return call(
        f"{title}-task",
        "task.add",
        {
            "project_id": project_id,
            "title": title,
            "acceptance_items": [{"text": text, "observable": True} for text in items],
        },
    )


def main() -> None:
    project = call("project-add", "project.add", {"root": str(PROJECT_ROOT)})
    project_id = project["project_id"]

    # -- AC04: incomplete delivery blocks build-dependent cases -------------
    ac04_task = task(project_id, "ac04", ["构建产物可用", "登录页面可打开"])
    ac04_items = [item["item_id"] for item in ac04_task["acceptance_items"]]
    delivery = call(
        "ac04-delivery",
        "delivery.save",
        {
            "project_id": project_id,
            "task_id": ac04_task["task_id"],
            "done": ["改完登录页"],
        },
    )
    ac04_plan = call(
        "ac04-plan",
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": ac04_task["task_id"],
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "acceptance_item_id": ac04_items[0],
                    "layer": "L2",
                    "expected": "构建成功",
                    "expectation_source": "构建产物可用",
                    "verify_method": "command: echo build-ok",
                    "depends_on": "build",
                },
                {
                    "acceptance_item_id": ac04_items[1],
                    "layer": "L2",
                    "expected": "登录页面可打开",
                    "expectation_source": "登录页面可打开",
                    "verify_method": "command: echo login-ok",
                },
            ],
        },
    )
    started = call(
        "ac04-run-start",
        "run.start",
        {"project_id": project_id, "plan_id": ac04_plan["plan_id"]},
        confirm=True,
    )
    ac04 = wait_for_run(project_id, started["run_id"], "ac04-run-status")
    cases = ac04["run"]["cases"]
    assert delivery["complete"] is False, delivery
    assert cases[0]["result"] == "blocked", cases
    assert cases[0]["attempts"][0]["message"] == "交付信息不完整：缺运行方式或版本"
    assert cases[1]["result"] == "passed", cases
    print("AC04 pass: complete=false -> build dependent blocked, independent passed")

    # -- AC06: uncovered list = applicable items outside the selected scope --
    ac06_task = task(project_id, "ac06", ["下单成功", "库存扣减一次", "订单可以取消"])
    ac06_items = [item["item_id"] for item in ac06_task["acceptance_items"]]
    ac06_plan = call(
        "ac06-plan",
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": ac06_task["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [ac06_items[0]]},
            "cases": [
                {
                    "acceptance_item_id": ac06_items[0],
                    "layer": "L2",
                    "expected": "命令成功",
                    "expectation_source": "下单成功",
                    "verify_method": "command: echo order-ok",
                }
            ],
        },
    )
    ac06_started = call(
        "ac06-run-start",
        "run.start",
        {"project_id": project_id, "plan_id": ac06_plan["plan_id"]},
        confirm=True,
    )
    ac06_status = wait_for_run(project_id, ac06_started["run_id"], "ac06-run-status")
    ac06_report = call(
        "ac06-report",
        "report.generate",
        {"project_id": project_id, "run_id": ac06_started["run_id"]},
    )
    expected_uncovered = [
        f"{ac06_items[1]}：库存扣减一次",
        f"{ac06_items[2]}：订单可以取消",
    ]
    assert ac06_report["coverage"] == {"selected": 1, "applicable": 3}, ac06_report
    assert ac06_report["uncovered_items"] == expected_uncovered, ac06_report
    assert ac06_status["uncovered_items"] == expected_uncovered, ac06_status
    print("AC06 pass: report and run.status list the two unselected applicable items")

    # -- AC07: real model expectation sources align with the item text ------
    ac07_task = task(project_id, "ac07", ["登录成功后跳转到首页", "错误密码提示用户名或密码错误"])
    ac07_items = [item["item_id"] for item in ac07_task["acceptance_items"]]
    ac07_texts = [item["text"] for item in ac07_task["acceptance_items"]]
    ac07_plan = call(
        "ac07-plan",
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": ac07_task["task_id"],
            "scope": {"preset": "all_applicable"},
        },
    )
    ac07_shown = call(
        "ac07-plan-show",
        "plan.show",
        {"project_id": project_id, "plan_id": ac07_plan["plan_id"]},
    )["plan"]
    sources = [case["expectation_source"] for case in ac07_shown["cases"]]
    for case in ac07_shown["cases"]:
        item_texts = {
            ac07_items[0]: ac07_texts[0],
            ac07_items[1]: ac07_texts[1],
        }
        assert case["expectation_verified"] is True, case
        assert case["expectation_source"] == item_texts[case["acceptance_item_id"]], case
    assert ac07_plan["unverified_cases"] == [], ac07_plan
    print(f"AC07 pass: {len(ac07_shown['cases'])} generated cases all quote the item text")

    # -- AC12: real Playwright screenshots become run evidence --------------
    ac12_task = task(project_id, "ac12", ["静态订单页显示两条订单"])
    ac12_item = ac12_task["acceptance_items"][0]["item_id"]
    ac12_plan = call(
        "ac12-plan",
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": ac12_task["task_id"],
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "acceptance_item_id": ac12_item,
                    "layer": "L3",
                    "expected": "页面显示订单列表并生成截图",
                    "expectation_source": "静态订单页显示两条订单",
                    "verify_method": "script:shot.py",
                    "script_name": "shot.py",
                    "script_content": SCRIPT,
                }
            ],
        },
    )
    ac12_started = call(
        "ac12-run-start",
        "run.start",
        {"project_id": project_id, "plan_id": ac12_plan["plan_id"]},
        confirm=True,
    )
    ac12 = wait_for_run(project_id, ac12_started["run_id"], "ac12-run-status")
    run_case = ac12["run"]["cases"][0]
    refs = run_case["evidence_refs"]
    png_refs = [ref for ref in refs if ref.endswith(".png")]
    assert run_case["result"] == "passed", run_case
    assert any(ref.endswith("home.png") for ref in png_refs), png_refs
    assert any(ref.endswith("detail.png") for ref in png_refs), png_refs
    run_dir = WORKSPACE / "projects" / project_id / "runs" / ac12_started["run_id"]
    assert (run_dir / "generated" / "shot.py").is_file(), "脚本未落盘"
    sizes = {}
    for ref in png_refs:
        data = (run_dir / ref).read_bytes()
        assert data.startswith(b"\x89PNG"), ref
        assert len(data) > 1000, ref
        sizes[ref] = len(data)
    print(f"AC12 pass: evidence {sizes}")

    # -- service config: name -> config mapping is understood --------------
    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *args, directory=str(PROJECT_ROOT), **kwargs
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        worker_marker = HERE / "worker-service-started.txt"
        worker_marker.unlink(missing_ok=True)
        call(
            "service-config",
            "project.config",
            {
                "project_id": project_id,
                "action": "update",
                "patch": {
                    "services": {
                        "api": {
                            "health": f"http://127.0.0.1:{server.server_address[1]}/index.html",
                        },
                        "worker": {
                            "health": "http://127.0.0.1:1/never",
                            "start": f'"python" -c "import pathlib; pathlib.Path(r\'{worker_marker}\').write_text(\'ran\')"',
                        },
                    }
                },
            },
        )
        svc_task = task(project_id, "service-shape", ["服务配置形状可识别"])
        svc_item = svc_task["acceptance_items"][0]["item_id"]
        svc_plan = call(
            "service-plan",
            "plan.generate",
            {
                "project_id": project_id,
                "task_id": svc_task["task_id"],
                "scope": {"preset": "all_applicable"},
                "cases": [
                    {
                        "acceptance_item_id": svc_item,
                        "layer": "L2",
                        "expected": "命令成功",
                        "expectation_source": "服务配置形状可识别",
                        "verify_method": "command: echo service-ok",
                    }
                ],
            },
        )
        svc_started = call(
            "service-run-start",
            "run.start",
            {"project_id": project_id, "plan_id": svc_plan["plan_id"]},
            confirm=True,
        )
        svc = wait_for_run(project_id, svc_started["run_id"], "service-run-status")
    finally:
        server.server_close()
    assert svc["run"]["service_name"] == "api", svc["run"]
    assert svc["run"]["service_started"] is False, svc["run"]
    assert not worker_marker.exists(), "worker 用了映射里的第二个服务"
    print("service pass: mapping shape selects the first service and records its name")

    summary = {
        "workspace": str(WORKSPACE),
        "project_root": str(PROJECT_ROOT),
        "ac04": {"run_id": started["run_id"], "results": [c["result"] for c in cases]},
        "ac06": {
            "run_id": ac06_started["run_id"],
            "uncovered_items": ac06_report["uncovered_items"],
        },
        "ac07": {"plan_id": ac07_plan["plan_id"], "sources": sources},
        "ac12": {
            "run_id": ac12_started["run_id"],
            "evidence_refs": png_refs,
            "bytes": sizes,
        },
        "service_config": {
            "run_id": svc_started["run_id"],
            "service_name": svc["run"]["service_name"],
            "service_started": svc["run"]["service_started"],
            "second_service_started": worker_marker.exists(),
        },
    }
    (HERE / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("SUMMARY written to summary.json")


if __name__ == "__main__":
    main()
