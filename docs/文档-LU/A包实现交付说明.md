# A 包本地核心底座实现交付

协议版本：`aitest.local/2.0`。A 包只保存事实、引用和事务状态，不计算业务通过/失败。

## 目录

```text
src/aitest/
├─ contracts/                 # Command/Event/Response/identity/schema
├─ application/ports.py       # 外部能力端口协议
├─ infrastructure/file_store/
│  ├─ workspace.py            # workspace.json 身份与 writer_epoch
│  ├─ locking.py              # Windows/portalocker 单写锁
│  ├─ atomic.py               # fsync + os.replace 原子发布与重试
│  ├─ records.py              # 不可变记录修订、结构化查询状态与原子提交
│  ├─ index.py                # 有限索引；缺失时返回 maintenance_required
│  ├─ unit_of_work.py         # 短工作单元；commit 原子发布记录/索引/清单/事件
│  ├─ objects.py              # sha256 内容寻址对象（无删除接口）
│  ├─ spool.py                # 证据流 spool 与校验
│  └─ checkpoints.py          # 恢复检查点
├─ interfaces/local/api.py    # 统一路由、请求去重和错误响应
└─ bootstrap.py               # 唯一装配入口
```

## Python API

```python
from pathlib import Path
from aitest.bootstrap import create_api, register_use_cases
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session

register_use_cases("B", {"query": lambda command: {"items": []}})
api = create_api(Path("workspace"))
response = api.dispatch(
    Command(request_id="req-1", action="doctor"),
    Session(session_id="cli", entry_kind=EntryKind.INTERACTIVE_CLI),
)
```

`request_id` 只用于传输重试去重；业务写命令必须同时携带独立的 `intent_id`、`project_id` 和 `expected_revision`。同一请求输入指纹不同会返回 `REQUEST_CONFLICT`。`LocalAPI` 只调用 bootstrap 注册的处理器，B/C/D 通过 `register_use_cases` 注册自己的用例表，不能改动装配点；`create_api(handlers=...)` 为过时兼容接口，B/C/D 禁止使用。

`FileUnitOfWork.open()`、`stage_record()`、`commit()`、`rollback()` 构成短事务。记录按 `(aggregate_kind, record_id, revision)` 追加保存，旧修订永不覆盖。`commit()` 在同一原子发布边界内写入 intent 记录、业务记录、提交清单、查询索引和事件，五者要么全部成功要么全部回滚。`FileObjectStore`、事务日志、清单和 spool 均没有永久业务材料删除路径。

`FileQueryIndex.query()` 只读取已维护的有限索引；索引文件不存在、损坏或版本不兼容时返回结构化 `status=maintenance_required`（不抛出原始异常），不会隐式全表扫描。文件发布先写临时文件、刷新并原子替换，Windows 替换拒绝会按策略重试；失败时保留上一提交边界。

## 验证边界

`tests/acceptance/p1/test_a_package_independent_acceptance.py` 覆盖真实文件单写锁、原子发布故障注入、对象不可变性/摘要校验和分页协议。内存实现仅用于 B/C/D 并行开发；Windows 重启、掉电和权限/空间故障仍需在目标文件系统执行并记录证据。
