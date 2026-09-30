# AI 辅助测试插件（AI Test Component）

> 面向 Windows 11 x64、Python 3.13 与 Trae 的本地 AI 辅助测试插件工程。目标是将项目与计划、分层执行、证据、判定、报告、问题回归和恢复组织成同一套可追溯闭环。
>
> 当前版本为 **0.4.0 开发阶段**：核心分层、部分规划/执行能力和本地存储底座已经实现，但尚未形成完整产品闭环，一期 35 项 AC 均未完成真实环境验收。

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.4.0-orange.svg)](CHANGELOG.md)

---

## 目录

- [项目简介](#项目简介)
- [当前状态](#当前状态)
- [核心能力](#核心能力)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [命令行入口](#命令行入口)
- [验证与验收](#验证与验收)
- [文档导航](#文档导航)
- [已知限制](#已知限制)

---

## 项目简介

本项目采用“入口 → 接口适配 → 应用用例 → 纯领域规则”的依赖方向，以本地文件工作空间保存不可变记录和证据。Trae、面板、CLI 与 stdio MCP 计划复用同一核心，不在宿主侧复制业务判定。

工程按三期演进：

| 期次 | 目标 |
| --- | --- |
| 一期 | Trae 上的本地测试闭环，共 17 FR / 35 AC |
| 二期 | 增加编辑器和操作系统组合、源码定位、依赖/链路图及能力诊断 |
| 三期 | 使用平台身份，将已保存结果安全投影并上传，不改变一期本地能力 |

## 当前状态

| 范围 | 现状 |
| --- | --- |
| 项目与规划 | 已有项目上下文、规则/用例/计划领域模型、草稿与发布编排、`prepare_run`、模型出站策略及六个模板 |
| 执行与证据 | 已有串行运行器、命令执行、超时/停止、流式 spool、采集前脱敏、恢复、证据发布和 ExecutionFacts 组装 |
| 本地底座 | 已有工作空间身份、写锁、不可变修订、内容寻址对象、有限查询及统一装配入口 |
| 产品入口 | CLI 仅提供离线诊断和模板发现；面板、Trae、命名管道及 MCP relay 尚未接通业务闭环 |
| 报告与问题 | 完整判定、报告修订/导出、缺陷复测与关闭仍待实现 |
| 一期验收 | 0/35 项通过真实环境验收；详见[当前代码分析](docs/当前代码分析与一期工程对比.md) |

## 核心能力

### 项目、规则与计划

- 建模本地项目、模块、依赖、任务、交付、环境与源码身份
- 管理规则版本、测试用例、冻结计划、档位和执行准备结果
- 支持人工草稿路径与受策略约束的模型请求编排
- 内置 Python 库、Python 服务、HTTP、Agent、人工 Web 和工单流程六类模板

### 执行与证据

- 以 Run → Step → Attempt 记录执行事实
- 串行调度命令，处理超时、停止、重试与未知副作用
- 分流保存 stdout/stderr，使用游标和摘要支持增量采集与恢复
- 在材料落盘前执行敏感信息过滤，并登记过滤摘要和证据缺口

### 本地数据底座

- 工作空间身份校验和单写锁
- 不可变记录修订、预期版本检查和内容寻址对象
- 有限 QuerySpec、索引、完整性和备份原语
- `bootstrap.py` 作为唯一核心装配入口

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 核心 | Python 3.13、Pydantic 2 |
| 本地存储 | JSON/二进制文件、portalocker、原子替换 |
| 面板 | TypeScript、esbuild、Playwright |
| Trae 扩展 | VS Code Extension API、TypeScript、VSIX |
| 工程工具 | uv、pytest、ruff、mypy、Hatchling |

## 系统架构

```text
用户 / Trae / CLI / MCP
          │
          ▼
入口层：Trae 扩展 · 共享面板 · CLI · stdio relay
          │
          ▼
接口层：LocalAPI · 本地管道 · DTO · 版本/能力协商
          │
          ▼
应用层：项目 · 规划 · 执行 · 证据 · 报告 · 连接诊断
          │
          ▼
领域层：不可变对象 · 状态机 · 判定与统计规则
          │
          ▼
基础设施：文件工作空间 · 执行/模型/源码适配器
```

应用层只通过 `application/ports.py` 使用外部能力，由 `bootstrap.py` 注入实现。外部执行、模型和网络调用位于短事务之外。

## 项目结构

```text
ai-test-component/
├── src/aitest/
│   ├── domain/                 # 项目、计划、执行、证据、报告与问题规则
│   ├── application/            # 用例编排与外部能力端口
│   ├── infrastructure/         # 文件存储及执行/模型/源码适配器
│   ├── contracts/              # Pydantic 合同与生成 Schema
│   ├── interfaces/             # LocalAPI、CLI、管道与 relay
│   ├── resources/              # 六个模板与共享面板源码
│   └── bootstrap.py            # 唯一核心装配入口
├── integrations/trae/          # Trae 薄扩展候选实现
├── tests/                      # unit / contracts / architecture / recovery / acceptance
├── docs/项目文档/              # 现行需求、功能、架构与设计合同
├── docs/接口对接/              # 跨包合同、裁定与交付材料
├── docs/修改日志/              # 分包修改记录
├── pyproject.toml
└── uv.lock
```

## 快速开始

### 环境要求

- Windows 11 x64（一期目标环境）
- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+ 与 npm（仅面板和 Trae 扩展需要）

### 安装开发依赖

```powershell
git clone https://github.com/ymyymy27/ai-test-component.git
cd ai-test-component
uv sync --extra dev --locked
```

### 运行 Python 检查

```powershell
uv run python scripts/check_versions.py
uv run ruff check .
uv run mypy
uv run python scripts/generate_schemas.py
uv run pytest
```

### 构建面板与 Trae 扩展

```powershell
npm --prefix src/aitest/resources/panel ci
npm --prefix src/aitest/resources/panel run build
npm --prefix integrations/trae ci
npm --prefix integrations/trae run package
```

生成的 VSIX 位于 `integrations/trae/dist/aitest-trae.vsix`。它目前只是候选扩展，编译或打包成功不等于真实 Trae 兼容通过。

## 命令行入口

```powershell
uv run aitest templates
uv run aitest doctor
uv run aitest mcp-relay --binding example
```

- `templates`：列出六个已打包模板及其当前状态。
- `doctor`：执行离线骨架诊断；当前不会打开业务工作空间，返回 `NOT_READY` 和退出码 2。
- `mcp-relay`：预留命令；当前明确返回不可用，不向 stdout 伪造 MCP 消息。

## 验证与验收

自动化测试覆盖部分领域规则、合同、依赖边界、存储故障和恢复行为，但不能替代产品验收。P1-AC01—35 的状态统一记录在 [`tests/acceptance/p1/status.json`](tests/acceptance/p1/status.json)。

截至 2026年9月30日的本地复核结果：pytest 为 **592 通过、4 失败**，ruff、mypy 与生成 Schema 一致性检查也存在失败。具体问题和未验证边界见[当前代码分析](docs/当前代码分析与一期工程对比.md)。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [文档总览](docs/项目文档/README.md) | 按期次进入需求、功能、架构和界面设计 |
| [总体架构](docs/项目文档/总体架构.md) | 分层、模块、运行形态和目标结构 |
| [阅读索引](docs/项目文档/阅读索引.md) | 字段、状态、规则和主责合同入口 |
| [一期架构总览](docs/项目文档/一期/架构文档/00-架构总览.md) | 一期本地闭环的模块与边界 |
| [接口对接台账](docs/接口对接/README.md) | 跨包合同、裁定和交付状态 |
| [当前代码分析](docs/当前代码分析与一期工程对比.md) | 实际实现、测试结果和一期差距 |
| [更新记录](CHANGELOG.md) | 精简版本与阶段变更 |

## 已知限制

- 尚无可供用户完成“建项目 → 发计划 → 执行 → 留证 → 判定 → 报告”的端到端入口。
- 命名管道、MCP relay、真实模型、源码快照及多类执行适配器尚未实现。
- 报告、缺陷管理和完整产品恢复流程尚未完成。
- Trae 扩展尚未在目标 Trae 版本完成安装、Webview、目录切换和生命周期验证。
- 当前质量门不是全绿状态，不能将构建或局部测试结果写成一期验收通过。

---

项目为内部工程，许可证以仓库和组织规定为准。
