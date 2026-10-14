# 迁移续修源码的新全量验证

旧source@14ea0ca/5397全量在18:26因记录器被测试的os.fsync故障注入误伤而结束，1112 passed/2 skipped/exit3，完整原回执见[上一轮](../p1-current-closure-20261014/README.md)。保留原日志，不重复使用旧running。

本轮使用零字节回收、冻结迁移备份、回滚切点与受控检查点守卫修后的5427节点。先验证记录器抗实际故障注入，再静态/生成/打包/安装，最后单次全量。记录器在加载测试前保存真实I/O函数引用，绕过全局Path.open替身；真实记录器I/O错误仍失败，不吞掉强制ledger/JUnit/退出/来源条件。进度仍为可选观测。

当前脚本与范围已准备，尚未登记新全量通过；主工作区重新冻结实际源码后才启动。完整通过须5427唯一节点、逐阶段回执、完整JUnit、退出0且无源码漂移。真实宿主/物理掉电/AC登记不变。


记录器预检已通过：8项真实Windows共享锁/重试/关键失败检查，以及[三个完整故障注入文件的38项](observer-probe-result.json)，完整JUnit和逐阶段回执在observer-probe目录。该数字不并入产品全量，正在准备主源码预检与重新冻结。


主工作区最新制品预检已结束，源码e7d6cb2：versions/lock/generation/ruff/mypy250/docs-facts/build/wheel-smoke/artifacts全部exit0，[预检结束回执](preflight-state.json)、[制品逐字节核对](artifact-source-integrity.json)及[601源码指纹](source-inventory.json)冻结新本机字节。236wheel模块与源码一致，生成物/源码无漂移。离线wheel安装用实际缓存，依赖版本与锁定回归分开登记。构建hatchling1.32.4；6项捕获I/O检查还验证真实记录器错误不吞。下步单次5427全量，尚不登记通过。
