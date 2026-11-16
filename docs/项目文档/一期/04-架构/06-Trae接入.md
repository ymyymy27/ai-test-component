# Trae 接入

本文是开发阶段的宿主接入技术合同，覆盖扩展与核心进程模型、本地协议、动作目录、错误与权限、确认通道、兼容清单、核心生命周期和计划确认证明模型。字段名、动作名、命令名与超时值以 `src/aitest/contracts/`、`src/aitest/interfaces/local/`、`src/aitest/interfaces/tools/`、`integrations/trae/src/` 的实际代码为准；与旧架构分册不一致处集中列在第 8 节。

本文不登记验收结论。当前验收边界与未验证项见 `docs/现状.md` 与 `tests/acceptance/p1/status.json`；文档一致、单元测试通过、组件级真实进程通过，都不等于真实 Trae 验收通过。

## 1 扩展与进程模型

### 1.1 扩展构成

| 位置 | 作用 | 关键事实 |
| --- | --- | --- |
| `integrations/trae/package.json` | 宿主清单 | `main` 指向 `dist/extension.js`；激活事件为 `onCommand:aitest.openPanel`、`onCommand:aitest.configureEntry`、`onView:aitest.sidebar`；`capabilities.untrustedWorkspaces.supported=false` |
| `integrations/trae/src/extension.ts` | 命令注册、Webview 宿主、消息往返、确认提示回传 | 注册 `aitest.openPanel`、`aitest.openWorkbench`、`aitest.openReport`、`aitest.configureEntry` 四个命令；未信任工作区直接返回 |
| `integrations/trae/src/core-bridge.ts` | 唯一核心桥 | 不实现管道协议，改为 `spawn` 既有 CLI 子命令；核对回执 `request_id`／`protocol_version`／`instance_id`／`workspace_id` |
| `integrations/trae/src/onboarding.ts` | 向导与模型配置的宿主侧动作 | 只做输入收集与受控派发，不计算业务结论 |
| `integrations/trae/dist/` | 构建产物 | `extension.js`、`panel.js`、`aitest-trae.vsix`；面板产物由 `src/aitest/resources/panel` 唯一源码构建 |
| `integrations/trae/tests/` | node 测试 | `core-bridge.test.mjs`、`extension-context.test.mjs`、`onboarding.test.mjs` |

扩展不写业务记录、不复制协议、不代签确认。`deactivate()` 是空实现，注释含义是“处置编辑器入口从不等于取消业务运行”。

### 1.2 进程链

```text
面板 Webview
   │ postMessage（kind: dispatch / writeDispatch / controlledDispatch / ensureCore / restartCore / wizard*）
Trae 扩展
   │ spawn python -m aitest.interfaces.tools.cli <子命令> --workspace <用户数据工作空间>
CLI（discover → 命名管道 → 回执核对）
   │ 4 字节大端长度前缀 + JSON 帧，上限 8 MiB
唯一核心进程（LocalAPI）
   │
同一文件工作空间（记录 / 对象 / 索引 / 事件）
```

- 一个用户数据工作空间只有一个核心进程；工作空间由 `aitest.workspaceRoot` 显式配置，留空时入口返回 `WORKSPACE_NOT_CONFIGURED`，不从源码目录猜测。
- **本地业务不监听 TCP 端口**：面板、扩展、CLI、stdio relay 全部经由命名管道与 stdio 汇入同一核心。
- 管道名形如 `\\.\pipe\aitest/<workspace_id>/session-<登录会话>/<instance_id>`；`PIPE_MAX_ONE_INSTANCE=1`；服务端 `validate_peer()` 限定当前登录会话与同一用户，拒绝远程客户端。
- 核心发现记录是 `<工作空间>/core/launch.json`，schema `aitest.core-launch/1`，字段为 `schema`、`endpoint_workspace_id`、`instance_id`、`state`、`process_id`、`process_identity`、`exit_code`、`reason`；`state` 取值 `creating`／`created`／`stopping`／`create_failed`／`publication_uncertain`／`exited`。实例号格式为 `core-` + 12 位十六进制。
- 启动竞争由 `<工作空间>/core/launch.lock` 串行化；写者世代来自 `writer.lock` 的 `admitted_writer_epoch`。**发现文件陈旧不能作为杀进程的依据**，只有真实业务写锁与进程身份能证明活性。
- 第二个窗口先核实既有核心再连接；无法核实一律 `WORKSPACE_IN_USE`，不另建写者。

### 1.3 宿主上下文

- 扩展从配置读 `projectId`、`runId`、`pythonPath`、`workspaceRoot`、`pythonCarriersFile`、`businessQueriesFile`、`modelConfigurationFile`，每次变更生成新的 `context_id` 并推给面板。
- 写入消息必须回传当前 `contextId`，不一致直接拒绝（`PROJECT_CONTEXT_MISMATCH`），迟到回执只提示“核对原操作、不要重复执行”。
- 依赖方向保持入口 → 适配 → 应用 → 领域：扩展与面板都不 import 业务规则，`interfaces/local` 不 import `infrastructure`（由架构边界测试锁定）。

## 2 协议与动作目录

### 2.1 四类消息字段

命令（`contracts/commands.py::Command`，`extra="forbid"`、`strict=True`）：

| 字段 | 约束 |
| --- | --- |
| `protocol_version` | 固定字面量 `aitest.local/2.0` |
| `request_id` | 必填，1—128 字符；仅用于传输去重 |
| `action` | 必填，非空字符串 |
| `project_id` | 写动作必填 |
| `binding_revision` | 可选，≥1 |
| `target` | 可选；受控确认动作必填 |
| `expected_revision` | 写动作必填，≥0 |
| `intent_id` | 写动作必填；不得与 `request_id` 相同 |
| `parameters` | JSON 对象，默认空 |

非只读动作若缺 `project_id`／`intent_id`／`expected_revision`，或 `request_id == intent_id`，在模型层即被拒绝。

响应（`contracts/responses.py::Response`）：`protocol_version`、`request_id`、`instance_id`、`workspace_id`、`project_id`、`intent_id`、`binding_revision`、`result`、`page`（`limit`／`next_cursor`／`index_state`）、`error`。**对象修订、`run_id`、提交序号都在 `result` 内**，不在响应顶层。

事件（`contracts/events.py::Event`，`aitest.event/2.0`）：`event_id`、`request_id`、`intent_id`、`instance_id`、`workspace_id`、`writer_epoch`、`commit_sequence`、`event_sequence`、`project_id`、`record_id`、`revision`、`event_type`。只有已提交事件才会发出。

能力（`contracts/capabilities.py::CapabilitySet`，`aitest.capability/2.0`）：`protocol_version`、`workspace_id`、`capabilities`（`schema_version`、`name`、`version`、`enabled`、`reason`）；能力名为 `transactions`、`immutable_records`、`objects`、`events`、`bounded_queries`、`backup`、`migration`、`integrity`、`recovery`、`redaction`、`connectivity_policy`。

### 2.2 动作目录

`PHASE_ONE_ACTIONS` 共 67 项，其中 `READ_ACTIONS` 13 项、可写 54 项。按用途归并（名称逐字取自代码）：

| 动作组 | 动作 |
| --- | --- |
| 读取与维护 | `query`、`read_view`、`events`、`doctor`、`storage_usage`、`copy_repair_brief`、`discover_tests`、`suggest_regression`、`aggregate_project_runs`、`check_source`、`inspect_case_reuse`、`plan_confirmation_state`、`export_rules_markdown` |
| 项目与内容 | `bind_project`、`save_binding`（表外兼容名）、`save_context`、`save_environment`、`save_dependency_graph`、`save_case`、`save_task`、`save_delivery`、`submit_delivery`、`save_acceptance`、`analyze_project` |
| 规则与模型 | `save_rules`、`publish_rules`、`import_rules_markdown`、`generate_draft`、`request_model_draft`、`resolve_model_response`、`save_model_outbound_policy`、`save_project_view_preference` |
| 计划与准备 | `save_plan`、`publish_plan`、`prepare_approval`、`revoke_approval`、`prepare_run`、`register_run`、`prepare_execution` |
| 执行与控制 | `execute_step`、`start_run`、`pause_run`、`resume_run`、`cancel_run`、`retry_step`、`return_stage`、`attach_evidence`、`record_agent_evidence`、`import_external_result`、`verify_pending`、`select_case_reuse` |
| 授权与修订 | `authorize_step`、`create_plan_grant`、`revoke_plan_grant`、`revise_pending_steps`、`narrow_driver`、`confirm_basis` |
| 问题与报告 | `update_issue`、`record_fix`、`request_regression`、`close_issue`、`create_report`、`record_local_review`、`export_report` |
| 存储与维护 | `backup`、`migrate`、`test_connection` |

`test_connection` 只在配置了连接器时被广告与派发；未配置时调用得到 `CAPABILITY_UNAVAILABLE`，不能写成“连通性通过”。

### 2.3 权限集与 MCP 面

- `HUMAN_ACTIONS` 18 项：`authorize_step`、`create_plan_grant`、`revoke_plan_grant`、`revise_pending_steps`、`narrow_driver`、`publish_plan`、`publish_rules`、`submit_delivery`、`record_local_review`、`save_model_outbound_policy`、`close_issue`、`update_issue`、`save_binding`、`bind_project`、`confirm_basis`、`prepare_approval`、`revoke_approval`、`select_case_reuse`。
- `CHALLENGE_ACTIONS` 10 项：`save_binding`、`save_environment`、`publish_rules`、`publish_plan`、`submit_delivery`、`save_model_outbound_policy`、`authorize_step`、`revise_pending_steps`、`narrow_driver`、`confirm_basis`。
- MCP relay（`interfaces/tools/agent_relay.py`）支持 MCP 协议版本 `2024-11-05`、`2025-03-26`、`2025-06-18`、`2025-11-25`，仅 `initialize`、`ping`、`tools/list`、`tools/call`。工具为 `aitest_doctor`、`aitest_query`，以及在核心广告对应动作时才出现的 `aitest_read_view`、`aitest_inspect_case_reuse`、`aitest_dispatch`。
- `aitest_dispatch` 的 `action` 枚举只包含同时满足三条的动作：属于 `PHASE_ONE_ACTIONS`、不是 `HUMAN_ACTIONS`、且 `doctor.intent_contracts[action] == "aitest.record-write-intent/1.0"`（即 `save_context`、`save_environment`、`save_dependency_graph`、`save_case`、`save_acceptance`、`save_task`、`save_delivery`）。
- relay 启动必须同时给出 `--binding`、`--workspace`、`--project`；缺任一即 `CapabilityUnavailable`。MCP 宿主配置里只写 `--binding` 会在启动阶段失败。

### 2.4 幂等边界

- 传输层：`LocalAPI` 以 `(*session.origin, request_id)` 缓存响应（上限 256 条），同号异输入返回 `REQUEST_CONFLICT`；`request_id` 不可当业务意图。
- 业务层：业务键为 `(project_id, action, intent_id)`，业务结果与幂等记录在同一工作单元提交；同键异输入 `INTENT_CONFLICT`，同键同输入返回原记录。
- `prepare_run` 用持久 `client_id`／`prepare_request_id` 取得同一意图；重传同一意图跨入口仍是同一 Run，不新建 Attempt。
- 回执未知（超时、输出超预算、进程失败）时入口只报告“结果未核实”，要求先核实原意图，不自动新建意图重试。

## 3 错误与权限

`ErrorDTO`（`aitest.error/2.0`）字段：`schema_version`、`code`、`message`（≤1000，已脱敏）、`retryable`、`next_step`（≤500）、`request_id`、`intent_id`。

| 分组 | 代码 |
| --- | --- |
| 协议与请求 | `INVALID_PROTOCOL`、`INVALID_REQUEST`、`REQUEST_CONFLICT`、`AWAITING_USER_CONFIRMATION` |
| 工作空间与身份 | `WORKSPACE_NOT_FOUND`、`WORKSPACE_IDENTITY_MISMATCH`、`WORKSPACE_IN_USE` |
| 事务与记录 | `TRANSACTION_NOT_FOUND`、`TRANSACTION_CONFLICT`、`INVALID_TRANSACTION_STATE`、`INTENT_CONFLICT`、`RECORD_NOT_FOUND`、`RECORD_REVISION_CONFLICT`、`IMMUTABLE_RECORD_CONFLICT` |
| 对象与索引 | `OBJECT_NOT_FOUND`、`OBJECT_DIGEST_MISMATCH`、`INDEX_REBUILD_REQUIRED`、`INTEGRITY_CHECK_FAILED` |
| 存储与恢复 | `STORAGE_NOT_WRITABLE`、`ATOMIC_PUBLISH_FAILED`、`BACKUP_FAILED`、`MIGRATION_FAILED`、`RECOVERY_REQUIRED`、`maintenance_required` |
| 能力与连接 | `CAPABILITY_UNAVAILABLE`、`CONNECTIVITY_FAILED`、`SNAPSHOT_REQUIRED`、`invalid_cursor`、`INTERNAL_ERROR` |

- `retryable` 默认 `false`；`LocalAPI._error()` 只把 `WORKSPACE_IN_USE` 与 `SNAPSHOT_REQUIRED` 置为可重试，并给出对应 `next_step`。
- 读取期间提交边界变化返回 `SNAPSHOT_REQUIRED`，要求重新取得同边界快照与游标，不能静默从最新位置续接。
- 面板确认相关的专用代码在实现内另有 `ENTRY_ACTION_NOT_ALLOWED`、`USER_DECLINED`、`INVALID_COMMAND`、`PANEL_CONFIRMATION_EXPIRED`、`PANEL_CONFIRMATION_UNAVAILABLE`、`CONFIRMATION_PREVIEW_LIMIT`，以及扩展侧的 `CORE_RESULT_UNVERIFIED`、`CORE_UNAVAILABLE`、`ENTRY_TIMEOUT`、`ENTRY_SPAWN_FAILED`、`ENTRY_OUTPUT_LIMIT`、`ENTRY_INVALID_CONFIG`、`PROJECT_CONTEXT_MISMATCH`、`PROJECT_CONTEXT_CHANGED`。
- 通道层还有 `MALFORMED_MESSAGE`、`UNKNOWN_CONTROL`、`CAPABILITY_DEGRADED`；它们不在 `ErrorCode` 枚举内，由核心或适配器按具体失败路径返回。

### 3.1 入口权限

核心按**对端进程映像事实**归类入口：映像名为 `trae.exe`（可用 `--human-host-images` 扩展）才归 `human_ui`；其他或取证失败一律 `agent_relay`。消息自报角色在协议层被拒（`Command` 为 `extra="forbid"`）。

| 入口 | 允许行为 | 人工确认边界 |
| --- | --- | --- |
| 面板／受控扩展 | 只读查询、普通写入、受控动作、展示核心冻结的确认提示 | 只有真实用户点击可回传确认；面板不提交挑战、摘要或角色 |
| 交互 CLI | `dispatch`、`ensure-core`、`restart-core`、`doctor` | 不提供人工确认；命中 `HUMAN_ACTIONS` 直接拒绝，须从面板完成 |
| MCP／agent_relay | 只读工具与 7 个普通写入动作 | 命中 `HUMAN_ACTIONS` 直接 `AWAITING_USER_CONFIRMATION`，不暴露确认动作 |

`doctor` 只返回 `READY` 或 `NOT_READY`（工作空间未就绪时），**没有 `DEGRADED`**；结果还带 `protocol`、`supported_actions`、`phase`、`intent_contracts`、`dependencies`（配置了能力门时），核心经 `dispatch_frame` 追加 `transport_capabilities`。

## 4 入口与确认通道

### 4.1 传输能力

| 能力 | 版本串 | 启用条件 |
| --- | --- | --- |
| 面板内确认 | `panel-confirmation/2` | 同时带 `panel_prepare_dispatch` 与 `panel_confirm_dispatch`；扩展直接连接核心命名管道发送面板帧 |

面板确认不再经 CLI。扩展先读取 `ensure-core`/`restart-core` 回执中的 `pipe_name`，再直接连接该核心命名管道发送面板帧；不支持 `panel-confirmation/2` 或连接身份不符时拒绝并要求受控重启，不降级为无确认写入。

### 4.2 面板确认路径

`ApprovalChallenge` 的领域字段：`challenge_id`、`basis`（`workspace_id`／`project_id`／`intent_id`／`action`／`target`／`input_digest`／`credential_scope_ref`／`materials`）、`origin_session_id`、`origin_entry_kind`、`state`、`created_at`。`ActionConfirmation` 在此基础上增加 `confirmation_id`、`confirmation_intent_id`、`interaction_id`、`confirmed_at_commit`、`confirmed_at`；`ConfirmationRecord` 绑定 `case_id`、`basis_revision`、`basis_text_digest`、`confirmed_at_commit`。

面板路径：扩展先发 `{__aitest_control__: "panel-confirmation/2", phase: "prepare", command}`，核心只回脱敏投影 `challenge_id`、`input_digest`、`panel_session_id`、`expires_at`、`prompt{title, note, facts, details}`；用户点击后扩展在同一连接上发 `phase: "confirm"` 并附同一 `request_id` 的 `challenge_id`、`input_digest`。核心要求 prepare/confirm 同连接、同 peer、入口为 `HUMAN_UI`，校验会话、挑战、摘要与修订后写入 `ConfirmationRecord`／`ActionAuthorization`。

无挑战人工路径：对 `HUMAN_ACTIONS` 中不在 `CHALLENGE_ACTIONS` 的 9 个动作（`update_issue`、`close_issue`、`record_local_review`、`select_case_reuse`、`bind_project`、`create_plan_grant`、`revoke_plan_grant`、`prepare_approval`、`revoke_approval`），扩展在同一面板连接上发 `{__aitest_control__: "panel-confirmation/2", phase: "human", command}`。核心要求 `HUMAN_UI` 入口、动作属于无挑战人工动作、项目/意图/预期修订完整，且不接受调用方确认字段；通过后直接 `dispatch`。面板点击就是手势，不冻结挑战。

| 约束 | 实现事实 |
| --- | --- |
| 挑战归属 | 挑战只存在于核心；`prepare` 拒绝调用方携带 `approval_challenge_id`／`user_confirmed`／`interaction`／`input_digest` |
| 超时 | 核心 `PANEL_CONFIRMATION_TIMEOUT_MS = 120_000`；扩展侧确认等待同为 120 秒，超时按“未确认”处理 |
| 过期 | 会话被摘除后 `confirm` 返回 `PANEL_CONFIRMATION_EXPIRED`，须重新准备 |
| 幂等 | 同一意图已提交时 `prepare` 直接返回原回执且不带 `prompt`，不再要求第二次手势 |
| 预览上限 | `details` 超过 `MAX_PROMPT_CHARS = 128_000` 返回 `CONFIRMATION_PREVIEW_LIMIT` |

### 4.3 人工动作的通道覆盖

`HUMAN_ACTIONS` 的 18 个动作都有面板入口：9 个与 `CHALLENGE_ACTIONS` 重叠的动作走 `prepare`/`confirm`（另 1 个 `save_environment` 可挑战但不属于人工动作集）；其余 9 个走 `phase: "human"` 直接派发。两条路径都要求已核实的 `HUMAN_UI` 连接；CLI 与 MCP 仍不能执行人工动作。

## 5 兼容性字段

兼容清单是**实测制品**，不是文档推测。代码中没有生成器、没有校验器、没有默认文件；`adapter_versions` 在代码里指执行适配器版本（`execution_source.adapter_versions`），与编辑器适配器版本不是同一概念，不能互相代填。

唯一字段集（合并旧分册两处不一致的写法，只保留一套）：

| 字段 | 含义 |
| --- | --- |
| `os` | 操作系统族 |
| `os_version` | 系统版本与补丁级别 |
| `runtime` | Python 版本 |
| `editor` | 编辑器产品名 |
| `editor_distribution` | 发行版（国际版／中国版必须分开，同名产品不保证同一接口） |
| `editor_version` | 实测版本 |
| `core_version` | 核心版本 |
| `adapter_versions` | 执行适配器版本映射 |
| `artifact_digest` | 核心 wheel 与 VSIX 摘要 |
| `evidence_path` | 证据文件路径 |
| `verified_at` | 实测时间 |
| `result` | `pass`／`fail`／`untested` |

规则：未实测组合一律 `untested`；`evidence_path` 为空不得写 `pass`；扩展版本、核心版本与面板资源摘要必须同时进入记录；不得手工编辑生成物。

## 6 核心保活与退出

### 6.1 启动协调

- 启动顺序：取 `core/launch.lock` → 写 `creating` → 启动子进程 → 子进程取真实业务写锁并公布实例身份 → 发布 `created`；失败候选退出（同名管道被占用时退出码 3）。
- 客户端只信“发现记录 + 真实锁 + 进程身份”三者一致；发现文件陈旧不足以判定核心已死。
- `ensure-core` 先连既有核心，连不上再启动一个；路径不是目录时返回 `WORKSPACE_NOT_FOUND` 并 `exit 2`，**不会**因为配置错误造出数据工作空间。
- `restart-core` 先优雅停机；活动执行未排空时返回 `CORE_RESTART_BLOCKED`，不强制杀进程。

### 6.2 驻留与退出判据

核心默认 `--max-clients 0`，即长期唯一核心；**空闲不自动退出**。客户端断开只关闭本连接，旧管道关闭后用同一实例名重建，等待下一次连接。

终止条件只有三种：达到有限 `--max-clients`（测试用）；收到停机控制帧；父进程消亡。父进程消亡时由 `shutdown_blocker()` 裁决，不是无条件退出：

| 状态 | 裁决 | 结果 |
| --- | --- | --- |
| 有活动执行、包装器或待收尾输出 | `shutdown_blocker()` 非空，`retain_activity()` | 保留服务继续收尾，不中断业务 |
| 执行器状态不明、可能仍占用工作目录 | 同上 | 保留目录与未知记录，重启后再核实 |
| 仅暂停、人工待办、未知，无待收尾输出 | blocker 为空 | 发布检查点后退出（退出码 6） |
| 无活动工作 | blocker 为空 | 退出（退出码 6） |
| 达到 `--max-clients` 且 blocker 为空 | — | 正常退出（退出码 0） |
| 同名管道已被另一核心持有 | — | 退出（退出码 3） |
| 管道服务端故障 | — | 退出（退出码 4），交宿主重新拉起 |
| 启动恢复 blocked | — | 拒绝服务（退出码 5） |

退出码：`0` 正常，`3` 同名管道被另一核心持有，`4` 管道服务端故障，`5` 启动恢复 blocked 拒绝服务，`6` 父进程消亡且已核实可以退出。停机控制帧是 `{__aitest_control__: "shutdown", request_id: "core-shutdown"}`，只能由工作空间关闭端发出；业务 `Command` 不含该键，带该键的帧一律按控制帧解析，未知控制动作返回 `UNKNOWN_CONTROL`。

父进程看门狗只置退出请求并取消阻塞中的连接等待，不直接 `os._exit`；是否退出仍由 `shutdown_blocker` 决定。编辑器退出、崩溃、CLI 先退出都不构成业务取消。

## 7 计划确认证明模型

目标：**一次用户确认**同时证明计划、执行许可与逐条断言依据。实现不新增 `confirm_plan` 动作，动作仍是 `publish_plan`。

### 7.1 PlanConfirmation

`PlanConfirmation` 是 `publish_plan` 的 `ActionConfirmation` 受控视图，不是新聚合；`confirmation_id` 仍由核心生成。

| 字段 | 来源 |
| --- | --- |
| `confirmation_id` | `ActionConfirmation.confirmation_id` |
| `plan_id`／`plan_record_revision` | 受控发布写入的计划记录身份 |
| `plan_digest`／`scope_digest` | 核心对冻结计划与范围计算的规范摘要 |
| `grant_scope` | 可选，冻结在 `approval_parameters.plan_grant` |
| `basis_digests[]` | 每条用例的 `case_id`／`case_revision`／`basis_revision`／`basis_text_digest` |
| `origin_session_id`／`origin_entry_kind`／`interaction_id` | 真实受控用户入口 |
| `confirmed_at_commit` | 提交序号 |

计划记录写入 `approval_action="publish_plan"`、`approval_confirmation_id`、`approval_commit_seq`、`approval_parameters`（冻结 plan/cases/scope + 可选 `plan_grant` 摘要）。`FrozenRunPlanReader` 与 `SavedRuntimeRevisionReader` 读取语义不变。

### 7.2 逐条依据与 plan proof

`CaseLink` 增加计划证明形状，替代逐用例 `approval_confirmation_id`：`plan_confirmation_id`、`plan_id`、`plan_revision`。`SavedCaseBasisReader` 接受两种互斥 proof：旧的 `approval_confirmation_id`（逐用例 `confirm_basis`）；新的 `plan_confirmation_id` + 计划记录验收。plan proof 不再携带 `approval_confirmation_id`，避免同一记录出现两个确认来源。

### 7.3 PlanExecutionGrant

真实字段（`domain/planning/grants.py`）：`grant_id`、`plan_revision`、`understanding_revision`、`scope_digest`、`allowed_action_classes`、`credential_scope_ref`、`environment_id`、`risk_class`、`max_side_effect_actions`、`max_data_mutation`、`allowed_hosts`、`time_window_start`、`time_window_end`、`budget`、`status`、`created_at`、`revoked_at`。许可记录另存 `plan_id` 并与计划绑定记录配对。`allowed_action_classes` 不能为空；时间窗两端必须成对且起点早于终点。

`create_plan_grant` 的参数集是：`plan_id`、`plan_revision`、`understanding_revision`、`scope_digest`、`allowed_action_classes`、`credential_scope_ref`、`environment_id`、`risk_class`，可选 `max_side_effect_actions`、`max_data_mutation`、`allowed_hosts`、`time_window_start`、`time_window_end`、`budget`；未知字段直接拒绝。

### 7.4 时序与幂等

```text
1 客户端提交 plan/cases/scope + 可选 plan_grant
2 核心计算 plan_digest、scope_digest、grant_scope
3 核心生成 ApprovalChallenge(action=publish_plan, target=plan_id, input_digest=上述摘要)
4 用户确认一次
5 核心在同一工作单元保存受控 Plan 记录（含 approval_confirmation_id/commit_seq/plan_grant）
6 核心派生（可重放，不要第二次手势）：
  a 逐条追加 CaseLink（plan_confirmation_id/plan_id/plan_revision）
  b 最后创建 PlanExecutionGrant
7 返回 plan_revision、plan_confirmation_id、grant_id、basis_confirmation_ids
```

第 5 步是单次原子提交；第 6 步是**可重放的派生批次**，不是同一批次。依据先写、许可最后写，因此不会出现“有许可、无依据”的可用状态。重传同一意图读回原记录，不重复写入；派生失败时计划仍是已确认的不可变事实，重试同一意图继续补齐。

### 7.5 校验与失败语义

| 对象 | 校验 |
| --- | --- |
| 挑战 | `action == "publish_plan"`；`target == plan_id`；`input_digest` 覆盖 plan + scope + cases + `plan_grant`；来源必须是受控用户入口；`agent_relay` 不能确认 |
| 计划 | `plan_id`／修订／`plan_digest` 与确认一致；scope/cases 与冻结摘要一致；发布门禁全部通过；`approval_confirmation_id` 等于确认编号 |
| 依据 | 每条 `case_revision`／`basis_revision`／`basis_text_digest` 出现在 `approval_parameters.cases`；用例仍在当前修订；不能少一条也不能多一条 |
| 许可 | `plan_id`／`plan_revision`／`scope_digest`／`understanding_revision` 由服务端从刚发布的计划计算，客户端不能自报 |

失败语义：挑战过期或输入变化 → 不写任何记录；计划门禁失败 → 不写计划、不产生确认/许可/依据；许可参数非法 → 计划确认可能已保存，派生拒绝且需修正输入后生成新的计划修订/意图；依据缺失或多余 → 拒绝该次派生且不创建许可；核心崩溃 → 按工作单元恢复，未提交批次不产生部分记录，已提交计划可按幂等意图重试派生。

### 7.6 只读状态动作

`plan_confirmation_state` 返回：`plan_id`、`plan_record_revision`、`plan_confirmation_id`、`state`、`cases[]`（`case_id`、`case_revision`、`basis_confirmation_id`）、`missing_basis_case_ids`、`grant_id`、`reason`。`state` 为 `confirmed`（计划已确认、派生未完成）、`ready`（依据与许可均已落地）或 `derivation_failed`（带失败原因）。重试继续使用原计划确认意图。

## 8 偏差记录

| 旧分册表述 | 代码事实 | 处理 |
| --- | --- | --- |
| 扩展直接经命名管道派发 | 扩展 `spawn` 既有 CLI，由 CLI 做发现、管道与回执核对 | 按代码写第 1 节；扩展不复制协议 |
| MCP 配置只需 `--binding` | relay 要求 `--binding`、`--workspace`、`--project` 三者齐全 | 按代码写第 2.3 节；旧示例会在启动阶段失败 |
| `doctor` 返回 `READY`／`DEGRADED`／`NOT_READY` | 只有 `READY`／`NOT_READY` | 按代码写第 3.1 节 |
| 响应顶层含对象修订与 `run_id` | `Response` 只有 `result`／`page`／`error`，修订与 `run_id` 在 `result` 内 | 按代码写第 2.1 节 |
| 计划级许可含 `plan_id` 与 `credential` | `PlanExecutionGrant` 无 `plan_id` 字段（在记录与绑定中），凭据字段名为 `credential_scope_ref` | 按代码写第 7.3 节 |
| 旧稿写“一期动作表 59 项/66 项” | `PHASE_ONE_ACTIONS` 为 67 项，13 项只读、54 项可写 | 按代码写第 2.2 节；现状页已同步 |
| 所有人工动作都可经面板确认 | 9 个挑战动作走 `prepare`/`confirm`，9 个无挑战动作走 `phase: "human"` 直接派发；两条都只接受 `HUMAN_UI` 连接 | 第 4.2、4.3 节 |
| 兼容清单可选 `editor_distribution` | 两处旧写法不一致，代码无生成器 | 统一为第 5 节 12 字段一套，国际版/中国版必须区分 |
