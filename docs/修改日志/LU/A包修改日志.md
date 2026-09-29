# A 包修改日志

## 2026-09-29：contracts 全套协议

| 文件路径 | 本次改动 | 实现能力 | 约束要点 |
| --- | --- | --- | --- |
| `src/aitest/contracts/versions.py` | 新增协议、事件、错误、能力和存储版本常量 | 固定 `aitest.local/2.0` 及相关 schema 版本 | 协议版本不允许调用方自行替换 |
| `src/aitest/contracts/errors.py` | 新增统一错误码枚举和 `ErrorDTO` | 归一化锁冲突、事务、记录、对象、索引、备份、恢复和存储错误 | 消息长度受限；不得承载凭据、原始日志或堆栈 |
| `src/aitest/contracts/queries.py` | 新增 `Query`、`QuerySpec`、有限排序和游标 | 支持记录、事件、工作空间和完整性查询 | 页大小有界；不提供隐式全表扫描入口 |
| `src/aitest/contracts/responses.py` | 新增分页信息和统一响应 | 返回事实、引用、游标和结构化错误 | 响应区分 `request_id` 与 `intent_id`；不计算业务结论 |
| `src/aitest/contracts/capabilities.py` | 新增能力项和能力集合 | 声明事务、不可变记录、对象、查询、备份、迁移、恢复等 A 能力 | 能力声明只描述 A 底座，不声明 B/C/D 业务能力 |
| `src/aitest/contracts/events.py` | 扩展不可变事件信封 | 记录事件身份、请求身份、业务意图和提交边界 | 事件追加保存；不提供删除字段或删除操作 |
| `src/aitest/contracts/__init__.py` | 汇总公开导出 | 为后续 application、file_store、local API 提供稳定导入面 | 仅导出 contracts 类型，不引入基础设施 |
| `docs/接口对接/A包修改日志.md` | 新增本日志 | 记录本轮文件级变更 | 后续每个文件完成后继续追加记录 |

## 2026-09-29：application 端口抽象层

| 文件路径 | 本次改动 | 实现能力 | 约束要点 |
| --- | --- | --- | --- |
| `src/aitest/application/ports.py` | 新增 `TransactionPort`、`StoragePort`、`BackupPort`、`IndexPort`、`QueryPort`、`LocalProtocolPort` 协议 | 为事务、不可变记录/对象、备份恢复、有限索引、只读查询和本地协议入口提供稳定抽象 | 仅定义 `Protocol` 方法签名；不包含文件、锁、JSON、数据库或业务判定实现 |
| `src/aitest/application/ports.py` | 端口参数统一使用 contracts 的 `RequestId`、`IntentId`、`Command`、`Query`、`QuerySpec`、`Response`、`Event`、`ErrorDTO`、`CapabilitySet` | 保证 `aitest.local/2.0` 类型边界与 request/intent 身份分离 | `request_id` 仅用于请求传输；`intent_id` 只表示持久业务意图；记录读取必须显式指定 revision |

