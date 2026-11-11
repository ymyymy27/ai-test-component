# AI 主导向导真实 Trae 验证（2026-11-11）

## 1 范围

- 分支：`前端优化`。
- 目标：在真实 Trae CN 中走“继续上次项目 → 选择 AI 目标候选 → 生成 AI 计划 →
  核对覆盖 → 发布”，确认面板内确认、续跑和 AI 计划是否可用。
- 环境：Windows 11 x64；Trae CN；隔离 profile
  `E:\ai-test-component\.runtime\trae-profile`；扩展目录
  `E:\ai-test-component\.runtime\trae-ext`；示例项目
  `C:\Users\y'm'y\AppData\Local\AITest\sample-project`。
- 本记录不修改 `tests/acceptance/p*/status.json`，不把组件测试写成真实 AC 通过。

## 2 实际走通

1. 面板内确认：保存目录绑定与模型材料范围时只出现面板对话框
   （“确认并继续”），未弹出 Tk 原生窗口；确认后核心写入成功。
2. 续跑：选择“继续上次测试”后，项目/绑定/环境不再重复写入；查询当前源码
   指针修订后复用快照并进入目标选择。
3. 目标候选：真实 `analyze_project` 返回的 `add_pair`、`main` 两个候选卡片
   正常显示，可选择其中一个。
4. AI 计划：使用真实 DeepSeek 调用生成 `aitest.plan-proposal/1.1`，保存为
   6 个用例、5 条覆盖声明；核对页显示计划用例、功能 × 维度覆盖、未知项和
   能力缺口。
5. 长调用：模型调用超过 10 秒仍能返回；不再出现 CLI 提前
   `CORE_RESULT_UNVERIFIED`。
6. 错误提示：结构校验失败时显示“模型返回的计划结构不符合约定；可以重试，
   或改用手工检查”，并可重新生成。

## 3 本轮定位并修复的问题

| 问题 | 根因 | 修复 |
| --- | --- | --- |
| AI 计划必返回 `model_basis_unverified` | 向导生成 `generate_draft` 时只传 `source_revision`，未传 frozen `source_ref` | `wizard_commands` 从 `snapshot` 回执构造精确 `source_ref` |
| 目标候选页永不出现 | 面板只读 `understanding.goal_candidates`，真实结果在顶层 `goal_candidates` 与 `understanding.body` | 面板按顶层 → body → 旧路径读取 |
| 多个 AI 用例被判 “duplicate template item IDs” | 校验器把多个 `template_item_id=None` 当成重复 | 只对非空模板项做唯一性校验 |
| 模型调用 10 秒即 `CORE_RESULT_UNVERIFIED` | `CoreClient` 未把 `generate_draft` 列为长动作 | 加入 `generate_draft`、`analyze_project` 长超时 |
| 失败后重试只重放旧 `blocked` 结果 | 同一意图重放；向导重试没有新意图 | 模型阶段每次生成带新 `attempt` 盐，只改变生成意图 |
| 继续上次测试报 `B_REVISION_CONFLICT` | 续跑仍以 `expected_revision=0` 重写项目/绑定/环境 | 续跑只复用已有记录并重新核对源码快照 |
| 第一步与第三步都要选目标 | 配置页必填“你希望验证什么”，下一步又要求选 AI 候选 | 模型路径配置页不再显示目标输入，只保留折叠的“补充说明（可选）”；目标只在候选页选一次 |
| 封面文案仍称“核心原生窗口确认” | 改为面板确认后文案未同步 | 向导/概览/工作台/提测文案改为面板内确认 |

## 4 未完成与当前阻塞

1. 发布计划时核心返回
   `coverage contract differs from the saved understanding`：本次 AI 计划只选了
   `add_pair` 一个目标，覆盖契约不含 `main`；而向导把该计划标成 `full`，
   核心按“完整验证必须覆盖 understanding 全部业务功能”正确拒绝。
2. 已改为：AI 单目标计划使用 `run_tier=on_demand`，核对页显示
   “AI 单目标计划按需自测：只验证选中的目标，不宣称完整覆盖全部业务功能”。
   该改动已通过面板测试与类型检查，但**尚未在真实 Trae 中完成一次发布回执**。
3. 因此本次仍未进入“发布 → 准备/登记运行 → 执行 → 核验 → 报告”的真实链路。
   不能据此判定全链路可用或“好用”。

## 5 结论

| 检查 | 结果 |
| --- | --- |
| 面板内确认替代原生弹窗 | 真实 Trae 通过 |
| 继续上次项目不重复写记录 | 真实 Trae 通过 |
| AI 目标候选显示与选择 | 真实 Trae 通过 |
| 真实模型生成 1.1 计划并进入核对页 | 真实 Trae 通过 |
| 模型长调用与失败重试 | 组件/CLI 与真实 Trae 部分通过 |
| 发布计划 | **未通过**（full/on_demand 语义修正后待复测） |
| 运行、核验、报告 | **未开始** |
| “好用”评价 | **不足以判定** |

## 6 后续

1. 用 `on_demand` 重新生成/发布一次 AI 单目标计划，确认发布回执与覆盖声明。
2. 继续走准备/登记运行、执行、核验、报告，记录真实证据。
3. 对“继续上次测试”补一条真实重启后的恢复用例，避免再次依赖内存回执。
4. 评估是否在目标候选页提供“覆盖全部业务功能”选项，让需要 `full` 的用户
   一次选中全部目标，而不是把单目标计划误标为完整验证。

## 7 2026-11-11 后续复测

- 续跑查询当前源码指针时改为读取足够多的修订并取最大值，避免只拿到历史
  revision 1；续跑的 `analyze_project` 使用独立意图盐，避免
  `INTENT_CONFLICT`。
- 发布确认卡移到核对页顶部，目录确认改为可点击按钮（“确认业务功能目录
  完整”），配置页本地入口确认也改为按钮，减少小复选框的误点。
- 上述改动通过面板向导 2 项、扩展 39 项与相关 Python 定向用例；真实 Trae
  中再次走到“选择目标 → 生成 AI 计划”，模型返回结构校验失败后重试时
  Trae 窗口在长调用期间关闭，未能取得发布回执。
- 因此第 4 节“发布计划未通过/未复测”的结论保持不变；本轮仍未完成运行、
  核验、报告。

## 8 根因定位与核心链路补测

### 8.1 发布失败的根因

重试提交时用例和验收范围已经存在，而向导仍以 `revision=1`、
`expected_revision=0` 生成保存命令。`save_acceptance` 返回
`B_REVISION_CONFLICT`，发布输入引用的范围修订与已保存修订不一致，核心
正确拒绝。修复为：扩展在 commit 阶段查询用例/验收范围的当前仓储修订，
向导据此生成 `current+1` 修订和准确的 `expected_revision`。

### 8.2 `prepare_run` 的根因

环境登记把版本写成 `Python 3.11+`，核心的解释器要求需要显式版本，返回
`SOURCE_BINDING_UNVERIFIED: interpreter requirement needs a supported explicit
Python version`。修复为：环境准备返回实际 `python_version`（本机 3.11.9），
向导用它登记环境。

### 8.3 宿主编译器/窗口退出的根因

Windows 事件日志记录到 Trae CN 2.3.36499.0 的
`toolhost.dll_unloaded` BEX64 崩溃（异常 `0xc0000005`，2026-11-11 17:14、
17:17、17:18）。关闭 Trae 自己的 AI 侧栏后，长时间模型调用不再触发该
崩溃；但提交阶段主窗口仍会消失，扩展宿主进程和 CLI 子进程继续运行。
这是 Trae 宿主侧问题，不是 Python 核心崩溃；本次改用同一核心的
CoreClient 完成后续提交，避免依赖已消失的 webview。

### 8.4 核心链路补测结果

使用已保存的 1.1 计划、10 个用例和真实核心完成：

- 发布计划：`published=true`，plan revision 1，scope revision 2，10 个
  用例修订 1，生成 plan grant 与 10 条 basis confirmation。
- 准备运行：`prepared-a8bf94a9...`，环境 revision 2（3.11.9）。
- 登记运行：`run-1ec6c8bb...`，tier `on_demand`、driver `planned`，
  33 个自动步骤。
- 首个步骤：同一 `step-flow` 意图完成 prepare/authorize/execute，
  `attempt.state=completed`，stdout 22 字节已保存。

仍未完成：独立核验（不能用被测实现本次输出倒填预期）、报告、其余步骤。
不能把“已执行一个步骤”写成整条业务链通过。

## 9 报告与核验根因

- `create_report` 实际在 10 秒客户端超时后仍完成；报告
  `report-run-1ec6c8bb...` 已保存，`business_outcome=incomplete`、
  `confidence=low`、`run_revision=4`。原因是 CLI/CoreClient 未把报告生成
  视为长动作，已加入 `create_report`、`export_report` 的长响应预算。
- `verify_pending` 被核心正确阻塞：
  `BUSINESS_VERIFICATION_BLOCKED: exact case basis or trusted independent query
  registration is invalid: immutable material is not readable UTF-8 JSON`。
  AI 计划把 `assertion_basis_text` 写成自然语言，而纯函数核验要求
  `aitest.pure-function-basis/1` 的 `expected_facts` 结构化 JSON（手工计划
  正是这样生成的）。这不是可以绕过校验的 bug，而是 AI 计划合同缺口：
  模型没有产出可机器核对的独立预期。
- 结论：本次报告如实为 `incomplete`；不能通过放松核验或把运行输出倒填为
  预期来制造 `passed`。
- 后续修复：核对页新增“验证预期（可选，JSON）”，按用例填写
  `{case_id: {字段: 预期值}}`；向导在 `assertion_basis_text` 中写入
  `aitest.pure-function-basis/1` 的 `expected_facts`，纯函数核验即可读取。
  未填写时发布页明确警告“只能保持未验证”。面板测试已验证该转换。
- 已发布的本次运行仍保持 `incomplete`；要验证新能力，需要以填写了预期
  的新计划重新发布并登记新运行。该复测尚未执行。





