# 公开发现路径阶段历史证据（2026-10-04）

完整1878 passed/2 skipped（194.70s）、523根因专项（131.15s）、152入口/管道/架构专项（26.47s）、Ruff/mypy134文件通过。本目录完成后冻结，不覆盖前序历史。

- [精确命令与输出摘要](validation-results.json)、[27项状态/355处引用](issue-status.json)
- [引用的72份源码/测试原字节](referenced-source-and-tests.zip)、[全部工程覆盖层](validation-source.zip)、[逐文件/压缩包摘要](source-manifest.json)
- [全量](pytest-full.log)、[根因专项](root-regressions.log)、[入口/管道专项](discovery-path-regressions.log)
- [旧源码9失败/1通过](core-start-before-fix.log)、[等效可重放测试](core-start-before-test.py)、[原源码/测试摘要](core-start-before-source.json)
- [实际解释器/venv前后PID探针](core-process-identity.json)、[旧启动缺陷诊断](core-start-before-probe.json)
- [源码/测试摘要及文档链接核对](evidence-check.json)

本阶段12类公开路径/保留名称反例全部失败（0.45s），已修复并保留真实Junction目标零访问证明。[前态输出](discovery-path-before-fix.log)、[测试原字节](discovery-path-before-test.py)与[源摘要](discovery-path-before-source.json)可以复核。

[完整提交反例](commit-closure-before-probe.json)仍未修复：记录到提交2而索引旧根，提交3健康却仍漏提交2；事件失败无当前快照游标。四处尚未修复的根因源码单独记录。

前阶段67新增回归验证互斥启动、指针发布、未知创建/存活、PID复用、准确退出码、停机等待/真实写锁、服务端SID/会话/PID、短读短写和帧上限。默认当前Python直接使用真实镜像并保留venv，实际Popen/核心/服务端PID一致。前阶段源码在[1866阶段](../core-startup-1866/README.md)；最初启动反例源码在[1799阶段](../finite-query-routes-1799/README.md)，回放测试为提取函数的等效测试，未冒称原完整测试文件字节。

新模块及所有修改的启动/接口文件已逐字节核对wheel，Schema/夹具再生不变，wheel/sdist与隔离冒烟通过。面板/VSIX未改，沿用既有日志。覆盖层要求同基线项目文档/依赖；这不是独立产品或真实验收。

A-02仍缺默认C/D、可信人工/权威排空、未知旧创建/其他自定义转启动解释器恢复、writer_epoch发现字段与真实Trae；完整提交索引闭包/实际业务链也未完成。仍5条整问题关闭、22条ABC剩余，35项AC无真实完成证据。
