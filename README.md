# AI 辅助测试插件（AI Test Component）

面向 Windows 11 x64、Python 3.13 与 Trae 的本地测试插件，当前产品版本 0.4.7。工程同时包含可运行组件与合同文档，完整产品闭环仍在开发。**当前能力、门禁、验收与未验证范围统一见[项目现状](docs/现状.md)。**

[![CI](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/ymyymy27/ai-test-component/actions/workflows/ci.yml)

## 实现结构

入口 → 接口适配 → 应用用例 → 纯领域规则；应用经 `application/ports.py` 使用外部端口，`bootstrap.py` 统一注入。用户数据位于独立本地工作空间，生命周期排他写锁和不可变提交保存记录/证据，执行/模型调用在短事务外。

项目/规则/计划与命令执行组件已存在；C-15/C-16 原缺陷已修，问题/报告/复核/导出、补证/重试/退回、备份/迁移与复用选择已接入。上述原代码反例中的维护、选择修订、关闭、阻塞集合、有效确认与导出回读已修；复用动态资格缺真实材料时继续拒绝；CLI/MCP 接同核心，Trae/面板接只读桥和核心原生确认的受控入口。已接按引用准备、结构化 HTTP 核验与分步操作；完整业务 UI、其他核验适配和真实环境验收仍有剩余，具体按四包检查表实施。

```text
src/aitest/           domain / application / infrastructure / contracts / interfaces
integrations/trae/    Trae 候选薄扩展
tests/               单元 / 合同 / 架构 / 恢复 / 集成 / 验收登记
docs/项目文档/        需求、功能、架构、设计主责合同
docs/一期工程整改/    当前分析、计划、证据索引与交接
docs/validation/     按准确版本保存的实测与反例
```

0.4.6 的当前操作入口见[实际进程来源与完整报告链](docs/一期工程整改/04-交接/0.4.6-实际进程来源与完整报告链.md)：从准确计划重新准备并登记运行，再执行、核验、正式提测和导出；来源策略支持范围与旧运行升级边界均在指南中说明。

历史 0.4.1 预览支持配置、连接、新建项目与高级受控命令，安装步骤见[操作说明](docs/一期工程整改/04-交接/0.4.1-受控入口预览安装与操作.md)。完整计划/执行向导与真实 Trae 验收仍待完成。

0.4.2 补上宿主 Python 环境登记与项目创建表单，见[项目与环境操作说明](docs/一期工程整改/04-交接/0.4.2-项目创建与Python环境登记.md)。登记不代替实际环境观察与执行授权。

0.4.3 操作主线见[计划→执行→核验→报告指南](docs/一期工程整改/04-交接/0.4.3-计划执行核验报告操作.md)，真实 Trae 验收仍未完成。

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
| [项目现状](docs/现状.md) | 唯一现行状态、门禁、验收和版本边界 |
| [项目文档总览](docs/项目文档/README.md)、[总体架构](docs/项目文档/总体架构.md)、[阅读索引](docs/项目文档/阅读索引.md) | 主责字段、时序与 FR/AC |
| [四包检查 A](docs/一期工程检查-A包.md) / [B](docs/一期工程检查-B包.md) / [C](docs/一期工程检查-C包.md) / [D](docs/一期工程检查-D包.md) | 稳定问题编号、当前实现和剩余关闭条件 |
| [整改索引](docs/一期工程整改/README.md)、[当前交接](docs/一期工程整改/04-交接/交接说明-2026-10-12.md) | 接续实施与历史追溯 |
| [本轮复核证据](docs/validation/p1-boundary-fixes-20261013/README.md) | 准确版本、实际测试、边界修复、真实材料限制与文档恢复依据 |
| [接口台账](docs/接口对接/README.md) | 现行跨包合同及对拍状态 |
| [验收台账](tests/acceptance/p1/status.json) | 按需求原场景逐项登记，真实证据才能 verified |
| [真实 Trae 入口准备](docs/validation/p1-trae-entry-20261011/README.md) | 只读入口流程，不替代完整业务 AC |
| [变更记录](CHANGELOG.md) | 历史版本与阶段摘要 |

项目为内部工程，许可证以仓库和组织规定为准。

0.4.4 鉴权与动态对象核验配置见[操作指南](docs/一期工程整改/04-交接/0.4.4-鉴权与动态对象核验.md)，准确自动化范围见[本批证据](docs/validation/p1-query-auth-20261015/README.md)。

0.4.5 新增纯函数独立预期比对、报告关联提测和运行控制表单，见[操作补充](docs/一期工程整改/04-交接/0.4.5-纯函数与报告关联提测.md)。

0.4.6 增加实际 Python 文件进程来源采集与永久证据，见[操作说明](docs/一期工程整改/04-交接/0.4.6-实际进程来源与完整报告链.md)。
