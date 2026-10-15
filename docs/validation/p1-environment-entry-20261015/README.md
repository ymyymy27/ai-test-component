# 0.4.2 项目表单与真实 Python 载体登记

起点 fix@348731c，日期 2026-10-15。目标是解除安装后默认核心无法登记被测 Python 载体的断点。不是一期完整验收，也不是全项目全量通过。

## 已实现

- 新增宿主配置 AITEST_PYTHON_CARRIERS 与默认装配：核心装配时冻结登记，实际 prepare/start 仍调用原真实解释器/依赖解析器；坏配置只阻塞环境相关动作，不回退核心解释器。
- CLI python-carrier 从显式指定的虚拟环境、基础解释器及依赖目录生成摘要登记，不运行候选解释器、不生成业务环境确认。--output 只创建新 UTF-8 文件，已有配置不覆盖。
- Trae 机器级 pythonCarriersFile 只传给核心启动环境，并参与上下文变化检测；不修改进程全局环境或公开业务 Command。既有核心须受控排空重启。
- 项目名称/实际测试目标由表单填写；空值拒绝，不保存示例目标。现有原生确认与提交前上下文核对保持。
- 制品版本 0.4.2；隔离安装脚本按 pyproject 的准确版本选择 wheel，保留旧包时不再拒绝或误选。

## 检查和失败记录

锁定 Windows Python 3.13.0 / Pydantic 2.13.5。首轮四文件命令为：

```powershell
python -m pytest tests/unit/test_environment_configuration.py tests/unit/test_python_environment.py tests/unit/test_environment_resolution_flow.py tests/unit/test_cli_core_forwarding.py -q --disable-warnings
```

首轮 [JUnit](targeted.xml)、[输出](targeted.log)、[退出码](targeted.exit.txt)保留：新增夹具漏传 instance_id，超长参数ID超过 Windows 环境变量上限，整轮 exit 1。随后补实例编号与简短参数ID；不删除原失败，不把此轮登记成全部通过。

修后入口/CLI 两完整文件 **28 passed**，见 [JUnit](final-entry.xml)、[输出](final-entry.log)、[退出码](final-entry.exit.txt)。随后增加“真实宿主登记→实际解释器观察→不可变 PreparedRun 保存”的组件链检查，环境配置最终整文件 **11 passed**，见 [JUnit](configured-preparation.xml)、[输出](configured-preparation.log)、[退出码](configured-preparation.exit.txt)；业务材料和确认手势为明确夹具，不能冒充真实人工/Trae 验收。

宿主 **25 passed**、浏览器 **15 passed**；ruff、mypy（252 文件）、版本/锁、Schema、面板/VSIX/wheel 构建、内容核对及离线隔离安装退出 0，见 [逐项命令](release-commands.json)。新增测试后的最终静态结果另见 [最终检查](final-static.log)。旧范围与新范围重叠，不相加成为全量。

架构首检发现 CLI 直接导入基础设施，见 [原始失败](architecture-first.log)；已改为经 bootstrap 装配层导出调用。修后架构、验收台账、环境入口与 CLI 的最终整文件回归 **73 passed、0 failed/error/skipped**，见 [JUnit](final-regression.xml)、[输出](final-regression.log)、[退出码](final-regression.exit.txt)。最后 Python 构建/检查/隔离安装以 [最终命令回执](final-release-commands.json) 为准，全部退出0。

当前源码 [事实](facts.json)、[逐文件指纹](source-inventory.json)、[制品摘要](artifacts.json)和 [238 模块字节核对](wheel-source-parity.json)分别冻结；[事实对账](facts-final.log)无差异，5484 项仅收集。

## 未完成

真实 Trae 操作/端到端执行恢复尚未验；完整计划/执行向导、独立业务核验及可信动态复用仍缺相应材料或实现。登记入口已接不等于用户已经登记环境，不等于业务通过。安装与配置步骤见 [0.4.2 指南](../../一期工程整改/04-交接/0.4.2-项目创建与Python环境登记.md)。

CORE 1.87、AB 1.47、BC 0.50、BD 0.10 同步；公开业务 Schema/验收分册/三期状态登记不改，签署和整体对拍状态保留。
