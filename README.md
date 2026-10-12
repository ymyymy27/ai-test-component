# AI 辅助测试插件（AI Test Component）

面向 Windows 11 x64、Python 3.13 与 Trae 的本地测试插件，当前产品版本 0.4.0。工程同时包含可运行组件与合同文档，完整产品闭环仍在开发。**当前能力、门禁、验收与未验证范围统一见[项目现状](docs/现状.md)。**

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)

## 实现结构

入口 → 接口适配 → 应用用例 → 纯领域规则；应用经 `application/ports.py` 使用外部端口，`bootstrap.py` 统一注入。用户数据位于独立本地工作空间，生命周期排他写锁和不可变提交保存记录/证据，执行/模型调用在短事务外。

项目/规则/计划与默认命令执行、授权、采集、控制已接通；CLI/MCP 接同核心，Trae/面板接只读桥。持久报告/问题/安全导出、复用选择保存、完整业务 UI 和真实环境验收仍有剩余，具体按四包检查表实施。

```text
src/aitest/           domain / application / infrastructure / contracts / interfaces
integrations/trae/    Trae 候选薄扩展
tests/               单元 / 合同 / 架构 / 恢复 / 集成 / 验收登记
docs/项目文档/        需求、功能、架构、设计主责合同
docs/一期工程整改/    当前分析、计划、证据索引与交接
docs/validation/     按准确版本保存的实测与反例
```

## 开发安装与验证

标准安装命令是 `uv sync --extra dev --locked --python 3.13`。**当前锁文件与工程的 Python 范围不一致，干净安装会退出 1（A-19）**；本轮复核的 frozen 环境不能写成此门禁通过。依赖已正确安装后，可按工程配置运行：

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
| [项目现状](docs/现状.md) | 唯一现行状态、门禁、验收和版本边界 |
| [项目文档总览](docs/项目文档/README.md)、[总体架构](docs/项目文档/总体架构.md)、[阅读索引](docs/项目文档/阅读索引.md) | 主责字段、时序与 FR/AC |
| [四包检查 A](docs/一期工程检查-A包.md) / [B](docs/一期工程检查-B包.md) / [C](docs/一期工程检查-C包.md) / [D](docs/一期工程检查-D包.md) | 稳定问题编号、当前实现和剩余关闭条件 |
| [整改索引](docs/一期工程整改/README.md)、[当前交接](docs/一期工程整改/04-交接/交接说明-2026-10-12.md) | 接续实施与历史追溯 |
| [本轮复核证据](docs/validation/p1-code-review-20261012/README.md) | 准确版本、实际测试、A-19/D-10 新反例和清理原文哈希 |
| [接口台账](docs/接口对接/README.md) | 现行跨包合同及对拍状态 |
| [验收台账](tests/acceptance/p1/status.json) | 按需求原场景逐项登记，真实证据才能 verified |
| [真实 Trae 入口准备](docs/validation/p1-trae-entry-20261011/README.md) | 只读入口流程，不替代完整业务 AC |
| [变更记录](CHANGELOG.md) | 历史版本与阶段摘要 |

项目为内部工程，许可证以仓库和组织规定为准。
