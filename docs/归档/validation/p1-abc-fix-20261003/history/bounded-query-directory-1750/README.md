# 有界查询目录阶段历史证据（2026-10-04）

完整1750 passed/2 skipped（253.24s）、395根因专项（174.85s）、96目录/迁移专项（107.70s）及Ruff/mypy133文件通过。此目录冻结后不再更新。

- [精确命令、结果与输出摘要](validation-results.json)、[27项状态/285处引用](issue-status.json)
- [引用的63份源码/测试原字节](referenced-source-and-tests.zip)
- [源码/测试/配置与锁文件覆盖层](validation-source.zip)、[逐文件与压缩包摘要](source-manifest.json)
- [全量](pytest-full.log)、[根因专项](root-regressions.log)、[目录/迁移专项](query-directory-regressions.log)
- [冻结旧源码的3项反例](query-directory-before-fix.log)、[等效可重放测试](query-directory-before-test.py)、[旧源码/测试摘要](query-directory-before-source.json)
- [有限组合/路由的剩余缺陷诊断](query-contract-gaps.json)

旧实现字节来自[1722阶段](../connection-safety-1722/README.md)。本阶段源码压缩包含322份工程源文件，不是独立完整仓库；回放时覆盖同基线仓库并保留项目文档/依赖。可读旧v3查询布局经活动守卫、备份校验后迁移，原根和节点保留。分页与小提交的路径、旧游标、发布中断及默认重启是组件验证，不能代替真实产品验收。

最新Python制品已重建、逐模块字节核对和隔离wheel验证；公开Schema、夹具、面板/VSIX未变化，沿用此前相应生成/构建日志。仍5项代码问题关闭、22项ABC剩余；QuerySpec有限组合、完整CommitManifest索引闭包、C/D默认业务链及真实35项AC未完成。
