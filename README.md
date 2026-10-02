# AI 辅助测试插件（AI Test Component）

> 面向 Windows 11 x64、Python 3.13 与 Trae 的本地 AI 辅助测试插件工程。目标是将项目与计划、分层执行、证据、判定、报告、问题回归和恢复组织成同一套可追溯闭环。
>
> 当前版本为 **0.4.0 开发阶段**：核心分层、真实文件底座、部分规划/执行能力及进程内用例入口已经实现，但尚未形成完整产品闭环，一期 35 项 AC 均未完成真实环境验收。

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

本项目采用“入口 → 接口适配 → 应用用例 → 纯领域规则”的依赖方向，以本地文件工作空间保存不可变记录和证据。Trae、面板、CLI 与 stdio MCP 按合同复用同一核心；当前产品接入尚未闭合，不在宿主侧复制业务判定。

工程按三期演进：

| 期次 | 目标 |
| --- | --- |
| 一期 | Trae 上的本地测试闭环，共 17 FR / 35 AC |
| 二期 | 增加编辑器和操作系统组合、源码定位、依赖/链路图及能力诊断 |
| 三期 | 使用平台身份，将已保存结果安全投影并上传，不改变一期本地能力 |

## 当前状态

| 范围 | 现状 |
| --- | --- |
| 项目与规划 | 项目/绑定/环境/依赖图、任务/交付/Case/验收范围/确认已有保存函数；默认B15动作含计划发布、模板正文、规则JSON导入导出和准备输入/观察冻结；运行修订守卫已有 |
| 执行与证据 | 已有串行运行器、命令执行、超时/停止、流式 spool、采集前脱敏、恢复、证据发布和 ExecutionFacts 组装 |
| 本地底座 | records权威提交台账与投影恢复、查询条件/提交/代次绑定、Blob发布前fsync及连接事实保存已实现；永久闭包、JSONL回收、有限分片与长期唯一写入者仍有缺口 |
| 产品入口 | 默认装配B15动作，worker解析Command并回Response、支持多次重连；C/D/模型与可信人工入口、业务CLI/MCP/面板/Trae消息桥仍未闭合 |
| 报告与问题 | 业务结论与等级解耦、F⊆H/FailureCandidate、归并环拒绝/P1重开及S/M/H DTO已补；生产事实聚合、持久化、真实新回归与安全导出未贯通 |
| 一期验收 | 0/35 项通过真实环境验收；详见[当前代码分析](docs/当前代码分析与一期工程对比.md) |

## 核心能力

### 项目、规则与计划

- 建模本地项目、模块、依赖、任务、交付、环境与源码身份
- 管理规则版本、测试用例、冻结计划、档位和执行准备结果；依据确认核对准确用例/修订，准备修订变化独立返回需重新准备
- 支持人工模板草稿和受策略约束的模型请求编排；模型请求先提交出站意图，再事务外调用，最后保存结果与草稿正文
- 内置 Python 库、Python 服务、HTTP、Agent、人工 Web 和工单流程六类模板

### 执行与证据

- 以 Run → Step → Attempt 记录执行事实
- 串行调度命令，处理超时、停止、重试与未知副作用
- 分流保存 stdout/stderr，使用游标和摘要支持增量采集与恢复
- 在命令 stdout/stderr 落盘前执行敏感信息过滤，并登记过滤摘要和证据缺口；模型响应的落盘前过滤仍待补齐

### 本地数据底座

- 工作空间身份校验和单写锁
- 不可变记录修订、预期版本检查、同持久意图返回原结果和内容寻址对象/历史源码字节
- 已有 QuerySpec 分页、查询条件/提交/代次绑定、完整性和备份原语；完整有限索引目录、末尾键分页及永久引用闭包仍待完成
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

截至2026年10月2日本次续查，源码基线为`develop 9bd4337`：版本、ruff、mypy（本次136个分析文件）和7份生成Schema一致性通过；本机pytest **1148 passed、2 skipped**（符号链接权限不足）。面板build和候选VSIX打包通过，静态Playwright **1 failed**（阶段标题数预期3、实际4）。该基线已记录的[Windows CI](https://github.com/ymyymy27/ai-test-component/actions/runs/37008560076)为 **1149 passed、1 failed**，符号链接恢复目标未拒绝；本机跳过该项不能视为修复通过。真实一期验收仍 **0/35**。

详见[整体分析](docs/当前代码分析与一期工程对比.md)、四包检查 [A](docs/一期工程检查-A包.md) / [B](docs/一期工程检查-B包.md) / [C](docs/一期工程检查-C包.md) / [D](docs/一期工程检查-D包.md)及[本轮证据](docs/validation/p1-audit-20261002/README.md)。四包只保留当前未闭合工作，共33项（A11/B7/C9/D6）；本次19个关键文件定向深查新增7项、细化A-06，[新证据](docs/validation/p1-audit-20261002/deep-audit-9bd4337.json)独立保存。修复过程另存修改日志和历史证据，不把条目数当缺陷数或完成率。

CI普通develop/PR范围为Windows/Python版本、静态、Schema和pytest；面板/VSIX/wheel/制品smoke只在tag路径运行。底层Windows凭据、管道和本地Git集成不替代真实Trae、模型、业务核验和掉电验收。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [文档总览](docs/项目文档/README.md) | 按期次进入需求、功能、架构和界面设计 |
| [总体架构](docs/项目文档/总体架构.md) | 分层、模块、运行形态和目标结构 |
| [阅读索引](docs/项目文档/阅读索引.md) | 字段、状态、规则和主责合同入口 |
| [一期架构总览](docs/项目文档/一期/架构文档/00-架构总览.md) | 一期本地闭环的模块与边界 |
| [接口对接台账](docs/接口对接/README.md) | 跨包合同、裁定和交付状态 |
| [当前代码分析](docs/当前代码分析与一期工程对比.md) | 当前四包进展、17FR/35AC、历史与本次测试结果及一期差距 |
| [编辑器内面板交互草案](docs/项目文档/一期/设计文档/02-编辑器内面板交互设计草案.md) | 0.3设计讨论与参考图，尚未在产品/真实Trae实现 |
| [本轮修改日志](docs/修改日志/袁/2026-10-03-一期源码深查与新增问题.md) | 文档逐项更新、复核证据及未验证边界 |
| [更新记录](CHANGELOG.md) | 精简版本与阶段变更 |

## 已知限制

- 同基线深查确认无锁重试/历史丢失、跨项目混入、模型重定向外发、公开事务无法结束、保存修订错配、采集提前完整和Windows Job绑定失败，见A-11—14、B-11、C-08—09。

- 尚无可供用户完成“建项目 → 发计划 → 执行 → 留证 → 判定 → 报告”的端到端产品入口；模型、C/D、可信人工会话及宿主业务桥未接通。
- 完整性检查把模板正文摘要误认成对象引用，正常草稿保存后会阻塞核心重启；缺历史源码Blob又可能误判完整，符号链接恢复目标仍未拒绝。
- 永久JSONL引用未纳入回收判断；有限查询仍整读JSON，核心排他锁只覆盖短事务，父进程退出与活动执行保活未协作。
- C的完成下游失效、执行前持久授权/启动意图及输出流耐久游标仍缺；模型响应草稿保存前尚未过滤凭据正文，同出站请求重复调用已复现（B-10）。
- 生产有效事实聚合、问题真实新回归、报告持久查询/安全导出及真实Trae生命周期仍未验收；面板静态测试与该基线已记录的CI各有一项失败；本次未重新查询远端。


---

项目为内部工程，许可证以仓库和组织规定为准。
