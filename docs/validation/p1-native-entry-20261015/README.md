# 0.4.1 受控入口预览验证

起点 `fix@6ce75901aa875767193f7719a6de8fb99b77cfbb`，日期 2026-10-15。本批不是一期完整交付，也不是完整 Python 全量通过。

## 已实现

- 核心独立 Windows 确认窗口读取准确输入、保存依据正文和摘要；取消、关闭、回车、超时拒绝，超大材料拒绝而不截断。请求方不能提交人工确认事实。窗口会话结束撤销未消费挑战。
- CLI 可选 native-confirmation/1 控制入口，先协商能力；旧核心不降级伪签。结束会话失败返回结果待核实，不把已经提交的业务误报成可安全重试。
- Trae 配置命令、受控转发、上下文/身份/修订校验；面板支持新建项目模板及高级参数输入，取消保留稳定意图。创建序号由实际事务分配。
- 修复浏览器非安全上下文缺少 randomUUID 的按钮失败，改用 getRandomValues。修复 Windows 虚拟环境找不到基础解释器 Tcl/Tk 的问题。

## 检查与证据

锁定回归环境 `.runtime/completion-clean313`：Windows、Python 3.13.0、Pydantic 2.13.5。

最终受影响路径命令：

```powershell
python -m pytest tests/unit/test_native_confirmation.py tests/unit/test_a_a02_core_dispatch.py tests/unit/test_cli_core_forwarding.py tests/unit/test_approval_channel_finalization.py tests/unit/test_project_persistence.py tests/unit/test_project_persistence_real_store.py tests/unit/test_record_write_intents.py -q --disable-warnings --junitxml=docs/validation/p1-native-entry-20261015/final-python.xml
```

最终 **122 passed，0 failed / error / skipped**，见 [JUnit](final-python.xml) 与 [输出](final-python.log)。初轮 [14 项组件](native-component.xml) 和 [101 项既有路径](existing-paths.xml) 与最终范围重叠，不相加。

面板浏览器 [15 项结果](browser.xml) 与 [输出](browser.log)；首次 randomUUID 失败保留 [上下文](browser-first-failure.md) 和截图，修复后相同范围通过。宿主桥与上下文检查独立运行，不等于真实 Trae。

最终 [逐项命令/退出码](release-commands.json)记录 ruff、mypy（251 文件）、版本/锁、Schema、宿主检查（23 项）、面板/VSIX/wheel 构建、制品核对和隔离安装，全部退出 0。隔离安装使用 Python 3.13.0 / Pydantic 2.14.1，区别于锁定回归环境；仅证明包内模板、未连接 doctor 和缺绑定 MCP 的准确行为。首次无隔离构建缺 hatchling，改用缓存 uv 隔离构建后成功。

当前 [事实](facts.json)为 237 个 Python 源模块、355 个测试文件、5472 项收集，仅收集不执行全量；[源码指纹](source-inventory.json)独立冻结。[事实对账](facts-final.log)无差异，[文档检查](document-checks.json)核对 101 份 Markdown、978 个本地链接、13 个锚点且无错误；[整改目录逐项记录](document-audit.json)保留有效历史，不删除原失败。初次发现的两处 CHANGELOG 旧链接已修。

[Windows 窗口探针](native-dialog-probe.json)使用实际 Tk/Tcl 8.6.14 创建控件并自动取消，结果 accepted=false；不是人工点击或真实宿主验收。原始 Tk() 在此虚拟环境报找不到 init.tcl，基础安装实际含 F:/python/tcl/tcl8.6 与 tk8.6；修复只从 sys.base_prefix 查找匹配组件，缺失时拒绝，结束恢复环境变量。

## 未验证与下一主线

真实 Trae 安装/人工点击/端到端执行恢复尚未验证，验收登记不改。完整计划和执行向导、默认可信环境载体登记、独立业务核验仍欠；高级 JSON 入口不等于完整可用产品。当前无全量测试在运行，旧全量中断保持原记录，不累计部分通过数。

安装和实际操作见[指南](../../一期工程整改/04-交接/0.4.1-受控入口预览安装与操作.md)。项目合同与验收状态保持原文；接口 CORE 1.86、AB 1.46、BD 0.8 仍 reviewing / partial / not_run，没有代填双方签署。
