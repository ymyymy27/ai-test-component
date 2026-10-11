# 连接事实与协议安全阶段历史证据（2026-10-04）

完整1722 passed/2 skipped（177.55s），根因专项367 passed（87.04s），连接/能力/安全专项133 passed（9.15s），Ruff/mypy132文件通过。wheel/sdist重新构建、修复模块/Schema/panel字节对拍和隔离wheel冒烟通过。此历史目录冻结后不再更新。

- [精确命令、结果与输出摘要](validation-results.json)
- [27项状态与263处源码/测试引用](issue-status.json)
- [引用的61份源码/测试原字节](referenced-source-and-tests.zip)
- [源码、测试、配置与锁文件](validation-source.zip)、[逐文件/压缩包摘要](source-manifest.json)
- [完整回归](pytest-full.log)、[根因专项](root-regressions.log)、[连接/安全专项](connection-fact-regressions.log)
- [修正命令夹具后的18项旧代码反例](connection-fact-before-fix.log)、[反例测试原字节](connection-fact-before-test.py)、[旧源码/测试摘要](connection-fact-before-source.json)
- [原四项命令夹具错误输出](connection-fact-before-fixture-correction.log)，不计作已修缺陷

旧代码原字节来自[1687阶段](../current-attempt-transition-1687/README.md)。本阶段源码包为工程源文件覆盖层，回放时保留同基线仓库的项目文档/运行依赖；逐节点命令在证据清单中，不宣称压缩包单独代表真实环境。

自动连接条件由同端点最新保存事实恢复，人工暂停保留；类型/Schema/重复字段/尾行不明时不报就绪。诊断先过滤再转义，正常/异常出口使用已配置凭据边界，过滤失败不退回原文。连接故障只阻塞其依赖，本地动作继续。

仍5项整代码问题关闭、22项ABC剩余；可信人工授权、未使用授权/整用例复用持久链、初始运行/默认入口、实际输入来源和真实35项AC未闭合。生成Schema/面板/VSIX未改变，沿用此前相应生成/构建日志，最新Python制品另行重新验证。
