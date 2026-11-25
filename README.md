# AI 辅助测试插件（AI Test Component）

面向 Windows 11 x64、Python 3.11+ 与 Trae 的轻量本地 AI 辅助测试组件：**Python 零绑定内核 CLI + 共享 Vue 3 面板 + Trae 扩展外壳 + 跨智能体外部适配出口**。通过开测前安检门、盲写预期隔离、免 AI 确定性硬探针、两阶段 `setup/verify` + 跑前 AST 安检、后台服务日志抓假通过、独立裁判与三档可信度，结合全栈 4 层代码链路图与只读本地 Git 影响分析，把漏测率、误报率与排障最后一公里成本降到最低。

仓库同时包含可运行产品代码与工程合同文档。**验收状态只看 [tests/acceptance/p1/status.json](tests/acceptance/p1/status.json)**（及 `p2/p3`）；产品合同见[项目文档](docs/项目文档/README.md)，重构执行基线见[修改计划](docs/修改日志/ymy/2026-11-16-轻量Trae插件重构计划.md)。**组件可构建、自动化单元测试或考题库通过，均不替代真实 Trae 窗口手动终验。**

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)

---

## 版本与代码规模

| 项 | 当前值 |
| --- | --- |
| 产品版本 | **0.5.0**（`pyproject.toml`、`panel/package.json`、`integrations/trae/package.json` 一致） |
| 代码规模 | **80** 个 Python 模块，约 **29,624** 行；**462** 个 pytest 节点；面板 vitest **155**、扩展 node test **101** |
| 基准考题 | 12 题跨类型埋雷基准考题库（CLI / Python 库 / 纯 API / 单文件 Web / 前后端分离全栈，召回率/一致率门禁） |
| 运行环境 | Windows 11 x64；Python 3.11—3.13（完整回归与 CI 主任务使用 3.13，3.11/3.12 另有单元兼容任务） |
| 宿主与出口 | Trae（VS Code 兼容扩展，可选复用 `HostCapabilities`）；外部跨智能体适配器 `integrations/agent-bridge/` + `SKILL.md`；面板可在浏览器调试 |
| 版本历史 | [CHANGELOG](CHANGELOG.md) |

---

## 系统架构全景

本组件坚持「**1 个纯 Python 零绑定内核 + 4 大扩展插槽 + 3 大超薄出口 + 可选宿主能力插头**」分层：

1. **纯 Python 零绑定内核（`src/aitest/`）**
   - 严格四态业务结论（`passed / failed / incomplete / not_applicable`）、`S/M` 覆盖口径、全栈代码图、只读 Git 影响分析、项目错题本与断点续跑均只在核心 `core/` 计算，任何面板、宿主扩展或外部适配器不得重算结论或直接写业务文件。
   - 架构红线：内核**不做**常驻核心、命名管道、数据库、迁移框架或内置 MCP；所有命令通过单次 CLI 进程或独立 `worker` 进程按项目写锁与原子写落盘。
2. **4 大可扩展插槽（[`src/aitest/core/plugins.py`](src/aitest/core/plugins.py)）**
   - `ContextProvider`：按技术栈（Python 后端 / Frontend Web / OpenAPI）提取结构化项目上下文；
   - `DeterministicProbe`：免 AI 确定性硬探针（前后端契约对账、静态资源全 200 与 MIME 检查、路由边界 5xx 探针及按需启用的 `LivePageProbe` 真页面活 DOM 巡检）；
   - `ScriptLinter`：按脚本语言分发的跑前 AST 静态安检器（Python / JS·TS），阻断无断言、恒真断言、吞异常及无写后读回水脚本；
   - `Isolator`：轻量测试数据前缀隔离器（`PrefixIsolator`）与本地文件快照还原器（`LocalFileSnapshotIsolator`）。
3. **3 大超薄出口**
   - **JSON CLI**：`python -m aitest.cli <command> --workspace <dir>`，当前注册 **60** 个命令；
   - **共享 Vue 面板 + Trae 扩展**（`panel/` + `integrations/trae/`）：内嵌全栈代码图与 Git 状态条，并与宿主双向联动；
   - **跨智能体外部适配出口**（[`integrations/agent-bridge/`](integrations/agent-bridge/README.md)）：独立于核心包的 stdio JSON-RPC 2.0 薄适配器 + [`SKILL.md`](integrations/agent-bridge/SKILL.md)，供 Codex / Google Antigravity / Claude Code 调用（仅安全转发与只读投影，启动运行强制校验人类显式确认）。
4. **可选宿主能力插头（[`integrations/trae/src/host_capabilities.ts`](integrations/trae/src/host_capabilities.ts)）**
   - 在 Trae 中优先复用宿主 Git、文档符号、文件保存监听（即时标过期）、源码行跳转（`showTextDocument`）、Problems 面板诊断红线（`DiagnosticCollection`）、`SecretStorage`、`OutputChannel` 与 `StatusBarItem`；脱离 IDE（纯 CLI 或外部智能体）时自动优雅回退内核原生实现。

---

## 核心能力详解

### 1. 高准确度闭环内核（压低漏测率与误报率）
- **开测前环境安检门（`core/preflight.py`）**：启动运行前按拓扑顺序检查多服务依赖（`db -> backend -> frontend`）、端口空闲/健康复用/占用冲突、环境变量与工具可用性，前置冲突直报阻塞而不混为业务缺陷。
- **盲写预期物理隔离（`core/blind_expectation.py`）**：计划生成第 1 步仅基于需求、前端调用与 OpenAPI 契约冻结请求结构与预期断言，隔离后端实现源码防“顺着 bug 改预期”。
- **免 AI 硬探针与按需真页面巡检（`core/probes.py`、`core/live_page_probe.py`）**：自动核对前后端路由/字段契约、静态资源 MIME/200、路由边界；配置 `probes.live_page.enabled=true` 时按需通过 Playwright 或本机无头 Edge/Chrome 巡检活 DOM 白屏、框架错误浮层、Console Error 与 4xx/5xx 响应（无浏览器环境自动 `skipped` 降级）。
- **`setup / verify` 两阶段与跑前脚本安检（`core/script_linter.py`）**：前置准备与业务验收物理分离（前置异常判 `blocked` 避免跨模块连坐）；跑前拦截空断言、退化断言、静默吞异常脚本。
- **后台日志窗口抓“假通过”（`core/service_logs.py`）**：按字节偏移量截取单用例执行时间窗内服务新增日志，即使前端/接口表面返回 200，只要出现未捕获 `Traceback` 或 `HTTP 50x` 立即判失败并列为铁证。
- **防抖重跑 + 独立裁判 + 三档可信度（`core/judge.py`）**：首次失败干净重跑防抖；连续失败经确定性证据与独立裁判区分 `🔴 铁证缺陷（confirmed）` / `🟡 疑似问题（suspected）` / `⚪ 环境阻塞（blocked）`。
- **分项目并发锁、流式 20MB 脱敏截断与 Token 预算刹车**：多项目操作互不阻塞；`backup.create` / `run.repair` 移出全局长锁；大日志边分块脱敏边计字节截断；模型调用发送前二次脱敏并受 `max_token_budget` 控制。

### 2. 无上限全栈代码图、只读本地 Git 与项目错题本
- **全栈代码图（`core/code_graph.py`）**：突破文件数限制且严格遵循 `.gitignore` / `.aitestignore`，构建「前端页面 (`frontend`) → 接口 (`api`) → 后端模块 (`backend`) → 数据存储 (`storage`)」4 层有向链路图与源码坐标，按业务模块增量缓存与失效，自动标识跨层契约/资源冲突与 2 跳双向影响面。
- **完整只读本地 Git 引擎（`core/git_engine.py`）**：采集分支、HEAD/作者/主题、领先/落后提交、脱敏远端 URL（剥离用户名/密码/Token）、工作区/暂存区/未跟踪改动、`diff --stat`、变更行块（hunks）及高频修改热点；非 Git 目录安全降级。
- **项目错题本与 `.aitest.json`（`core/shared_config.py`）**：支持仓库级无密钥共享配置 `.aitest.json` 与个人工作空间两层合并，支持按匹配规则将预期行为标为「项目正常约定」，对硬探针和独立裁判精准豁免消噪；支持 `local` / `staging` 多环境档案切换。

### 3. 共享 Vue 3 面板与 Trae 原生体验
- **项目概览可视图与 Git 状态条**：顶部展示分支/提交/脏文件与增量变更，4 层架构图/列表视图可缩放、平移、按模块/冲突/受影响范围筛选，并一键发起「仅测此节点范围」或「一键只测受影响范围」。
- **统一设计系统与无障碍门禁**：深浅高对比度双主题；全站严格 3 断点自适应（`900px` 抽屉 / `768px` 中屏 / `620px` 窄屏与 390px 侧栏卡片化摘要，全站通用表格自动转为紧凑卡片）；所有非 `tokens.css` 文件零硬编码色值；统一 `FormField.vue` 无障碍表单与 `aria-invalid`/`aria-describedby`/`role="alert"`。
- **铁证缺陷最后一公里**：失败详情支持一键跳源码对应行、复制「最小复现命令」及包含失败阶段/预期与实际/后台异常日志/源码坐标的结构化「AI 修复任务包」，并在 Trae 侧推送原生 `Problems` 诊断红线、写入 `OutputChannel` 与 `StatusBarItem`。

### 4. 跨智能体协同（Codex / Google Antigravity / Claude Code）
- 详见 [integrations/agent-bridge/README.md](integrations/agent-bridge/README.md) 与标准技能描述 [integrations/agent-bridge/SKILL.md](integrations/agent-bridge/SKILL.md)。
- 暴露 `aitest_doctor`、`aitest_project_map`、`aitest_run_start`（强制人工确认守卫）、`aitest_report_summary`、`aitest_issues_list` 等 10 个工具，通过标准 stdio JSON-RPC（`Content-Length` 与 NDJSON 双帧）安全复用本地 CLI 内核。

---

## 安装、构建与快速开始

### 环境准备与 Python CLI

```powershell
uv sync --extra dev --locked

.venv/Scripts/python.exe -m aitest.cli --version
.venv/Scripts/python.exe -m aitest.cli doctor --workspace "$env:APPDATA/aitest/workspaces/default"
```

CLI 约定：`python -m aitest.cli <command> --workspace <dir> --input <json>`（等价支持 stdin JSON）；stdout 只输出一行 JSON，日志走 stderr。当前注册 **60** 个命令。

### 共享面板与 Trae 扩展构建、打包和哈希校验

```powershell
npm --prefix panel ci
npm --prefix integrations/trae ci

# 一键完成前端检查构建、Python wheel + 多版本离线依赖构建、VSIX 打包与 SHA-256 对拍校验
npm --prefix panel run build:extension
npm --prefix integrations/trae run package
uv run python scripts/verify_vsix.py --write-manifest
```

- `integrations/trae run build` 会自动重编 `panel/` 并同步至 `integrations/trae/media/panel/`，切勿手改生成物。
- VSIX 产物与清单输出到 `integrations/trae/dist/aitest-trae.vsix` 及 `.sha256`；安装向导及 **真实 Trae 窗口终验手动检查清单**见 [安装与操作说明](docs/项目文档/一期/06-发布运维/03-安装与操作说明.md)。

---

## 仓库结构

```text
src/aitest/                 Python 核心包：cli/worker、commands & command_handlers、core（判定/代码图/Git/探针/安检/裁判/错题本）、storage、runners、adapters
integrations/trae/          Trae 轻量扩展外壳（bridge/runtime/HostCapabilities）与 Node 测试
integrations/agent-bridge/  独立跨智能体 stdio JSON-RPC 适配器与标准 SKILL.md（纯外部入口，不侵入核心）
panel/                      共享面板源码（Vue 3 + TypeScript + Vite）与 Vitest/无障碍门禁测试
tests/fixtures/             12 题跨类型埋雷基准考题库
tests/unit/                 行为、契约、设计门禁、基准考题与 AST 红线测试
tests/acceptance/           p1/p2/p3 验收状态登记（status.json）与真实环境证据
scripts/                    版本校验、文档事实对账、台账检查、wheel 冒烟与 VSIX 哈希校验
docs/
├── 项目文档/                产品合同：一期为当前范围，二期/三期后置占位
└── 修改日志/ymy/            轻量 Trae 插件重构计划（执行基线）
```

---

## 文档导航

| 入口 | 用途 |
| --- | --- |
| [项目文档总览](docs/项目文档/README.md) | 一期产品合同、架构、设计与测试验收唯一入口 |
| [重构计划](docs/修改日志/ymy/2026-11-16-轻量Trae插件重构计划.md) | 轻量重构的执行基线、人工动作边界与工作包 |
| [项目简报](docs/项目文档/一期/01-项目/01-项目简报.md) | 背景、定位、一期范围与成功标准 |
| [需求规格](docs/项目文档/一期/02-需求/02-需求规格.md) | 18 FR / 32 AC、范围与验收场景 |
| [功能清单](docs/项目文档/一期/02-需求/03-功能清单.md) | 判定、覆盖、Mock 与统计唯一口径 |
| [产品方案与界面设计](docs/项目文档/一期/03-设计/01-产品方案与界面设计.md) | 界面信息架构、可视项目图、3 断点规范与交互规则 |
| [CLI 与数据契约](docs/项目文档/一期/03-设计/03-CLI与数据契约.md) | 60 条命令、请求信封、字段、枚举与错误码 |
| [安装与操作说明](docs/项目文档/一期/06-发布运维/03-安装与操作说明.md) | 安装配置、排障指南与真实 Trae 窗口终验手动检查清单 |
| [验收登记](tests/acceptance/p1/status.json) | 按需求场景逐项登记；`verified` 需真实环境验证与证据路径 |

---

## 全套验证命令

```powershell
# Python 静态检查、类型门禁、版本/台账/文档事实校验与全量回归
.venv/Scripts/python.exe scripts/check_versions.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe scripts/audit_ledger.py --check
.venv/Scripts/python.exe -m pytest -rs --tb=short -o addopts=""

# 共享面板与 Trae 扩展（各自目录执行）
npm --prefix panel run typecheck
npm --prefix panel run lint
npm --prefix panel test
npm --prefix panel run build:extension
npm --prefix integrations/trae run check
npm --prefix integrations/trae run build
npm --prefix integrations/trae test
npm --prefix integrations/trae run verify:vsix

# 文档与代码事实全量对账
.venv/Scripts/python.exe scripts/check_docs_facts.py --full
```

- **全量 pytest 期间切勿并发运行其他重测试**，禁止用分块或历史测试结果冒充最新全量。
- 严格禁止直接改动 `tests/acceptance/*/status.json` 冒充真实验收；所有真实 Trae 窗口核验请参照 [安装与操作说明](docs/项目文档/一期/06-发布运维/03-安装与操作说明.md) 第 9 节手动清单逐条核对并留证。

项目为内部工程，许可证以仓库与组织规定为准。
