# 有限选择器阶段历史证据（2026-10-04）

完整1799 passed/2 skipped（249.83s）、444根因专项（183.53s）、136查询专项（94.66s）、Ruff/mypy133文件通过。此目录冻结后不再更新。

- [精确命令、结果与输出摘要](validation-results.json)、[27项状态/297处引用](issue-status.json)
- [引用的66份源码/测试原字节](referenced-source-and-tests.zip)
- [源码/测试/配置与锁文件覆盖层](validation-source.zip)、[逐文件/压缩包摘要](source-manifest.json)
- [全量](pytest-full.log)、[根因专项](root-regressions.log)、[查询专项](query-route-regressions.log)
- [17类旧代码反例](query-route-before-fix.log)、[测试原字节](query-route-before-test.py)、[原源码摘要](query-route-before-source.json)
- [两项合同诊断的前后验证](query-route-verification.json)
- [尚未修复的启动指针/重复创建诊断](core-start-before-probe.json)

原查询实现字节在[1750阶段](../bounded-query-directory-1750/README.md)。本覆盖层含323份工程文件，不是独立完整仓库；回放时保留同基线项目文档/依赖。合法选择器、准确修订点查、内部模型复制拒绝、接口错误码和首提交守卫有组件证据，完整提交索引闭包/产品历史排序/默认业务链仍未完成。

7份Schema和4份夹具已再生对拍，最新wheel/sdist构建、逐模块字节检查和隔离冒烟通过；面板/VSIX未变化，沿用既有构建日志。仍5条代码问题关闭、22条ABC剩余，真实35项AC未完成。
