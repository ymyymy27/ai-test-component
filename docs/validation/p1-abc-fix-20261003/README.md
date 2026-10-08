# A/B/C根因整改验证

基线develop `7dbc5a45aa1666652c3dcdc2d0b0604066bc6498`，验证时工作分支`文档更新-袁`，本轮交付分支`fix`；工作始于2026-10-03，执行记录为2026-10-04。原缺陷取证目录保持原字节，本目录保存修复后的独立输出。

| 检查 | 实际结果 | 输出 |
| --- | --- | --- |
| 全量Python | 1967 passed、2 skipped，333.34s | [完整输出](pytest-full.log) |
| 提交/Windows/迁移专项 | 97 passed，98.37s（解析修复前）；最终新增2场景2.42s | [专项输出](root-regressions.log) |
| Ruff/mypy | 通过，138源码文件 | [Ruff](ruff.log)、[mypy](mypy.log) |
| 生成物 | 7份Schema、4份功能夹具再生后字节不变 | [输出/摘要](generated.log) |
| 面板/制品 | 当前wheel/sdist通过；panel/6文件VSIX沿用未改来源的历史通过日志 | [panel](panel-build.log)、[Python构建](python-build.log)、[VSIX](trae-package.log) |
| 制品对拍 | 修复模块、全部Schema、共享panel与源码/VSIX字节一致 | [输出](artifacts.log)、[制品摘要](artifacts.json) |
| 真实DeepSeek组件 | 已提交草稿；原意图同进程/新核心回读一致且没有外调，117文件凭据检查通过 | [实测JSON](deepseek-live.json)、[验证脚本](../../../scripts/validate_deepseek.py) |
| 隔离wheel冒烟 | 6模板；doctor NOT_READY/退出2；MCP CAPABILITY_UNAVAILABLE/退出2 | [输出](wheel-smoke.log) |

精确命令、环境、输出SHA-256及未验证范围见[执行JSON](validation-results.json)；27条状态、源码/测试行号及文件SHA-256见[逐项JSON](issue-status.json)和[人读清单](../../一期工程整改/03-修复证据/ABC包问题修复证据清单-2026-10-03.md)。每个测试节点可按清单中的pytest命令独立运行。

证据目录通过`.gitattributes`保留原始字节。逐项清单的`sha256`对应验证时工作文件和冻结ZIP中的原始字节；Git会按仓库配置规范化源码换行。[提交源码字节清单](git-source-bytes.json)同时记录80份引用文件的原始SHA-256、Git blob SHA-256及对象号，核对二者仅有CRLF/LF转换。跨系统检出时用Git blob摘要核对提交，用冻结ZIP核对原始验证材料。

## 证据边界

文件底座、临时Windows进程/Job、受控HTTP transport、合成凭据和故障注入均真实执行各自组件代码；受控transport不能证明真实供应方验收。真实Junction祖先拒绝通过，两项物理symlink测试因权限跳过。新模块已进入wheel，不以开发环境可导入代替打包检查。

面板行为未修改，前轮Playwright计划按钮定位失败保留在原复核证据，未据build通过声明产品流程通过。隔离wheel的doctor/MCP明确不可用结果属于当前真实限制。

真实DeepSeek草稿生成及持久回读已通过组件实测，未覆盖真实供应方故障/恢复、Trae或业务端到端验收。TraeCode CN现装3.3.89；官方日志列出3.3.93—96系列，Windows实际最新包与更新仍待核实。界面工具因无法可靠识别浏览器URL停止，未继续该窗口操作。

一期真实AC仍0verified/7not_verified/1blocked/27untested，35证据路径全空；真实Trae、供应方/业务独立核验、物理掉电均未验收。当前关闭5条代码问题，A/B/C仍22条具体剩余工作；D7条本轮未整改。上述验证发生在提交前，未查询新远端CI。

## 当前Windows发布阶段

本阶段[冻结归档](history/windows-publication-1967/README.md)保留430处源码/测试引用、4处剩余边界引用、80份引用文件及332份工程覆盖层；逐文件、ZIP和日志摘要已独立核对，归档不覆盖。

39个新增场景由最终全量覆盖；97专项在解析修复前运行，最后2个损坏JSON场景修复后通过。七字段current、0006迁移及实际Windows API故障证据保留。其他27问题的现行节点由1965全量覆盖；未重跑历史573命令。[阶段证据](WINDOWS-PUBLICATION.md)、[原型历史](history/publication-prototype-1928/README.md)。

完整主责CommitManifest及幂等结果引用/文件摘要合同、证据对象与business_change_index完整闭包、实际核心实例来源、普通启动有界恢复、未知发布人工恢复、目标设备名称元数据耐久/物理掉电、产品历史/固定排序、默认业务入口与真实AC仍需落实。 真实AC仍0/35，未以组件通过关闭A-05。
