# Trae 实机安装与模型接入

基线：fix@d129c24，接口关闭与 0.4.6 已提交推送。真实用户操作验收尚未完成。

## 已取得的事实

- Trae CN 已运行；其官方 CLI 返回 VSIX 安装成功，扩展列表为 `aitest-local.aitest-trae@0.4.6`。
- 0.4.6 Python 包已安装到用户目录的独立 Python 3.13.0 环境；不是源码可编辑安装。
- Windows 截图显示锁屏，已停止 UI 操作并请求用户解锁；没有绕过锁屏、登录或安全提示。
- 用户提供的模型密钥已写入 Windows Generic Credential；只记录用途/引用，读回一致；不写入仓库、普通配置、日志或命令行参数。
- [DeepSeek 官方接口说明](https://api-docs.deepseek.com/zh-cn/)指定的官方服务模型列表返回 HTTP 200，实际列出 deepseek-flash / deepseek-v4-pro。使用项目 HttpModelProvider 和不含项目私有内容的连通性提示，实际收到准确 AITEST_OK，见[组件证据](deepseek-component.json)。这是供应方组件取证，不等于 Trae 内模型出站策略/人工确认验收。

## 发现并补齐的启动缺口

模型适配器原来只支持进程内显式注入端点/凭据引用；Trae 标准 ensure-core 启动缺少这条配置通路。0.4.7 增加机器级 `aitest.modelConfigurationFile` → `AITEST_MODEL_CONFIGURATION` → 核心冻结配置。文件只接受 `schema_version: aitest.model-provider/1`、HTTPS `endpoint`、`credential_reference`，不接受密钥字段、重复 JSON 键、查询/片段/用户信息、超长内容或相对路径；坏配置只降级模型相关动作。

凭据继续按 model 用途由原 SecretManager 解析；配置文件不携带密钥。改变端点/材料仍走原模型策略确认，宿主设置不代签、不自动发送项目材料。改变配置须受控重启核心，既有实例不热换目标。

## 验证与剩余

新增及既有模型接线测试 28 项通过，见[JUnit](host-model.xml)。首次测试的文件扫描在写锁仍持有时被 Windows 拒绝，见[原失败](host-model-first.xml)；已将扫描移到释放写锁之后，生产逻辑未因此放宽。

真实 Trae 的面板打开、配置、受控确认、项目/计划、执行、核验、报告、问题、半程控制、恢复、导出尚待解锁后实际操作。没有把安装成功、真实供应方调用或单元测试登记成 P1 verified。

0.4.7 进一步修复一次性 ensure-core 的生命周期所有者：原进程将 CLI 自身作为父进程，退出后核心也退出，下一次 doctor 会启动不同实例（真实 0.4.6 反例已保存）。宿主启动改用 OS 返回的 CLI 父进程，即真实编辑器/宿主进程；不是业务请求自报 PID。普通进程内启动仍按原自身生命周期，活动/未知执行的退出守卫保持。新增跨进程延迟读取必须保持同一实例的回归。

生命周期首轮仅采用 getppid，Windows venv 重定向启动器仍是临时父进程，真实测试 1 failed / 135 passed，见[原 JUnit](lifecycle-first.xml)。现已核对父进程映像，仅在其确为当前 venv 启动器时读取 OS 的上一级父进程；无法核对时拒绝启动。最终 [136 项回归](lifecycle.xml)通过，包含真实延迟读取同实例、启动身份与活动/未知执行退出守卫。

## 最终安装与回执

0.4.7 VSIX 已安装，独立用户目录 Python 环境已安装同版 wheel；Trae 机器设置已配置并备份原文件。安装后的核心使用真实系统凭据，模型能力 ready，启动 CLI 退出 3 秒后 doctor 仍是同一实例；该探测宿主结束后核心可正常退出，后续由 Trae 连接时启动。见[安装记录](install.json)、[命令回执](installation-commands.json)。

[发布检查](final-release-commands.json)及[生命周期修复后的重建/安装检查](lifecycle-commands.json)均退出0；宿主25、浏览器19、登记准备16分别通过。最终 VSIX 仅补同步交接 README 后重打包（final-vsix-repack.log，退出0），其源码与初次发布相同。[产物](artifacts.json)、[源码字节核对](wheel-source-parity.json)、[规模及仅收集结果](facts.json)单独留存；没有运行完整全量。

等待解锁后才能继续真实 Trae 全功能操作；本轮 P1 验收结果保持，不以组件供应方成功代填 verified。
