# 七字段current与Windows发布组件证据

本阶段[源码/测试/日志冻结归档](history/windows-publication-1967/README.md)保留原字节和摘要。

1967 passed、2 skipped（333.34s），解析修复前提交/Windows/迁移专项97 passed（98.37s），解析修复后新增2场景通过（2.42s）；Ruff和mypy138文件、生成物、wheel/sdist构建、制品字节对拍及隔离wheel冒烟通过。面板/VSIX源未改，沿用前轮日志并对拍字节，未声明重新构建。

- [39个新增场景的源文件](../../../tests/unit/test_windows_publication_integrity.py)、[97专项输出](windows-publication-regressions.log)、[全量输出](pytest-full.log)
- [真实API调用/旧根备份/失联意图回读探针](windows-publication-probe.json)、[27问题源码函数/行号/SHA清单](issue-status.json)、[执行记录](validation-results.json)
- [前轮四字段/os.replace的冻结源码与测试](history/publication-prototype-1928/README.md)；原389引用核对器计数更正见[evidence-count-correction](evidence-count-correction.json)，历史归档没有覆盖

current七字段及准确引用核对、旧格式备份迁移、原业务根/游标不改；实际Windows普通文件API、同卷/共享拒绝/只读句柄、未知发布与失联/刷盘故障、子进程切点均有断言。候选/前指针/描述符按摘要保存，缺current/未知状态不自动修复或空根降级。没有实际Trae或被测业务调用，没有物理掉电验收。

完整主责CommitManifest及幂等结果引用/文件摘要合同、证据对象与business_change_index完整闭包、实际核心实例来源、普通启动有界恢复、未知发布人工恢复、目标设备名称元数据耐久/物理掉电、产品历史/固定排序、默认业务入口与真实AC仍需落实。 A-05仍partial；5个整代码问题关闭、22项ABC剩余、35项真实AC未变。

收尾追加：准确SHA清单中的100000层数组会触发普通Python子进程RecursionError；现在转换为有限材料错误及默认CoreAssemblyBlocked。恢复定位预算内的坏结构也阻塞。[原1失败/1通过](windows-json-before-fix.log)、[原源码字节及摘要](windows-json-before-source.json)、[源码ZIP](windows-json-before-source.zip)、[修复后2通过](windows-json-after-fix.log)。前次1965全量和97专项在该修复前运行，最终1967全量覆盖全部现行源码和39新场景。

[可重跑的实际API探针](windows-publication-probe.py)，命令：`uv run python docs/validation/p1-abc-fix-20261003/windows-publication-probe.py`。只创建临时工作空间并执行本地组件，不调用模型或被测业务。
