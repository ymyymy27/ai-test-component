# 0.4.8 真实 Trae 界面续验

2026-10-16，Windows 11 x64、Python 3.13.0、已登录的 Trae CN。操作者为获得用户授权的自动化代理；使用真实扩展、命名管道、核心原生确认窗口和独立用户数据空间，没有注入合成的确认事件。此记录不等于全部一期 AC 验收。

## 发现和修复

1. 原安装位于打包应用重定向的 LocalAppData，Trae 无法看到解释器，真实启动返回 ENOENT。改用 `E:/AI-Test-userdata/` 下的独立运行环境、配置与工作空间，保留旧数据。宿主仅呈现固定 OS 错误分类、平台和路径存在布尔值，不回显原始错误、环境变量或凭据。
2. 原生确认允许等待 120 秒，但客户端读取只有 10 秒，出现“已经提交、面板却显示结果待核实”。固定确认预算为 120 秒，耗时业务读取预算为 130 秒；原生回执总预算 250 秒，宿主外层 270 秒。普通只读请求保留原预算，超时不自动重放。
3. 面板首次 doctor 直接派发，绕过宿主配置启动入口，产生短命且未加载模型/载体配置的核心。现在每次派发前先由宿主发现或启动核心，并在启动后再次核对配置；配置变化或连接失败则不派发业务操作。连接成功后自动刷新 doctor。
4. 主屏在未查询计划/报告时原来显示“等待准备计划”“暂无报告修订”，与真实已保存事实冲突；改为明确的待查询提示，阶段摘要不推断报告是否存在。
5. 重建锁文件时发现 colorama 的旧版本标签/URL 与制品摘要不一致，纠正为解析器确认的 0.4.6；未更换为未经核对的新依赖。

## 真实操作证据

- 项目 `trae-live-20261016`：真实 Trae 表单提交后持久保存，原始超时场景以 `query-after-confirmation.json` 核对实际修订 1；该次 UI 未正确收到成功，不能写为完整通过。
- 0.4.8：真实原生绑定确认等待超过原有 10 秒后成功回执，绑定 `binding-library` 修订 1；环境 `env-library` 修订 1 保存成功。
- 最终宿主启动修复后，`doctor-host-startup.json` 与 `doctor-host-delayed.json` 保持同一实例 `core-9a87e8bfd0e1`，模型状态 ready。此前每次 CLI 重启的实例分别保留在早期回执。
- 四个用例及范围通过正式 CLI 保存，源码通过 analyze_project 固定；`*-command.json` / `*-receipt.json` 保存实际输入与回执。
- 计划 `plan-library` 通过 Trae 发起、原生窗口确认发布，`publish-receipt.json` 是相同稳定意图的回读，不是再次发布。
- 四份断言依据逐一通过真实原生窗口确认。`prepare-receipt.json` 返回 prepared，引用真实源码、环境和确认材料。初次缺少占位字段及普通 CLI 超时分别保留为 `prepare-initial-invalid.json` 和 `prepare-cli-timeout.json`，之后沿原意图核实，不另造运行。

## 验证范围

初轮 52 项 Python 回归、单独 1 项真实管道延迟回归、26 项宿主及 20 项浏览器测试通过，分别见对应日志/JUnit。宿主启动续修后 29 项通过，见 `host-final.log`。后续修订的最终回归以带 final 的日志为准，批次有重叠，不累计为全量。

最终受影响 Python 路径 53 项通过；Unicode、MCP、验收台账和架构既有检查另批 110 项通过（见 compatibility-final.xml），不与前批相加宣称全量。宿主 29 项、最终浏览器 20 项通过。全套仅收集 5627 节点，未运行本轮全量。

## 实际闭环结果

- 四个用例通过正式已安装 CLI 执行，每项动作授权都由代理核对并点击真实核心原生窗口；独立核验均 matched。真实执行和来源附件引用见 execute/verify-library-*-receipt.json。
- 正常输入与语法用例曾确认超时，未提交的 USER_DECLINED 回执保留在 *-unconfirmed.json；原意图重试后才执行。报告首次确认因操作会话引用旧窗口标识超时，Trae 显示 USER_DECLINED，核实未保存后沿原意图再次确认；不计为产品执行成功。
- 报告与导出均从真实 Trae 高级入口发起并经原生窗口确认。report/export-receipt.json 是随后以相同稳定意图经正式 CLI 回读的回执，不是另建报告。report-record.json 为实际不可变报告：full、passed、A，M=S=E=V 为四个用例，R 为空，gaps 为空。
- export-verification.json：ZIP 清单的 17 个文件长度/摘要全部吻合，包含 4 份 runtime-source 附件；missing/excluded 均为空。导出复制到 E:/AI-Test-userdata/trae-acceptance-20261016/trae-library-report.zip，原对象保留。
- 优雅退出旧核心后，由 Trae 连接按钮启动新实例 core-fe7a74fb4403。真实主屏刷新显示核心 READY 和业务 passed；doctor/panel-after-restart.json 与 restart-verification.json 证明新实例回读，报告/导出未变化。此项不等于强杀、掉电或未知副作用恢复验收。
- 操作链混合真实 Trae 与正式 CLI；没有声称全程均由图形向导完成。主屏 control_state 仍按核心 running 显示，业务 passed 与执行控制状态分别呈现，不由前端改写。

## 制品与仍未验证范围

0.4.8 wheel/VSIX 最终构建、隔离安装与共享面板校验见 artifacts.json、安装核对文件及 final 日志。P1 验收状态没有升级。真实业务目标、全部模型草稿操作、人工补证/外部导入/修订/恢复的完整真实宿主场景、未支持的构建或动态载体以及一期全部功能仍未证明。接口六主题关闭结论不替代这些产品验收。
