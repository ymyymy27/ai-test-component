# 同一发布边界组件原型历史证据（2026-10-04）

全量1928 passed/2 skipped（270.06s），573根因专项（216.11s），Ruff/mypy137文件、生成物、构建、制品字节与隔离wheel通过。50个新增场景由最终全量/专项覆盖；88提交/入口专项是限制锁重入前记录，75锁/释放专项在限制后执行。中间兼容和探测失败保留。源码冻结后不覆盖。

- [完整执行JSON](validation-results.json)、[27项状态/389处修复与测试引用](issue-status.json)、[核对结果](evidence-check.json)
- [引用的78份文件原字节](referenced-source-and-tests.zip)、[工程覆盖层](validation-source.zip)、[逐文件和压缩包摘要](source-manifest.json)
- [全量](pytest-full.log)、[根因专项](root-regressions.log)、[提交/入口中间专项](commit-closure-regressions.log)、[锁/释放专项](commit-closure-lock-regressions.log)
- [实际旧缺陷探针](commit-closure-before-probe.json)、[实际新默认路径故障探针](commit-closure-after-probe.json)
- [旧格式兼容中间失败](commit-closure-full-intermediate.log)、[锁探测中间失败](commit-closure-lock-full-intermediate.log)

记录/准确索引/事件/意图结果准备后同一current切换，失败保持旧完整边界，发布后失联不产生第二修订。旧正式事件或events.json逐项核对，经备份迁移保留原字节/摘要，未知/错配阻塞。旧同序异内容不能冒认提交。真实Python子进程在切换前/后os._exit(77)验证进程中断一致性，不是物理掉电或Trae/业务AC。

当前仍为内部字段与os.replace组件原型：主责current格式、Windows ReplaceFileW/候选和前指针恢复材料、证据对象及业务变更索引完整闭包、实际核心实例来源、有界普通启动和产品历史/排序尚缺。A-05保持partial，不把组件修复称为完整存储合同。5条整问题关闭，ABC仍22项；真实35项AC未完成。

历史启动/路径及其他专项是对应旧快照结果；当前全源码以本阶段全量/573及构建对拍为准。面板和VSIX未改，沿用前轮日志。覆盖层基于相同Git基线并要求原项目文档/依赖，不是独立可验收产品。
