# AI 辅助测试插件（AI Test Component）

面向 Windows 11 x64、Python 3.13 与 Trae 的本地测试插件，当前产品版本 0.4.10。工程同时包含可运行组件与合同文档，完整产品闭环仍在开发。**当前能力、问题与未验证范围见[现状与问题汇总](docs/现状与问题汇总.md)，数字与门禁见[项目现状](docs/现状.md)，全部工程文档入口见[文档总览](docs/README.md)。**

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)

## 实现结构

入口 → 接口适配 → 应用用例 → 纯领域规则；应用经 `application/ports.py` 使用外部端口，`bootstrap.py` 统一注入。用户数据位于独立本地工作空间，生命周期排他写锁和不可变提交保存记录/证据，执行/模型调用在短事务外。

项目/规则/计划与命令执行组件已存在；C-15/C-16 原缺陷已修，问题/报告/复核/导出、补证/重试/退回、备份/迁移与复用选择已接入。上述原代码反例中的维护、选择修订、关闭、阻塞集合、有效确认与导出回读已修；复用动态资格缺真实材料时继续拒绝；CLI/MCP 接同核心，Trae/面板接只读桥和核心原生确认的受控入口。已接按引用准备、结构化 HTTP 核验与分步操作；完整业务 UI、其他核验适配和真实环境验收仍有剩余，具体按[现状与问题汇总](docs/现状与问题汇总.md)的问题表实施。

```text
src/aitest/           domain / application / infrastructure / contracts / interfaces
integrations/trae/    Trae 候选薄扩展
tests/                单元 / 合同 / 架构 / 恢复 / 集成 / 验收登记
docs/README.md        工程文档总览与索引
docs/项目文档/        需求、功能、架构、设计主责合同
docs/一期工程整改/    现行交接与操作指南；历史整改批次在归档
docs/接口对接/        跨包合同、评审、裁定与总台账
docs/修改日志/        现行工作记录（2026-11-03 起）
docs/validation/      现行验证批次入口
docs/归档/            历史修改日志、验证证据、作者文档与整改批次
```

## 版本与操作入口

当前源码 **0.4.10**。现行能力、问题与未验证边界见[现状与问题汇总](docs/现状与问题汇总.md)；数字与契约版本见[项目现状](docs/现状.md)；完整版本历史见[CHANGELOG](CHANGELOG.md)。

| 版本 | 操作/配置入口 |
| --- | --- |
| 0.4.9—0.4.10 | [面板引导实测](docs/归档/validation/p1-panel-guidance-20261016/README.md)、[0.4.10 向导与插件实测](docs/归档/validation/p1-onboarding-live-20261016/README.md) |
| 0.4.8 | [Trae 实测与连接修复](docs/一期工程整改/04-交接/0.4.8-Trae实测与连接修复.md) |
| 0.4.6—0.4.7 | [实际进程来源与完整报告链](docs/归档/一期工程整改/04-交接-旧版本/0.4.6-实际进程来源与完整报告链.md)、[Trae 与模型配置](docs/归档/一期工程整改/04-交接-旧版本/0.4.7-Trae与模型配置.md) |
| 0.4.1—0.4.5 | 历史操作说明见[04-交接](docs/一期工程整改/04-交接)，仅对应各自版本 |

0.4.1—0.4.6 的安装与向导步骤只证明当时版本；真实 Trae、其他宿主与完整 AC 仍按[项目现状](docs/现状.md)登记，不能把历史操作说明当作当前验收。

## 开发安装与验证

标准安装命令是 `uv sync --extra dev --locked --python 3.13`。A-19 锁声明范围不一致已修，历史干净锁定安装证据与本轮 lock 检查、Python 运行边界分别见[项目现状](docs/现状.md)。依赖安装后按工程配置运行：

```powershell
.venv/Scripts/python.exe scripts/check_versions.py
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe scripts/generate_schemas.py
.venv/Scripts/python.exe scripts/check_docs_facts.py --full
.venv/Scripts/python.exe -m pytest -p no:cacheprovider -ra tests
```

生成 Schema 后须检查 Git diff；不手工编辑生成物。全量测试期间不并发其他重测试，保留退出码、原输出与跳过原因，不用分块结果相加替代全量。临时数据可放 `.runtime/tmp` 并设置 TEMP/TMP。

面板/Trae 需要 Node/npm，按现有 lock 安装并构建：

```powershell
npm --prefix src/aitest/resources/panel ci
npm --prefix src/aitest/resources/panel run build
npm --prefix src/aitest/resources/panel test
npm --prefix integrations/trae ci
npm --prefix integrations/trae run package
```

VSIX 在 `integrations/trae/dist/aitest-trae.vsix`，构建物不跟踪；浏览器/打包通过不代表真实 Trae 行为验收。制品用 `scripts/verify_artifacts.py` / `scripts/smoke_wheel.py` 验证内容、共享面板字节与隔离安装，原证据不得用于新构建。

## 命令行入口

```powershell
.venv/Scripts/python.exe -m aitest.interfaces.tools.cli templates
.venv/Scripts/python.exe -m aitest.interfaces.tools.cli doctor
.venv/Scripts/python.exe -m aitest.interfaces.tools.cli ensure-core --workspace "C:\本地用户数据\aitest"
.venv/Scripts/python.exe -m aitest.interfaces.tools.cli dispatch --workspace "C:\本地用户数据\aitest"
.venv/Scripts/python.exe -m aitest.interfaces.tools.cli mcp-relay --workspace "C:\本地用户数据\aitest" --project <项目ID> --binding <绑定ID>
```

离线 doctor 如实返回 NOT_READY；ensure-core 发现/启动既有工作空间的唯一核心，缺路径拒绝且不创建目录；dispatch 从有界 stdin Command JSON 派发，保留 request/intent/准确修订；MCP 冻结项目绑定、只读查询与允许的普通保存，不自行签人工确认。动作可用性以实际装配回执和具体依赖为准。

## 文档导航

| 入口 | 用途 |
| --- | --- |
| [文档总览](docs/README.md) | 全部工程文档的分类、维护边界、阅读顺序与新增约定 |
| [现状与问题汇总](docs/现状与问题汇总.md) | 单页总览：当前状态、27 项开放问题、验收边界与交付主线 |
| [项目现状](docs/现状.md) | 唯一现行状态、门禁、验收和版本边界 |
| [项目文档总览](docs/项目文档/README.md)、[总体架构](docs/项目文档/总体架构.md)、[阅读索引](docs/项目文档/阅读索引.md) | 主责字段、时序与 FR/AC |
| [现状与问题汇总](docs/现状与问题汇总.md) | 唯一现行问题清单（A/B/C/D 共 27 项）与状态总览；历史分包检查在[归档](docs/归档/一期工程整改/历史分包检查/) |
| [整改索引](docs/一期工程整改/README.md)、[当前交接](docs/一期工程整改/04-交接/交接说明-2026-10-12.md) | 接续实施与历史追溯 |
| [接口台账](docs/接口对接/README.md) | 现行跨包合同及对拍状态 |
| [修改日志](docs/修改日志/README.md) | 2026-11-03 起的现行工作记录；历史在[归档](docs/归档/修改日志/README.md) |
| [作者工作文档](docs/归档/作者文档/文档-LU/README.md) | A/B/C/D 包的设计、计划、自检与待办（历史参考） |
| [学习讲义](docs/学习讲义/README.md) | 架构入门材料，不作为验收依据 |
| [验证档案](docs/validation/README.md) | 新批次入口；历史批次在[归档验证档案](docs/归档/validation/README.md) |
| [验收台账](tests/acceptance/p1/status.json) | 按需求原场景逐项登记，真实证据才能 verified |
| [变更记录](CHANGELOG.md) | 历史版本与阶段摘要 |

项目为内部工程，许可证以仓库和组织规定为准。
