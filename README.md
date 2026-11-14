# AI 辅助测试插件（AI Test Component）

面向 Windows 11 x64、Python 3.11+ 与 Trae 的本地测试插件：Python 核心 + Trae 薄扩展 + 共享面板，在一个本地工作空间内完成项目/计划、L1/L2/L3 执行、证据与核验、判定与报告、问题闭环和受控恢复。

仓库同时包含可运行产品代码与工程文档。**当前状态、开放问题、规模数字与未验证范围只看[项目现状](docs/现状.md)**；文档分类与阅读顺序见[文档总览](docs/README.md)。组件可构建、测试可运行，都不等于真实宿主或验收通过。

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)

## 版本

| 项 | 当前值 |
| --- | --- |
| 产品版本 | **0.4.10**（`pyproject.toml`、两个 `package.json` 一致） |
| 运行环境 | Windows 11 x64；Python 3.11—3.13（开发与 CI 使用 3.13） |
| 宿主 | Trae（VS Code 兼容扩展）；面板产物可在浏览器中调试 |
| 版本历史 | [CHANGELOG](CHANGELOG.md) |

## 安装与快速开始

```powershell
uv sync --extra dev --locked

.venv/Scripts/python.exe -m aitest.interfaces.tools.cli doctor
.venv/Scripts/python.exe -m aitest.interfaces.tools.cli ensure-core --workspace "C:\本地用户数据\aitest"
```

面板与 Trae 扩展构建（需要 Node/npm）：

```powershell
npm --prefix src/aitest/resources/panel ci
npm --prefix src/aitest/resources/panel run build

npm --prefix integrations/trae ci
npm --prefix integrations/trae run package
```

- VSIX 输出到 `integrations/trae/dist/aitest-trae.vsix`，构建物不跟踪。
- 扩展 `package.json` 声明 `aitest.openPanel`、`aitest.configureEntry` 两个命令；`src/extension.ts` 另注册 `aitest.openWorkbench`、`aitest.openReport`（未在 `contributes.commands` 声明，命令面板不可直接调用）。
- 登记核心与扩展、首次向导、日常操作与排障见[安装与操作说明](docs/项目文档/一期/06-发布运维/03-安装与操作说明.md)；运行拓扑与故障分类见[运维手册](docs/项目文档/一期/06-发布运维/04-运维手册.md)。

## 仓库结构

```text
src/aitest/           核心：domain / application / infrastructure / contracts / interfaces / resources（含共享面板源码与 Playwright 用例）
integrations/trae/    Trae 薄扩展（候选实现，含 Node 测试）
tests/                单元 / 合同 / 架构 / 恢复 / 集成 / 验收登记（tests/acceptance/p1|p2|p3/status.json）
scripts/              版本、Schema、文档事实核对与产物校验脚本
docs/
├── README.md              文档总览：分类、阅读顺序、维护规则
├── 现状.md                唯一状态与问题清单
├── 00-项目简报.md
├── 01-准备阶段/           01-业务方案、02-需求规格、03-功能清单、04-产品方案与界面设计、05-技术评估、06-测试计划、07-交付计划
├── 02-开发阶段/           01-架构总览 … 09-遗留问题与风险
├── 03-发布阶段/           01-发布方案、02-备份与恢复预案、03-安装与操作说明、04-验收台账
├── 04-运营阶段/           01-运维手册、02-效果检测与数据采集
├── 90-未来期次/           二期、三期的需求/功能/架构/设计合同（不在当前交付范围）
├── 接口对接/              跨包合同、裁定与总台账
├── 修改日志/              现行工作记录
├── validation/            现行验证批次
├── 学习讲义/
└── 归档/                  历史材料；文档重构前的旧结构在 归档/文档重构-2026-11-14/
```

## 文档导航

| 入口 | 用途 |
| --- | --- |
| [文档总览](docs/README.md) | 全部文档分类、事实优先级、阅读顺序与新增约定 |
| [项目现状](docs/现状.md) | 唯一状态与开放问题清单、验收边界、交付主线 |
| [项目简报](docs/项目文档/一期/01-项目/01-项目简报.md) | 背景、目标用户、范围、里程碑与验收口径 |
| [准备阶段](docs/项目文档/一期/README.md) | [需求规格](docs/项目文档/一期/02-需求/02-需求规格.md)（FR/AC）、[功能清单](docs/项目文档/一期/02-需求/03-功能清单.md)（判定与统计唯一口径）、界面设计、测试与交付计划 |
| [开发阶段](docs/项目文档/一期/README.md) | 分层架构、项目/计划、执行与证据、报告与缺陷、存储与恢复、Trae 接入、面板、开发规则、风险 |
| [发布阶段](docs/项目文档/一期/README.md) | 发布门禁、备份与恢复预案、安装与操作、验收台账 |
| [运营阶段](docs/项目文档/一期/README.md) | 运维手册、效果检测与数据采集 |
| [未来期次](docs/) | 二期/三期合同，仅作追溯，不在当前交付范围 |
| [接口对接](docs/接口对接/README.md) | 六份跨包合同版本、裁定与本轮状态 |
| [修改日志](docs/修改日志/README.md) / [验证档案](docs/validation/README.md) | 现行工作记录与验证批次 |
| [归档](docs/归档/README.md) | 历史材料，保留原结论、不改判 |
| [验收登记](tests/acceptance/p1/status.json) | 按需求原场景逐项登记；`verified` 需真实环境验证与证据路径 |

## 验证命令

```powershell
.venv/Scripts/python.exe scripts/check_versions.py
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe scripts/generate_schemas.py
.venv/Scripts/python.exe scripts/check_docs_facts.py --full
.venv/Scripts/python.exe -m pytest -p no:cacheprovider --import-mode=importlib -n 8 --dist loadfile -ra tests

npm --prefix src/aitest/resources/panel test
npm --prefix integrations/trae test
```

- `check_docs_facts.py` 把现状页、`AGENTS.md` 与本文档的数字与代码对账；`--full` 另跑 mypy 并真实装配核心取可派发动作数。
- 生成 Schema 后检查 `git diff`，不手工编辑生成物。
- Python 全量在本机耗时数小时；跑全量期间不要并发其它重测试，分块或历史结果不能相加冒充全量。
- 两个 Node 测试必须在各自包目录内运行（`npm --prefix`）；直接在仓库根跑扩展用例会因 esbuild 相对路径解析失败。
- 制品内容、共享面板字节与隔离安装用 `scripts/verify_artifacts.py`、`scripts/smoke_wheel.py` 校验，历史证据不能用于新构建。

项目为内部工程，许可证以仓库与组织规定为准。
