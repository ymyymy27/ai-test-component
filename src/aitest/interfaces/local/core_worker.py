"""长生命周期唯一核心子进程入口，由 :class:`SystemProcessLauncher` 启动。

核心生命周期（A-02 闭合）
------------------------

1. 启动时经 :func:`aitest.bootstrap.assemble_workspace_core` 完成**统一装配**：
   启动恢复 → 文件底座 → B 用例经窄转接头自动接线 → C/D 注册表叠加；
   恢复 blocked 时拒绝服务（退出码 5）。
2. 在工作空间命名管道上接受**同会话同用户**对端连接，逐帧解析
   ``aitest.local/2.0`` 的 :class:`Command`，经 :meth:`LocalAPI.dispatch`
   派发到唯一写入者，并把 :class:`Response` 序列化回写；坏帧回协议错误，
   不吞消息、不断连。
3. 客户端断开**不再退出**：默认 ``--max-clients 0`` 表示长期唯一核心，
   关闭旧管道后用同一实例名重建管道，等待下一次连接（重连/保活）。
4. 终止条件三选一：达到有限 ``--max-clients``（测试用）；收到同管道的
   停机控制帧（:func:`shutdown_frame`，工作空间关闭端）；父进程消亡。
   父进程消亡不再被看门狗线程直接 ``os._exit``：看门狗只置退出请求并
   取消阻塞中的连接等待；主循环核实权威执行与输出。仍有活动或未知
   执行时保留核心继续服务，已核实收尾后才允许退出（退出码 6）。
5. 入口类型由核心依据对端**进程映像事实**判定：可信交互宿主进程映像
   （内置 ``trae.exe`` 及 ``--human-host-images`` 显式扩展）才归类
   ``human_ui``；取证失败/未知映像一律按最小权限归类 ``agent_relay``，
   连接帧自报入口类型在协议层即被拒绝（Command extra=forbid）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from aitest.application.errors import WorkspaceInUse
from aitest.contracts.commands import Command
from aitest.contracts.errors import ErrorDTO
from aitest.contracts.responses import Response
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

#: 停机控制键；不出现在 ``contracts.commands`` 的业务 Command 中
#: （``Command`` 为 extra=forbid，业务帧携带该键会直接判非法命令）。
CONTROL_KEY = "__aitest_control__"
_CONTROL_SHUTDOWN = "shutdown"
_FALLBACK_REQUEST_ID = "invalid-request"
_SHUTDOWN_REQUEST_ID = "core-shutdown"
_PARENT_EXIT_CODE = 6

#: 默认可信交互宿主映像（小写 basename）；可经 --human-host-images 扩展。
#: 只有这些进程发起的连接才允许归类 human_ui 并执行 HUMAN_ACTIONS；
#: 其余（python.exe 上的 MCP/agent_relay、未知映像、取证失败）一律
#: 按 agent_relay 最小权限归类（A-02）。
_BUILT_IN_HUMAN_HOST_IMAGES = frozenset({"trae.exe"})


@dataclass(frozen=True, slots=True)
class ShutdownControl:
    """一帧停机请求；``request_id`` 用于回执关联。"""

    request_id: str


def shutdown_frame() -> bytes:
    """停机控制帧的载荷（管道层再加 4 字节长度前缀）。"""
    return json.dumps(
        {CONTROL_KEY: _CONTROL_SHUTDOWN, "request_id": _SHUTDOWN_REQUEST_ID},
        ensure_ascii=False,
    ).encode("utf-8")


def serialize_response(response: Response) -> bytes:
    return response.model_dump_json().encode("utf-8")


def _safe_request_id(obj: object) -> str:
    if isinstance(obj, dict):
        value = obj.get("request_id")
        if isinstance(value, str) and 1 <= len(value) <= 128:
            return value
    return _FALLBACK_REQUEST_ID


def _protocol_error(api: LocalAPI, request_id: str, code: str, message: str) -> Response:
    return Response(
        request_id=request_id,
        instance_id=api.instance_id,
        workspace_id=api.workspace_id,
        error=ErrorDTO(
            code=code,
            message=api._safe_message(ValueError(message)),
            next_step="检查帧格式与 aitest.local/2.0 合同",
        ),
    )


def dispatch_frame(api: LocalAPI, session: Session, payload: bytes) -> Response | ShutdownControl:
    """解析并派发一帧；纯函数边界，便于不依赖命名管道做单元测试。

    - 非法 UTF-8 / JSON / 非对象帧 → ``MALFORMED_MESSAGE`` 错误响应；
    - 停机控制帧 → :class:`ShutdownControl`，未知控制动作 → 协议错误；
    - 不符合 :class:`Command` 合同的帧 → ``INVALID_COMMAND`` 错误响应；
    - 正常帧交 ``api.dispatch``，业务幂等与错误归一在该层完成。
    """
    try:
        obj: object = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _protocol_error(
            api, _FALLBACK_REQUEST_ID, "MALFORMED_MESSAGE", "帧不是合法 UTF-8 JSON"
        )
    if not isinstance(obj, dict):
        return _protocol_error(
            api, _FALLBACK_REQUEST_ID, "MALFORMED_MESSAGE", "帧载荷必须是 JSON 对象"
        )

    if CONTROL_KEY in obj:
        action = obj.get(CONTROL_KEY)
        if action == _CONTROL_SHUTDOWN:
            return ShutdownControl(request_id=_safe_request_id(obj))
        return _protocol_error(
            api,
            _safe_request_id(obj),
            "UNKNOWN_CONTROL",
            f"未知核心控制动作: {action!r}",
        )

    try:
        command = Command.model_validate(obj)
    except ValidationError as error:
        return _protocol_error(
            api,
            _safe_request_id(obj),
            "INVALID_COMMAND",
            f"命令不符合合同: {error.error_count()} 个字段错误",
        )
    return api.dispatch(command, session)


def classify_entry_kind(image_basename: str | None, human_host_images: frozenset[str]) -> EntryKind:
    """按对端进程映像事实归类入口；未知/取证失败按最小权限归类。"""
    if image_basename and image_basename in human_host_images:
        return EntryKind.HUMAN_UI
    return EntryKind.AGENT_RELAY


def prepare_core_shutdown(
    blocker: Callable[[], str | None] | None,
    seal: Callable[[], tuple[str, ...]],
) -> str | None:
    """No collection, blanket stop or seal of live output on a mere exit request."""
    try:
        if blocker is None:
            return "退出核实能力不可用，核心保留并继续服务"
        reason = blocker()
        if reason is not None:
            return reason if isinstance(reason, str) and reason else "退出状态无法核实"
        seal()
        return None
    except Exception:
        return "退出材料保存或核实失败，核心保留并继续服务"


def serve_connection(
    server: object,
    api: LocalAPI,
    *,
    connection_no: int,
    entry_kind: EntryKind = EntryKind.AGENT_RELAY,
    shutdown_blocker: Callable[[], str | None] | None = None,
    on_wait: Callable[[], None] | None = None,
) -> str:
    """处理一条已通过身份核对的连接；返回 ``shutdown`` 或 ``disconnected``。

    ``server`` 只需具备 ``read_message/write_message``（真实管道或测试桩），
    因此整条派发回路可在非 Windows 环境单测。``entry_kind`` 由调用方依据
    对端进程事实归类，不从连接帧接受（A-02）。
    """
    prefix = "human-ui" if entry_kind == EntryKind.HUMAN_UI else "agent-relay"
    session = Session(
        session_id=f"{prefix}-{connection_no}-{uuid4().hex[:12]}",
        entry_kind=entry_kind,
        interactive=entry_kind is EntryKind.HUMAN_UI,
    )
    try:
        while True:
            try:
                if on_wait is not None:
                    payload = server.read_message(on_wait=on_wait)  # type: ignore[attr-defined]
                else:
                    payload = server.read_message()  # type: ignore[attr-defined]
            except Exception:
                # 对端关闭/管道故障：本连接结束，核心继续存活等待重连。
                return "disconnected"
            outcome = dispatch_frame(api, session, payload)
            if isinstance(outcome, ShutdownControl):
                reason = prepare_core_shutdown(shutdown_blocker, lambda: ())
                if reason is not None:
                    response = _protocol_error(api, outcome.request_id, "CORE_DRAINING", reason)
                    with suppress(Exception):
                        server.write_message(serialize_response(response))  # type: ignore[attr-defined]
                    continue
                ack = Response(
                    request_id=outcome.request_id,
                    instance_id=api.instance_id,
                    workspace_id=api.workspace_id,
                    result={"status": "shutting_down"},
                )
                # 回执写不进去也必须停机：对端已经不在，停机语义不受影响。
                with suppress(Exception):
                    server.write_message(serialize_response(ack))  # type: ignore[attr-defined]
                return "shutdown"
            try:
                if on_wait is not None:
                    on_wait()
                server.write_message(  # type: ignore[attr-defined]
                    serialize_response(outcome)
                )
            except Exception:
                return "disconnected"
    finally:
        api.close_session(session)


def watch_parent(
    parent_pid: int,
    *,
    is_alive: Callable[[int], bool],
    stop_event: threading.Event,
    interval_seconds: float = 1.0,
) -> bool:
    """在连接等待期间也能发现父进程消亡的看门狗循环（纯函数边界）。

    父进程消失返回 ``True``；``stop_event`` 被设置返回 ``False``。
    """
    while not stop_event.is_set():
        if not is_alive(parent_pid):
            return True
        stop_event.wait(interval_seconds)
    return False


@dataclass
class ShutdownCoordinator:
    """父进程消亡/停机请求的协作器（A-02）。

    看门狗线程不再直接 ``os._exit``：只置退出请求；若核心正阻塞等待
    连接，调用已绑定的取消回调关闭监听管道以解除阻塞；若正在服务活动
    连接则不取消，由主循环在命令边界排空后自行退出。
    """

    parent_pid: int
    is_alive: Callable[[int], bool]
    interval_seconds: float = 1.0
    event: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    _guard: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cancel_accept: Callable[[], None] | None = field(default=None, repr=False)

    def bind_accept(self, cancel_accept: Callable[[], None]) -> None:
        with self._guard:
            self._cancel_accept = cancel_accept
            requested = self.event.is_set()
        if requested:
            with suppress(Exception):
                cancel_accept()

    def unbind_accept(self) -> None:
        with self._guard:
            self._cancel_accept = None

    def request_exit(self, reason: str) -> None:
        with self._guard:
            self.reason = reason
            cancel = self._cancel_accept
            self.event.set()
        if cancel is not None:
            with suppress(Exception):
                cancel()

    def retain_activity(self) -> None:
        """The lost parent cannot authorize interrupting an active or unknown execution."""
        with self._guard:
            self.reason = "parent_exited_activity_retained"
            self.event.clear()

    @property
    def exit_requested(self) -> bool:
        return self.event.is_set()

    def watch_forever(self) -> None:
        """看门狗线程体：发现父进程消亡即请求排空退出，不直接终止进程。"""
        if watch_parent(
            self.parent_pid,
            is_alive=self.is_alive,
            stop_event=self.event,
            interval_seconds=self.interval_seconds,
        ):
            self.request_exit("parent_exited")

    def start_watchdog(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.watch_forever,
            name="aitest-core-parent-watch",
            daemon=True,
        )
        thread.start()
        return thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aitest-core-worker")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--launch-claim", action="store_true")
    parser.add_argument("--instance-id-file", default=".core-instance-id")
    parser.add_argument(
        "--max-clients",
        type=int,
        default=0,
        help="处理多少次连接后退出；0 表示长期唯一核心（默认），有限值供测试",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=0,
        help="启动器进程 PID；父进程消亡后核心在连接边界自行退出，0 表示不探测",
    )
    parser.add_argument(
        "--connection-endpoint",
        default=None,
        help="test_connection 的真实目标端点（http/https）；缺省不挂外网能力",
    )
    parser.add_argument(
        "--human-host-images",
        default="",
        help=(
            "逗号分隔的可信交互宿主映像 basename（小写，如 Trae.exe）；"
            "这些进程的连接归类 human_ui，其余一律 agent_relay"
        ),
    )
    args = parser.parse_args(argv)

    if not sys.platform.startswith("win"):
        print("命名管道仅 Windows", file=sys.stderr)
        return 2
    if args.max_clients < 0:
        print("max-clients 不能为负", file=sys.stderr)
        return 2

    human_host_images = set(_BUILT_IN_HUMAN_HOST_IMAGES)
    human_host_images.update(
        item.strip().lower() for item in args.human_host_images.split(",") if item.strip()
    )

    # 延迟导入：bootstrap 反向延迟导入本模块（shutdown_endpoint），
    # 且非 Windows 平台无需触发命名管道/kernel32 加载。
    from aitest.bootstrap import (
        CoreAssemblyBlocked,
        assemble_workspace_core,
        await_core_launch_claim,
        registered_use_cases,
    )
    from aitest.interfaces.local.pipe import (
        NamedPipeServer,
        PeerRejected,
        PipeUnavailable,
        process_exists,
    )

    workspace_root = Path(args.workspace_root)
    if args.launch_claim:
        try:
            if not await_core_launch_claim(
                workspace_root,
                args.workspace_id,
                args.instance_id,
                instance_id_file=args.instance_id_file,
            ):
                return 7
        except (OSError, WorkspaceInUse):
            return 7
    try:
        # --workspace-id 是**管道地址**（可信宿主按工作空间身份命名，
        # 但运输层不承载身份校验）；持久工作空间身份由工作空间根目录的
        # workspace.json 唯一确定，装配时按根目录打开，不拿管道名做校验。
        # C/D 默认装配：全局注册表快照随唯一装配路径注入（A-02）。
        assembly = assemble_workspace_core(
            workspace_root,
            instance_id=args.instance_id,
            extra_handlers=registered_use_cases(),
            connection_endpoint=args.connection_endpoint,
        )
    except CoreAssemblyBlocked as error:
        print(str(error), file=sys.stderr)
        return 5
    api = assembly.api
    next_work_tick = 0.0
    next_work_warning = 0.0

    def continue_work() -> None:
        nonlocal next_work_tick, next_work_warning
        now = time.monotonic()
        if now < next_work_tick:
            return
        next_work_tick = now + 0.25
        if assembly.continue_work is not None:
            try:
                assembly.continue_work()
            except Exception:
                if now >= next_work_warning:
                    next_work_warning = now + 60.0
                    print("已登记运行续行受阻，原材料与活动状态保留", file=sys.stderr)

    coordinator: ShutdownCoordinator | None = None
    if args.parent_pid:
        coordinator = ShutdownCoordinator(parent_pid=args.parent_pid, is_alive=process_exists)
        coordinator.start_watchdog()

    def shutdown_blocker() -> str | None:
        return prepare_core_shutdown(assembly.shutdown_blocker, assembly.seal_inflight)

    def _parent_lost_exit() -> int | None:
        reason = shutdown_blocker()
        if reason is not None:
            assert coordinator is not None
            coordinator.retain_activity()
            print(reason, file=sys.stderr)
            return None
        return _PARENT_EXIT_CODE

    try:
        served = 0
        connection_no = 0
        while True:
            if coordinator is not None and coordinator.exit_requested:
                code = _parent_lost_exit()
                if code is not None:
                    return code
            if args.max_clients and served >= args.max_clients:
                if shutdown_blocker() is None:
                    return 0
                args.max_clients = 0
            try:
                server = NamedPipeServer(args.workspace_id, instance_id=args.instance_id)
                server.start()
            except PipeUnavailable:
                # 同名管道仍被另一核心持有：唯一核心语义，本进程退出。
                if shutdown_blocker() is None:
                    return 3
                time.sleep(0.1)
                continue
            try:
                if coordinator is not None:
                    coordinator.bind_accept(server.cancel_wait)
                try:
                    server.wait_for_client(on_wait=continue_work)
                    server.validate_peer()
                except PeerRejected:
                    # 拒绝对端不致命，关闭本管道后继续接受下一连接；
                    # 拒绝期间父进程可能已消亡，循环顶部统一检查。
                    continue
                except PipeUnavailable:
                    if coordinator is not None and coordinator.exit_requested:
                        code = _parent_lost_exit()
                        if code is not None:
                            return code
                        continue
                    raise
                finally:
                    if coordinator is not None:
                        coordinator.unbind_accept()
                # 对端身份核对通过后按进程映像事实归类入口（A-02）。
                image_basename = server.peer_process_basename()
                entry_kind = classify_entry_kind(image_basename, frozenset(human_host_images))
                connection_no += 1
                outcome = serve_connection(
                    server,
                    api,
                    connection_no=connection_no,
                    entry_kind=entry_kind,
                    shutdown_blocker=shutdown_blocker,
                    on_wait=continue_work,
                )
                served += 1
                if outcome == "shutdown":
                    return 0
            except PipeUnavailable:
                # 等待/核对/服务阶段管道故障：客户端断开已在 serve_connection
                # 内部归一，能到这里的是服务端自身故障，退出交宿主重新拉起。
                if shutdown_blocker() is None:
                    return 4
                time.sleep(0.1)
            finally:
                server.close()
    finally:
        assembly.lifetime_lock.release()


if __name__ == "__main__":
    sys.exit(main())
