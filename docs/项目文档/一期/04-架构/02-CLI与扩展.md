# CLI 与扩展（一期）

> 状态：2026-11-16 设计基线（D4），2026-11-27 按重构事实核对。命令字段见[CLI 与数据契约](../03-设计/03-CLI与数据契约.md)。当前 CLI 注册 54 条命令，版本 0.5.0；验收状态只以 `tests/acceptance/p1/status.json` 为准。

## 1 CLI

### 1.1 入口

```bash
python -m aitest.cli <command> --workspace <dir> [--input <json-file>] [--confirm]
```

- 读 `--input` 或 stdin；成功和错误都向 stdout 输出一行 JSON；
- stderr 不是协议通道：只在设置 `AITEST_LOG` 时写诊断日志，未预期异常另会打印堆栈；
- 退出码 0/1/2。

### 1.2 分发

```text
main(argv)
 → 解析 command、workspace、input
 → 读取并校验信封（command、request_id、payload）
 → 路由到 handler
 → 成功：{"ok": true, "request_id": "...", "data": {...}}
 → 业务拒绝：{"ok": false, "request_id": "...", "error": {...}}，退出 1
 → 参数/环境错误：退出 2
```

handler 只做三件事：校验入参、调用 core/storage/runners/adapters、组装响应。不直接做业务判断。

### 1.3 幂等

- `commands.py` 的 `MUTATING` 集合定义有副作用的命令（项目/任务/计划/运行/问题/报告/备份等；`integration_commands` 与 `runtime_commands` 会追加）；
- 只有 `MUTATING` 命令记录 `request_id → 结果`；
- 同一 `request_id` 重放返回原结果；
- 新意图必须使用新 `request_id`。

### 1.4 错误映射

内部异常统一映射到契约中的错误码；未预期异常映射 `INTERNAL`，附 `request_id`，不把堆栈写进 stdout。

## 2 扩展

### 2.1 文件与宿主能力插头

```text
integrations/
  trae/
    package.json
    src/
      extension.ts         # 激活、命令、OutputChannel、StatusBarItem
      bridge.ts            # 调用 CLI、解析 JSON、超时
      host_capabilities.ts # 可选宿主能力插头（Git/LSP/FileWatcher/跳源码/Diagnostics/SecretStorage）
      runtime.ts           # Python 发现、环境准备、版本校验
      process.ts           # 子进程封装
    media/panel/           # 共享面板构建产物
  agent-bridge/
    server.py              # 独立于核心的跨智能体 stdio JSON-RPC 薄适配器
    SKILL.md               # Codex / Google Antigravity / Claude Code 协同技能描述
panel/
  src/                     # Vue 3 共享面板源码
  tests/
```

- 在 Trae 中扩展通过 `host_capabilities.ts` 可选复用 `vscode.git`、文档符号、文件保存监听器（即时刷新提示）、`showTextDocument` 跳源码、`DiagnosticCollection` 缺陷红线以及状态栏与输出通道；脱离 IDE（纯 CLI 或通过 `integrations/agent-bridge/` 接入外部智能体）时自动优雅回退内核本地引擎。
- 外部跨智能体适配层严格位于 `integrations/agent-bridge/`，不放进 `src/aitest/`（内核不做内置 MCP 或常驻服务），并强制校验 `aitest_run_start` 必须带人类显式确认。

### 2.2 命令

| 命令 | 作用 |
| --- | --- |
| `aitest.openPanel` | 打开测试面板 |
| `aitest.checkEnvironment` | 运行 doctor 并展示结果 |
| `aitest.runStatus` | 查看当前项目运行 |

### 2.3 调用

- 所有业务动作经 `bridge.ts` 调 CLI；
- 统一超时、取消、错误展示；
- 不直接读写工作空间文件；
- 不复制业务规则、不重算结论。

## 3 运行环境

### 3.1 Python 发现

按顺序，任一步成功即停：

1. 设置 `aitest.pythonPath`；
2. 工作区 `.venv/Scripts/python.exe`；
3. `py -3`；
4. PATH 中的 `python`。

校验：版本 ≥3.11，且 `import aitest` 成功或 `aitest --version` 可执行。

### 3.2 自动准备

发现顺序全部失败后，按 `runtime.ts` 的实际顺序准备：

1. 先复用已缓存运行时：本地 probe 已安装包版本，与 VSIX 自带 wheel 版本匹配即直接使用，不联网；
2. 解析下载源与资产摘要；缺 sha256 直接拒绝，不降级到未校验包；
3. 下载便携 Python 包到 `%APPDATA%/aitest/runtime/<版本>/`；
4. 校验下载资产的 sha256，不一致即删除临时文件并停止；
5. 用系统 `tar` 解压；
6. 在运行时目录内创建 venv；
7. 用 VSIX 自带 wheel 在 venv 内执行 `pip install --no-index --find-links <wheel目录>`，不回退 PyPI；
8. 用与发现阶段相同的 probe 再核验一次，确认版本与 `import aitest` 可用。

任一步失败即停并显示阶段与原因，不静默改用其他解释器；准备完成后后续离线可用。缓存运行时的包版本或目录不可用时，只重建同一版本目录，其他版本目录保持原样。

### 3.3 升级

打开面板或检查环境时比较 wheel 版本与已安装版本；不一致则重建运行时。旧运行目录不受影响。

## 4 面板

### 4.1 数据流

```text
面板 → bridge → CLI（读命令）
CLI → JSON → bridge → 面板渲染
面板动作 → bridge → CLI（写命令）
```

### 4.2 轮询

- 运行中 2 秒轮询 `run.status`；
- 空闲不设定时轮询，打开测试/历史/问题/设置时按需读取；
- 轮询失败保留上次数据，下一次继续；不把失败写成新结论。

### 4.3 执行前确认

- 面板当前展示：范围（验收项数与用例数）、计划号与修订、依赖、会改动什么（副作用摘要）。环境、凭据边界、用例层级与预计耗时不在面板确认区展示；
- 用户勾选确认并点击开始后，`run.start` 由扩展按 `CONFIRMABLE_COMMANDS` 放行 `--confirm`；
- CLI 直调不带 `--confirm` 时，只有存在**同计划版本、同范围、同指纹**的可复用确认记录才允许启动，否则返回 `CONFIRMATION_REQUIRED`；
- 确认记录写入 `projects/<project_id>/confirmations/<confirmation_id>.json`，包含 `confirmed_by`、`confirmed_at`、计划版本、范围、环境/凭据摘要、副作用与 `fingerprint`；`run.json` 只引用 `confirmation_id`、`confirmation_fingerprint` 与 `confirmation_reused`；
- 可复用记录取最近一次运行的 `confirmation_id`，并核对 `plan_id`、`plan_revision`、`scope` 与指纹；计划版本、范围、环境、凭据或副作用任一变化都需要重新确认。

### 4.4 信息架构

- 顶层只有测试 / 问题 / 历史，设置收在右上角；
- 任务 → 计划 → 运行：任务是稳定对象，计划是合同，运行是尝试；
- 计划、运行、结果共用一张验收项台账；计划/报告/问题详情是二级文档页；
- 范围变化生成新计划；计划内改用例生成新修订；任务验收项/验证方式变化使旧计划过期；
- 运行启动时写计划快照与任务快照；历史运行使用快照，不混用当前任务文字。

### 4.5 只读命令

- `issue.show` 返回完整问题（发生记录、证据、处置）；
- `report.show` 返回完整报告；`report.list` 返回某次运行的报告版本；
- `run.status` 返回运行目录里的计划/任务快照与最近报告 ID；
- `run.list`/`issue.list` 带列表所需摘要，面板不逐条补查；
- 面板只消费这些事实，不重算判定、覆盖或 Mock 真实性。

### 4.6 展示边界

- 结论、覆盖、Mock、分析都来自 CLI 输出；
- 面板不合并结论与摘要；
- 缺证据、截断、未知都显式标注。

### 4.7 面板命令边界

- 扩展在 `ext_commands.ts` / `extension.ts` 用 `PANEL_COMMANDS` 白名单放行面板调用；白名单之外一律 `COMMAND_NOT_ALLOWED`，即使 CLI 已注册；
- `panel/src/host/commands.ts` 实际接入 51 条命令；`link.assert` 等已有面板入口；
- `delivery.show`、`plan.update`、`project.update` 已在 `PANEL_COMMANDS` 白名单；计划保存/交付读取的宿主回归仍待补；
- `plan.publish`、`data.cleanup` 等命令不在白名单，面板不能直接调用。
- `project.map`、`project.map.refresh`、`run.quick` 已注册并在白名单放行；`panel/src/host/commands.ts` 与面板入口均已接入。

## 5 安装与升级

- 扩展安装后首次打开面板触发环境检查；
- 缺 Python 时按 §3 准备；
- 缺 `gh` 时提示安装与登录命令，由人执行；
- 升级只替换 wheel 与面板资源，不动工作空间数据。

## 6 安全

- 密钥只从环境变量或用户配置读，不写入工作空间；
- CLI 输出前统一脱敏；
- 扩展不执行模型生成的代码，执行只发生在 worker；
- 生成脚本先过危险操作扫描。

## 7 测试

- 契约测试：命令、字段、错误码、枚举；
- bridge 单测：超时、非零退出、非法 JSON；
- 面板测试：确认流程、轮询、错误态；
- 环境测试：Python 发现顺序、缺失提示、版本不匹配。
