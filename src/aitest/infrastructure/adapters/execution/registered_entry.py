"""默认执行端口：把已验证的 `ExecutionRequest` 绑成登记命令并真正启动进程。

架构02《执行与证据》第 15/122 行把 `ExecutionPort` 定为
`start(request)`／`inspect(handle)`／`collect(handle, cursor)`／`request_stop(handle)`，
并要求它**绑定固定快照、登记入口、参数数组与环境**，且**命令使用登记入口及参数数组，
禁止把模型或正文拼成 shell**。

`CommandAdapter` 需要先 `register(CommandRegistration)`，而 `register` 不在
`ExecutionPort` 协议里（协议只有上述四个方法），应用层也拿不到它。因此由本适配器在
**首次 `start` 时**从请求派生登记项——请求本身已由准入链核对过（实际入口、实际解释器、
来源核验、固定 workdir），所以它的 `entrypoint`／`materialized_snapshot_ref` 是可信任的
冻结事实，正是合同要求端口绑定的那几项。

**环境白名单刻意取空**：只保留适配器自设的 `PYTHONIOENCODING`／`PYTHONUTF8`
（见 `CommandAdapter.start`）。`ExecutionSourceBinding.allowed_env_keys` 目前**没有**
进入 `ExecutionRequest`，在其进入前宁可少传环境变量，也不猜。
"""

from __future__ import annotations

from pathlib import Path

from aitest.domain.execution.runs import (
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionRequest,
    OutputCursor,
    StopRequestResult,
)

from .command import CommandAdapter, CommandRegistration

#: 登记命令允许的最大参数个数：与冻结参数数组一道阻止"把正文拼成命令"。
MAX_REGISTERED_ARGUMENTS = 64


class RegisteredEntryExecutionAdapter:
    """`ExecutionPort` 实现：登记项从已验证请求派生，命令始终是 argv 数组、`shell=False`。"""

    adapter_version = "registered-entry/1.0"

    def __init__(self, commands: CommandAdapter | None = None) -> None:
        self._commands = commands or CommandAdapter()
        self._bound: dict[str, tuple[str, str]] = {}

    def _bind(self, request: ExecutionRequest) -> None:
        """把已验证请求的登记入口绑成命令；同一 entry_id 的**不同**绑定一律拒绝。

        一个已登记入口只对应一个绑定（入口身份、可执行文件、固定 workdir）。若同一
        `entry_id` 带着不同的可执行文件或不同 workdir 再次到来，说明冻结事实互相矛盾——
        此时**报错**，既不静默复用旧绑定（会把命令跑到别处），也不覆盖（会改变已核对的事实）。
        """

        entry = request.registered_entry
        binding = (entry.entrypoint, request.materialized_snapshot_ref)
        existing = self._bound.get(entry.entry_id)
        if existing is not None:
            if existing != binding:
                raise ValueError(
                    "registered entry identity conflicts with its earlier binding: "
                    f"{entry.entry_id}"
                )
            return
        self._commands.register(
            CommandRegistration(
                entry_id=entry.entry_id,
                executable=entry.entrypoint,
                cwd=Path(request.materialized_snapshot_ref),
                env_allowlist=(),
                max_arguments=MAX_REGISTERED_ARGUMENTS,
            )
        )
        self._bound[entry.entry_id] = binding

    def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self._bind(request)
        return self._commands.start(request)

    def inspect(self, handle: ExecutionHandle) -> ExecutionInspectionResult:
        return self._commands.inspect(handle)

    def collect(
        self,
        handle: ExecutionHandle,
        cursors: tuple[OutputCursor, ...] | None = None,
    ) -> ExecutionCollectionResult:
        return self._commands.collect(handle, cursors)

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult:
        return self._commands.request_stop(handle)


__all__ = ["MAX_REGISTERED_ARGUMENTS", "RegisteredEntryExecutionAdapter"]
