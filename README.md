# AI 辅助测试插件（AI Test Component）

面向 Windows 11 x64、Python 3.11+ 与 Trae 的轻量本地测试插件：Python CLI + Trae 薄扩展 + 共享面板。一次运行完成项目/计划、L1/L2/L3 执行、证据、判定与报告、问题闭环和备份恢复。

仓库同时包含可运行产品代码与工程文档。**验收状态只看 `tests/acceptance/p{1,2,3}/status.json`**；产品合同见[项目文档](docs/项目文档/README.md)，重构执行基线见[修改计划](docs/修改日志/ymy/2026-11-16-轻量Trae插件重构计划.md)。组件可构建、测试可运行，都不等于真实宿主或验收通过。

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)

## 版本

| 项 | 当前值 |
| --- | --- |
| 产品版本 | **0.5.0**（`pyproject.toml`、`panel/package.json`、`integrations/trae/package.json` 一致） |
| 代码规模 | **40** 个 Python 模块，约 **11,784** 行；**267** 个 pytest 节点；面板 vitest **50**、扩展 node test **48** |
| 运行环境 | Windows 11 x64；Python 3.11—3.13（开发与 CI 使用 3.13） |
| 宿主 | Trae（VS Code 兼容扩展）；面板产物可在浏览器中调试 |
| 版本历史 | [CHANGELOG](CHANGELOG.md) |

## 安装与快速开始

```powershell
uv sync --extra dev --locked

.venv/Scripts/python.exe -m aitest.cli --version
.venv/Scripts/python.exe -m aitest.cli doctor --workspace "$env:APPDATA/aitest/workspaces/default"
```

CLI 约定：`python -m aitest.cli <command> --workspace <dir> --input <json>`，也接受 stdin JSON；stdout 只输出一行 JSON，日志走 stderr。当前注册 **54** 个命令。

面板与 Trae 扩展构建（需要 Node/npm）：

```powershell
npm --prefix panel ci
npm --prefix integrations/trae ci
npm --prefix integrations/trae run package
```

- `integrations/trae run build` 会先构建 `panel/`，再把产物复制到 `integrations/trae/media/panel`；不要手改生成物。
- VSIX 输出到 `integrations/trae/dist/aitest-trae.vsix`。
- 扩展命令与配置项见 `integrations/trae/package.json`；首次向导、日常操作与排障见[安装与操作说明](docs/项目文档/一期/06-发布运维/03-安装与操作说明.md)。

## 仓库结构

```text
src/aitest/           Python 包：cli/worker、commands、core（判定）、storage、runners、adapters、resources/templates
integrations/trae/    Trae 薄扩展（调用 CLI 的 JSON 协议）与 Node 测试
panel/                共享面板源码（Vue 3 + Vite）与浏览器测试
tests/unit/           行为、合同与门禁测试
tests/acceptance/     p1/p2/p3 验收登记与证据
scripts/              版本、文档事实与制品校验脚本
docs/
├── 项目文档/          产品合同：一期为当前范围，二期/三期后置占位
└── 修改日志/ymy/      轻量 Trae 插件重构计划（执行基线）
```

## 文档导航

| 入口 | 用途 |
| --- | --- |
| [项目文档](docs/项目文档/README.md) | 一期产品合同、架构、设计与测试验收 |
| [重构计划](docs/修改日志/ymy/2026-11-16-轻量Trae插件重构计划.md) | 轻量重构的执行基线、人工动作边界与工作包 |
| [项目简报](docs/项目文档/一期/01-项目/01-项目简报.md) | 背景、目标用户、范围与验收口径 |
| [需求规格](docs/项目文档/一期/02-需求/02-需求规格.md) | 18 FR / 27 AC、范围与边界 |
| [功能清单](docs/项目文档/一期/02-需求/03-功能清单.md) | 判定、覆盖与统计的唯一口径 |
| [CLI 与数据契约](docs/项目文档/一期/03-设计/03-CLI与数据契约.md) | 命令、字段、枚举与错误码 |
| [验收登记](tests/acceptance/p1/status.json) | 按需求场景逐项登记；`verified` 需真实环境验证与证据路径 |

## 验证命令

```powershell
.venv/Scripts/python.exe scripts/check_versions.py
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe scripts/check_docs_facts.py --full
.venv/Scripts/python.exe -m pytest

npm --prefix panel test
npm --prefix panel run build
npm --prefix integrations/trae test
```

- `check_docs_facts.py` 把 `AGENTS.md`、README 与代码事实对账；`--full` 另跑 mypy。
- 不手工编辑面板产物、wheel 或 VSIX；构建后用 `npm --prefix integrations/trae run build` 重新生成。
- 全量 pytest 期间不要并发其它重测试；分块或历史结果不能相加冒充全量。
- 两个 Node 测试必须在各自包目录内运行（`npm --prefix`）。

项目为内部工程，许可证以仓库与组织规定为准。
