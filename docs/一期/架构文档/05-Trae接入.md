# 一期架构05：Trae接入

版本：2.6  
更新日期：2026年9月21日  
状态：修订基线，待实现与实测；文档不代表产品已通过验收。

覆盖一期真实编辑器接入；基线Windows 11 x64、Python 3.13和实测确定的Trae发行版/版本。国际版与中国版须在兼容清单区分，不把同名产品当同一接口保证。

## 1 接入方式

integrations/trae承载命令注册、目录事件与面板宿主；interfaces/local承载本地API、管道与生命周期，resources/panel是唯一面板源码。扩展可用的命令/面板API必须在选定Trae版本实测，不假设任何其他编辑器专有注册API存在。

Trae官方文档提供MCP设置中的手动JSON配置和stdio服务方式；一期MCP基线采用固定程序入口的本地stdio relay，接入同一核心，不另起写入者。安装指引给出配置供用户确认，不自动覆盖项目已有配置；项目级配置启用遵守宿主信任提示。

```json
{
  "mcpServers": {
    "aitest-local": {
      "command": "aitest",
      "args": ["mcp-relay", "--binding", "<binding_id>"]
    }
  }
}
```

此处aitest命令与参数是拟实现的插件CLI合同，须随制品提供并验收，不代表机器已安装。command应是可解析可执行文件，参数单列；有空格安装路径的行为必须按实测处理，不能拼接shell字符串。配置不放令牌/密钥/私有正文，env只允许必要非敏感值。

参考：[Trae官方MCP添加说明](https://docs.trae.cn/ide_add-mcp-servers)。官方配置能力与扩展Webview/生命周期能力分别验收；文档没有确认可用的Trae自动注册API，因此不编造其名称或签名。

## 2 核心与本地通道

面板→扩展→命名管道→核心；Trae AI→stdio relay→同一管道；CLI同样转发。发现信息含workspace_id、随机实例、PID/启动身份、writer_epoch。限制本机当前用户/登录会话，拒绝远程客户端；核对实例/进程/项目绑定，不能仅看锁文件存在。

多个目录在同一核心按项目隔离。第二窗口核实既有核心后连接，不能接入则报WORKSPACE_IN_USE。卸载不依赖停用回调必执行：外部维护命令仅清无效发现/本插件登记，不删除业务数据或活锁。编辑器退出不等于取消测试。

## 3 接口与动作

协议aitest.local/2.0，命令含request_id、action、project_id、binding_revision、target、expected_revision、intent_id（业务写动作适用）、参数。响应含相同请求、实例/工作空间、项目/绑定、对象修订、run_id或结果/结构化错误。事件含实例、workspace、writer_epoch、单调提交/事件序号、项目、修订和类型。

| 动作组 | 最小动作 |
| --- | --- |
| 项目/内容 | bind_project、save_context、save_delivery、save_acceptance、analyze_project、save_model_outbound_policy、save_project_view_preference、save_rules、publish_rules、generate_draft |
| 计划 | save_plan、publish_plan、prepare_run、start_run、revise_pending_steps、narrow_driver、authorize_step |
| 执行 | pause_run、resume_run、cancel_run、retry_step、attach_evidence、verify_pending、return_stage |
| 问题/报告 | update_issue、record_fix、request_regression、close_issue、create_report、record_local_review、export_report、copy_repair_brief |
| 查询/维护 | query、events、doctor、test_connection、storage_usage、backup、migrate |

Schema定义字段和错误：code、脱敏message、retryable、相关记录及下一步。协议主版本不兼容拒绝写动作；后续新增动作通过能力声明，旧语义不变。传输同request_id异参数冲突；业务intent在应用层持久去重。

## 4 面板、安全和降级

Webview仅包内资源，CSP、Schema消息校验、净化文本；不直接模型请求/任意文件读取/凭据解析。事件只发增量摘要，大对象按需分块，分页查询。游标失效SNAPSHOT_REQUIRED，重新取得同边界快照与游标后续读（见面板架构第4节）；项目切换迟到响应只更新原缓存，不污染当前视图。

缺MCP登记可以手动配置或用面板/CLI，不影响核心。面板API缺失时命令/CLI作为诊断和临时替代，但一期面板交付承诺仍须在选定Trae实测通过，不能用CLI成功替代全部面板验收。源码只读允许物化运行，数据目录不可写拒绝新写操作，未信任工作区不执行项目入口。

## 5 验证与兼容清单

doctor统一返回READY/DEGRADED/NOT_READY及原因，检查核心/锁/提交指针、索引/证据可用性、环境和可选连接；模型失败只降AI。完整扫描为显式维护，不为面板刷新扫描全部历史。

compatibility.json是将来实现时维护的实测制品，不用文档推测填pass。字段：os/os_version/runtime/editor/editor_distribution/editor_version/core_version/adapter_versions/artifact_digest/evidence_path/verified_at/result。覆盖安装、面板、命令、stdio MCP、目录切换、停用升级卸载、管道权限/实例校验、进程停止、文件系统刷新和恢复；未测untested。

P1-AC01—35全部按需求执行；提交中断、同意图/明确重跑、同元数据异内容、密钥不外泄、双入口单写等专项见[修订记录](../../修订记录.md)。

## 6 安装、连接与生命周期

| 阶段 | 实现步骤 | 失败与保留 |
| --- | --- | --- |
| 安装/启用 | 核对Trae发行版、运行时、核心/扩展制品和协议；注册本插件命令，加载共享面板 | 不兼容明确提示，未测不宣称可用；不覆盖其他扩展配置 |
| 目录绑定 | 规范化当前目录、选择/建立稳定项目，取得binding_revision | 多目录显式选择，显示名不是键；未信任项目不执行入口 |
| 发现核心 | 读取发现信息→核实实际进程/启动身份/实例/工作空间→能力握手 | 旧发现可清理但活锁不可删除；无法核实不另开写入者 |
| 配置MCP | 展示固定command和分离args，用户确认宿主配置；绑定编号为参数 | 不假设Trae有其他编辑器私有API；MCP stdout只用于协议，诊断写stderr |
| 面板订阅 | 同一提交边界返回快照与snapshot_cursor，从该游标之后重放并订阅；附件按需分块 | 重复事件去重；失效游标重建快照再续读 |
| 目录切换 | 取消旧视图订阅，获取新绑定快照；保留旧项目活动运行事实 | 迟到响应只归原缓存，不能把旧run显示在新目录 |
| 停用/升级 | 断开本入口订阅，注销自己确实创建的宿主登记；保存所需检查点 | 不把编辑器退出当取消；维护/迁移不得绕过活动执行保护 |
| 卸载准备 | 展示数据保留位置，提供无效发现/本插件配置的维护说明 | 不依赖停用回调；不删除业务材料或其他插件配置 |

任何自动注册增强必须有该Trae版本的实际能力证据；手动JSON是既定基线，不能把文档示例当作已安装能力。扩展版本、核心版本与面板资源摘要共同进入兼容记录。

## 7 消息字段与恢复语义

| 消息 | 最小内容与守卫 |
| --- | --- |
| 命令 | protocol_version、request_id、action、project_id、binding_revision、target、expected_revision、适用的intent_id与参数；校验类型及项目归属 |
| 响应 | 原request_id、实例/工作空间、项目/绑定、对象修订、run_id或结果；错误含code/message/retryable/关联记录/下一步 |
| 事件 | 实例/workspace/epoch、单调序号、项目、对象修订与类型；只发送已提交事件 |
| 能力 | 协议主版本、核心/适配器版本、支持动作与执行入口类型、对象分块能力；不暴露凭据 |
| 对象获取 | 项目、受控对象引用、请求范围与来源修订；校验归属，不能传任意路径要求核心读取 |

主版本不兼容拒绝写动作，新增字段允许旧客户端忽略，但未知关键动作明确报能力不足。request_id只去重传输；intent_id在应用层与业务结果原子保存。主版本/能力/业务修订冲突分别提示，不靠页面无限重试掩盖。

连接错误至少区分核心未就绪、地址/DNS/代理/TLS、认证、限流、能力缺失、修订冲突、摘要不符、材料不完整、结果未知和存储不可写。日志带请求/项目/Run/Attempt及相关修订，敏感正文不进入日志。重试策略只由connectivity执行。

## 8 验证主题与证据交付

| 主题 | 必须实际验证的边界 | 需求入口 |
| --- | --- | --- |
| 项目/计划/模板 | 单模块/plain、草稿/发布、依据三态、full范围、来源变化与保守quick | P1-AC01/03/12/17/20/25/30—32 |
| 执行/证据 | 分层、假成功、Mock/未知、日志关联、人工页面、暂停取消/新尝试/待核实 | P1-AC02/04—10/13/18—24 |
| 问题/报告 | P0/P1、复测失败、复制修复说明、精确修订、独立复核、摘要/证据包 | P1-AC11/14—16/27/29/33/35 |
| 存储/恢复 | 真实锁、epoch、每个提交中断点、输出抢救、恢复再中断、备份迁移、永久留存 | P1-AC19/28及存储分册机制 |
| 界面/接入 | 真实Trae命令/Webview/stdio、项目切换、刷新恢复、同意图跨入口、键盘和窄面板 | P1-AC26/27/34及兼容清单 |

场景详细预期仍由需求维护；本表只是实现主题索引。先用同一DTO夹具验证面板与CLI，再做真实Trae、核心重启和文件系统故障验证；夹具通过不能替代真实接入。每条证据含实际输入、版本、动作、结果、原始材料与AC关联，未执行不得填pass。

## 9 入口权限与人工确认来源

共享核心动作不表示每个入口可调用全部动作。核心握手区分human_ui、interactive_cli和agent_relay；通道类别由受控入口注册与会话上下文确定，不接受消息参数自报角色。此处防止把智能体工具调用误当用户确认，不承诺隔离同一OS用户下的恶意进程。

| 入口 | 允许行为 | 人工确认边界 |
| --- | --- | --- |
| 面板/受控扩展 | 查询、编辑、请求执行及展示核心确认挑战 | 仅实际用户操作可提交确认；Webview消息校验所属会话、挑战及动作摘要 |
| 交互CLI | 同一核心用例，展示待确认目标/输入并取得用户交互 | 无交互终端不提交人工确认；不提供--yes或正文user_confirmed替代 |
| MCP/agent_relay及非交互调用 | 查询、提交草稿/执行请求、使用已有有效授权启动或续行 | 不暴露或代理人工确认动作；未授权返回AWAITING_USER_CONFIRMATION及面板/交互入口 |

人工确认动作包括authorize_step、计划/规则发布确认、断言依据确认、问题级别与处置确认、本地复核、模型出站策略确认，携带已知缺口迁移的范围/清单确认，以及三期发送策略和提交确认。AI可准备草稿或发起请求，不得替代用户作这些决定。共享用例仍在核心校验权限，不能只从MCP工具列表隐藏名称。

核心生成ApprovalChallenge：challenge_id、action、project/intent或submission、target、input_digest、credential_scope、相关修订、origin_session、状态。用户查看冻结摘要后确认；核心以同一工作单元校验当前修订、消费挑战并保存ConfirmationRecord/ActionAuthorization及幂等结果。同一确认请求重传返回原记录；另一个动作或改动后的输入不能复用挑战。挑战不经relay转发，不以nonce存在本身认定用户同意。切换项目、输入改变或确认通道失效时旧挑战不可用；尚未执行且内容不变的既有有效授权不重复询问。

ConfirmationRecord保留核心分配的入口/会话、用户交互来源、动作和冻结摘要、对象修订、提交序号；客户端提供的姓名仅为展示，不作授权凭据。不新增平台账号、服务或授权微服务。P1-AC31/32及P3-AC07覆盖伪造来源、挑战错配、重复确认与实际用户确认路径。
