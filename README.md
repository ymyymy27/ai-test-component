<div align="center">

# AI Test Component

**面向 Windows 11 x64、Python 3.11+ 与 Trae 的轻量本地 AI 辅助测试组件**

纯 Python 零绑定 CLI 内核 · 共享 Vue 3 测试工作台 · Trae 轻量扩展 · 跨智能体外部桥接

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)
![Version](https://img.shields.io/badge/version-0.5.0-0e6655)
![Python](https://img.shields.io/badge/python-3.11--3.13-3776ab)
![IDE](https://img.shields.io/badge/Trae%20%2F%20VS%20Code-%5E1.85.0-111318)

[快速开始](#-快速开始) · [核心能力](#-核心能力) · [系统架构](#-系统架构) · [配置与工作流](#-常用工作流与配置) · [文档导航](#-文档导航)

</div>

---

## 为什么做 AI Test Component

在 IDE 里做本地自测或提测前验证，最容易踩四个坑：**改动影响面不清**、**Mock 或表面 200 掩盖真缺陷**、**失败缺可复现证据**、**AI 推测与真实执行结果混为一谈**。

**AI Test Component** 用确定性工程门禁把 AI 辅助测试拉回“事实优先、可复现、可审计”的轨道：

- **四态严格收敛**：业务结论只认 `passed / failed / incomplete / not_applicable`，未执行、阻塞或未知绝不汇总成通过，空分母不显示 100%。
- **显式人工确认边界**：生成测试计划与执行运行严格分离，`run.start` 必须由人显式确认（绑定计划修订、范围、环境与凭据指纹），AI 不能代替确认。
- **不污染被测仓库**：所有运行记录、证据、报告、缓存与导出均保存在用户级工作空间（默认 `%APPDATA%/aitest/workspaces/default`），被测源码目录零侵入。
- **可独立降级**：模型、GitHub 或调用链适配器不可用时，本地项目、计划、执行、证据、问题与报告导出仍可正常使用。

> **重要边界**：本仓库同时包含可运行产品代码与工程合同文档。当前验收状态只以 [tests/acceptance/p1/status.json](tests/acceptance/p1/status.json)（17 FR / 31 AC）为准；自动化测试、组件构建或基准考题通过，均不替代真实 Trae 窗口手动终验。

---

## 快速开始

### 前置要求

- **操作系统**：Windows 11 x64
- **Python**：`3.11`—`3.13`（推荐 `uv` 管理虚拟环境；CI 主任务使用 `3.13`）
- **Node.js / pnpm**（仅构建面板与 Trae 扩展时需要）：Node.js `20+`
- **宿主（可选）**：Trae（兼容 VS Code Engine `^1.85.0`）；脱离 IDE 也可纯 CLI 或通过外部智能体桥接运行

### 1. 安装依赖并验证 Python CLI

```powershell
# 安装 Python 运行与开发依赖
uv sync --extra dev --locked

# 查看 CLI 版本
.venv/Scripts/python.exe -m aitest.cli --version

# 运行环境自检（自动初始化默认工作空间）
.venv/Scripts/python.exe -m aitest.cli doctor --workspace "$env:APPDATA/aitest/workspaces/default"
```

### 2. 构建共享面板与 Trae 扩展（VSIX）

```powershell
# 安装面板与扩展依赖
npm --prefix panel ci
npm --prefix integrations/trae ci

# 一键构建面板、打包 Python wheel 与 VSIX，并生成 SHA-256 校验清单
npm --prefix panel run build:extension
npm --prefix integrations/trae run package
uv run python scripts/verify_vsix.py --write-manifest
```

打包完成后，将产物 `integrations/trae/dist/aitest-trae.vsix` 在 Trae 扩展面板中通过 **“从 VSIX 安装…”** 导入即可使用。完整安装、模型配置与真实 Trae 手动终验清单见 [安装与操作说明](docs/项目文档/一期/06-发布运维/03-安装与操作说明.md)。

---

## 核心能力

### 1. 压低漏测率与误报率的确定性内核
- **开测前环境安检（`core/preflight.py`）**：按服务依赖拓扑核对端口状态、健康复用、环境变量与工具可用性，环境冲突直接判 `blocked` 而不混作业务缺陷。
- **免 AI 确定性硬探针（`core/probes.py`）**：自动对账前后端路由与契约、静态资源状态码与 MIME、路由边界异常，不依赖模型即可稳定复现基础缺陷。
- **跑前脚本 AST 安检与两阶段隔离（`core/script_linter.py`）**：`setup` 与 `verify` 物理分离，跑前静态拦截无断言、恒真断言、吞异常与危险命令脚本。
- **后台日志时间窗抓“假通过”（`core/service_logs.py`）**：按字节偏移精确截取单用例执行期间的服务增量日志，表面返回 200 但后台出现未捕获 `Traceback` 或 `HTTP 50x` 立即判失败。
- **独立裁判、三档可信度与双盲冲突复核（`core/judge.py`）**：结合防抖重跑与确定性证据区分 `confirmed` / `suspected` / `blocked`，并在计划阶段对需求与实现口径冲突冒泡提醒。
- **按项目并发锁与落盘前脱敏（`storage/`、`security.py`）**：多项目操作互不阻塞；已知凭据在落盘、日志、报告与模型发送前自动脱敏，单文件证据支持流式 20MB 截断。

### 2. 全栈代码图、只读 Git 影响分析与项目错题本
- **只读仓库索引与 4 层代码图（`core/repo_index.py`、`core/code_graph.py`）**：遵循 `.gitignore` / `.aitestignore`，构建 `frontend → api → backend → storage` 四层依赖链路与源码坐标，标识跨层冲突与双向影响面。
- **只读本地 Git 引擎（`core/git_engine.py`）**：采集分支、HEAD、脱敏远端 URL、未提交改动、`diff --stat` 与变更行块，非 Git 目录自动优雅降级。
- **仓库共享配置与项目错题本（`core/shared_config.py`）**：支持仓库根目录无密钥 `.aitest.json` 与个人工作空间配置合并，可将已知预期行为登记为「项目正常约定」以消除重复误报。

### 3. 共享 Vue 3 工作台与 Trae 原生联动
- **统一任务台账与可视图**：覆盖项目概览、范围选择、测试计划、执行前确认、运行监控、结果/报告/调用链三合一视图、问题台账与运行历史，支持深浅双主题与 3 断点响应式布局。
- **排障最后一公里**：失败用例支持一键跳转源码行、复制最小复现命令与结构化「AI 修复任务包」，并在 Trae 侧推送原生 `Problems` 诊断红线。

### 4. 跨智能体外部桥接（Agent Bridge）
- 独立于核心包的外部出口 [`integrations/agent-bridge/`](integrations/agent-bridge/README.md) 与标准 [`SKILL.md`](integrations/agent-bridge/SKILL.md)，通过 stdio JSON-RPC 2.0 暴露 `aitest_doctor`、`aitest_project_map`、`aitest_run_start`（强制人工确认守卫）等工具，供 Codex / Google Antigravity / Claude Code 安全调用本地 CLI。

---

## 系统架构

组件采用 **「单次 CLI 进程 + 独立运行 Worker + 文件工作空间」** 架构，**无常驻后台守护进程、无命名管道、无数据库、无内置 MCP**。

```text
Trae 扩展 (integrations/trae/)  |  跨智能体桥接 (integrations/agent-bridge/)  |  纯命令行
                                │  JSON over stdio（按需拉起单次进程）
                                ▼
Python CLI 内核 (src/aitest/，当前注册 54 个命令，处理完毕即退出)
 ├── core/      领域规则、四态判定、S/M 覆盖、repo_index/代码图、Git 只读、preflight/probes/script_linter/judge
 ├── storage/   用户级工作空间、分项目排他写锁、原子写、流式脱敏证据、无锁备份与恢复
 ├── runners/   命令/pytest、HTTP、JUnit 解析与断言执行器
 └── adapters/  模型（二次脱敏与 Token 预算刹车）、GitHub（只读 gh api）、链路日志与查询
                                │
                                ▼
           用户级工作空间（JSON / Markdown / 脱敏证据 / 备份 ZIP，零侵入被测仓库）
```

---

## 常用工作流与配置

### CLI 调用约定

所有 CLI 命令遵循统一 JSON 信封协议（`--input` 文件与 stdin 等价，stdout 只输出单行 JSON，日志输出至 stderr；当前注册 **54** 个命令）：

```powershell
# 1. 登记本地项目
echo '{"command":"project.add","request_id":"req-1","payload":{"root":"D:\\work\\demo"}}' | `
  .venv/Scripts/python.exe -m aitest.cli project.add --workspace "$env:APPDATA/aitest/workspaces/default"

# 2. 导出正式验收报告（Markdown）
echo '{"command":"report.export","request_id":"req-2","payload":{"project_id":"proj_xxx","report_id":"rep_xxx"}}' | `
  .venv/Scripts/python.exe -m aitest.cli report.export --workspace "$env:APPDATA/aitest/workspaces/default"
```

### 仓库共享配置示例（`.aitest.json`，可选，严禁放密钥）

```json
{
  "l1": {
    "build": "pnpm build",
    "smoke_command": "pytest -q"
  },
  "services": {
    "base_url": "http://127.0.0.1:8000",
    "health_url": "http://127.0.0.1:8000/health"
  },
  "evidence": {
    "single_file_mb": 20,
    "media_mb": 500
  }
}
```

---

## 版本与代码规模

| 指标 | 当前值 |
| --- | --- |
| 产品版本 | **0.5.0**（`pyproject.toml`、`panel/package.json`、`integrations/trae/package.json` 同步） |
| 后端规模 | **77** 个 Python 模块，约 **33,731** 行；注册 **54** 条 CLI 命令 |
| 自动化测试 | **556** 个 pytest 节点（含 12 题跨类型埋雷基准考题库）；面板 vitest **158** 项 + Playwright E2E **51** 项；扩展 node test **101** 项 |
| 交付范围 | 一期 **17 FR / 31 AC**（验收登记见 [tests/acceptance/p1/status.json](tests/acceptance/p1/status.json)） |
| 版本记录 | [CHANGELOG.md](CHANGELOG.md) |

---

## 仓库结构

```text
src/aitest/                 Python 核心包：cli/worker、命令注册表、core 领域规则与门禁、storage、runners、adapters
panel/                      共享前端面板源码（Vue 3 + TypeScript + Vite）、Vitest 与 Playwright E2E 测试
integrations/trae/          Trae 轻量扩展外壳（CLI 桥接、便携运行时引导、HostCapabilities）与 Node 测试
integrations/agent-bridge/  独立跨智能体 stdio JSON-RPC 适配器与 SKILL.md
tests/
├── unit/                   单元测试、契约门禁、基准考题与真实项目回归套件
├── fixtures/               12 题跨类型埋雷基准考题库与测试夹具
└── acceptance/             p1/p2/p3 验收状态登记（status.json）与真实环境证据
scripts/                    版本一致性、文档事实对账、台账审计、wheel 冒烟与 VSIX SHA-256 校验
docs/
├── 项目文档/                产品合同（一期为当前交付范围，二期/三期为后置占位）
└── 修改日志/ymy/            轻量 Trae 插件重构计划（执行基线）
```

---

## 文档导航

| 文档 | 说明 |
| --- | --- |
| [项目文档总览](docs/项目文档/README.md) | 一期产品合同、架构、设计与验收文档唯一入口 |
| [一期项目简报](docs/项目文档/一期/01-项目/01-项目简报.md) | 项目定位、交付范围、成功标准与约束 |
| [一期需求规格](docs/项目文档/一期/02-需求/02-需求规格.md) | 17 FR / 31 AC 范围与验收场景唯一来源 |
| [一期功能清单](docs/项目文档/一期/02-需求/03-功能清单.md) | 业务结论、覆盖口径、Mock 与统计唯一口径 |
| [产品方案与界面设计](docs/项目文档/一期/03-设计/01-产品方案与界面设计.md) | 页面信息架构、交互规则与响应式断点规范 |
| [CLI 与数据契约](docs/项目文档/一期/03-设计/03-CLI与数据契约.md) | 54 条命令、请求信封、字段、枚举与错误码 |
| [架构总览](docs/项目文档/一期/04-架构/01-架构总览.md) | 进程模型、分层依赖方向、模块职责与失败边界 |
| [安装与操作说明](docs/项目文档/一期/06-发布运维/03-安装与操作说明.md) | 安装指南、排障说明与真实 Trae 手动终验清单 |
| [协作指南](AGENTS.md) | 仓库开发约束、文档主责与验收登记硬边界 |

---

<details>
<summary><b>展开查看全套开发与发布验证命令</b></summary>

```powershell
# 1. Python 静态检查、严格类型检查、版本/台账校验与全量测试
.venv/Scripts/python.exe scripts/check_versions.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe scripts/audit_ledger.py --check
.venv/Scripts/python.exe -m pytest -rs --tb=short -o addopts=""

# 2. 共享面板检查、单元测试、E2E 与扩展静态产物构建
npm --prefix panel run typecheck
npm --prefix panel run lint
npm --prefix panel test
npm --prefix panel run build:extension

# 3. Trae 扩展检查、构建、测试与 VSIX 哈希校验
npm --prefix integrations/trae run check
npm --prefix integrations/trae run build
npm --prefix integrations/trae test
npm --prefix integrations/trae run verify:vsix

# 4. 文档与代码事实全量对账
.venv/Scripts/python.exe scripts/check_docs_facts.py --full
```

- 运行全量 `pytest` 期间请勿并发启动其他重测试，避免环境负载引入假失败。
- 严禁手工修改 `integrations/trae/media/panel/` 生成物或未实测篡改 `tests/acceptance/*/status.json`。

</details>

---

## 许可证

本项目为内部工程（`Proprietary`），使用与分发以仓库及所属组织规定为准。
