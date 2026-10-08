---
contract_id: AB-001
title: 端口与保存语义
provider: A
consumer: B
contract_version: "1.33"
contract_status: reviewing
provider_implementation: partial
consumer_implementation: partial
verification_status: not_run
last_verified_commit: null
blockers: []
next_owner: A
next_action: 默认来源、准备快照与受控依据确认已有组件证据；未发布响应补登记已有受控证据；继续可信人工会话、未使用授权/复用、初始运行与C/D默认入口及真实验收；Q4按DEC-009执行。
---

1.33 模型传输总截止与完整响应（2026-10-08）：默认ModelProvider沿一次显式端点POST的实际单调总截止，覆盖DNS、连接、TLS、发送、响应头及正文；不使用隐式代理、不重试、不跳转。响应默认最多4MiB，可信传输装配可冻结不超过4MiB的更小预算，成功/错误状态都不得无界读取。既有HttpResponse(status, body)构造兼容，新增body_complete/error_class只记录实际传输完整性；截断、超限、慢响应及歧义framing不能成为有效草稿。只有完整200及严格UTF-8/唯一字段/有限JSON数值、准确choices[0].message.content字符串和可选非空字符串id才返回草稿；其他状态保留失败分类，不回显过滤前材料。逐次发送复核model用途、准确凭据引用、未清空及头字段安全；失败仍保留原响应/意图编排语义，不重发或改结论。公共ModelCall/Result签名和Schema、FR/AC不变；真实供应方故障及人工产品验收仍须另验，reviewing/partial/not_run保持。

1.32 不可变材料的缓存与实际JSON字节保持一致：写入缓存采用规范落盘字节的解码结果，不能保留调用方原嵌套对象/tuple等与落盘数组不同的形态；读取/遍历的返回值不得暴露可修改内部缓存的引用。修改原输入、准确读取结果或遍历结果不应改变同一内容身份的历史事实，重开读取须得到同样材料。权威/有序树节点读取拒绝任意层级重复JSON字段及NaN/Infinity/-Infinity，不能取最后字段或非JSON数值充当成功事实。权威节点沿用完整清单单文件16MiB上限，有序节点沿用2MiB上限；读取至多上限加一字节判超限，写入及已有文件核对也遵守同预算，不能只在stat后无界read_bytes。摘要/路径/节点形状与业务字段守卫继续分别核对，不以缓存隔离代替实际来源或全历史巡检。合法旧JSON、命名、Schema、索引/仓储格式不迁移；格式等价的Python容器以实际JSON语义回读。FR/AC、reviewing/partial/not_run不变。


1.31 有限查询的摘要必须与实际物理键一致：查询读取已有叶页时严格核对project/kind/id、实际正整数revision、实际非负commit_sequence及可选排序计数；布尔/文本/小数不能转换或按缺省值解释。报告/问题身份与已声明facet形状须合法，叶条目的完整物理键须等于同一摘要在本目录的规范键之一，不能把另一项目、身份、业务结果或facet放在所请求前缀下。材料摘要正确只证明保存字节，不能替代上述语义核对；任何读到的异常返回maintenance_required且整页不暴露部分结果，不回退历史扫描。仓储列表水合准确修订时还核对实际对象、项目归属及原摘要白名单字段，不能用int/str转换或另一项目正文冒充该索引行。迁移后归属读取固定身份元数据；仅旧版显式过渡路径允许核对已加载旧提交台账，普通查询不增加全历史扫描。旧合法物理目录/游标/缺省排序语义保留，不修改协议、Schema或索引格式；异常材料保留并要求受控维护，拒绝不等于恢复。FR/AC及reviewing/partial/not_run不变。


1.30 仓储准确修订与权威元数据只接受实际整数：记录revision/expected_revision、权威提交水位与账本计数不得由布尔、文本或小数转换取得。真正不存在的身份可返回0；已存在但缺字段、非对象或错误类型的元数据必须显式材料无法核实，不伪装不存在。分片记录/台账读取核对对象形状，目录引用不能充当value记录。公开准确读取修订须为正整数，写入期望须为非负整数，暂存前拒绝错误值；原持久意图回执的修订/序号及created引用也须逐项保持原实际类型，不丢弃坏引用或将其int转换成功。权威头Schema/提交水位须与实际树一致，包括类型一致。新身份登记归属时明确初始化revision=0，随后正常追加；已存在有效元数据/历史未知附加字段原样保留，不迁移或改义历史。损坏权威状态仍保留材料并阻塞恢复/执行，拒绝本身不代表已恢复业务。公开字段、记录格式和FR/AC不变，继续reviewing/partial/not_run。

1.29 冻结业务源码快照身份：技术固定ID和content_identity继续证明原固定内容，业务snapshot_id以新命名空间aitest.business-source-snapshot/2.0对完整冻结源码正文（排除snapshot_id自身）生成。项目/绑定准确仓储修订、目的、形态/Git/plain身份、逻辑范围、选择、排除、复取依赖/范围、技术清单引用均参与业务身份；同字节但冻结合同不同须独立保存，不覆盖旧snapshot@1，不能误报不可变身份冲突。相同规范材料的新意图仍复用同一业务快照，指针按准确CAS独立递增。所选路径在应用层排序去重后交给技术端口并冻结/摘要，与实际清单保持一致且不改变所选范围，不能因界面顺序误拒绝；这不改变依赖和排除正文的语义。原历史编号/Schema/意图按准确引用回读，不迁移、重新编号或根据今天的目录重算。A新意图闭包同时核对冻结范围/选择/复取字段与实际保存快照，不用仅绑定/目的匹配冒充完整合同。DEC-009的content_identity口径、公开协议/Schema/PreparedRun字段、FR/AC不变，保持reviewing/partial/not_run。

1.28 源码原意图回读须核对原source_pin_intent的准确项目/意图与输入摘要、结果字段、准确业务快照、受控绑定及实际固定清单/全部blob；有source_current_ref时只读取本意图准确指针修订，不能以最新指针代替。新意图同时保存规范冻结inputs并核对其摘要，旧意图缺inputs/source_current_ref时仍按原已存快照验证材料，不补猜字段或重新固定今天的目录。结果/归属/修订/内容身份/固定范围或字节缺失损坏时返回B_SOURCE_UNVERIFIED，不返回仅元数据的reused成功、不产生新提交。旧SRC暂停不阻断原已保存材料读取，但新固定仍按具体能力阻塞。原快照范围、选择、排除、目的与绑定路径须能同实际固定清单核对，固定字节不证明当前源码/环境/加载/业务通过。FR/AC、公开协议与PreparedRun字段不变，继续reviewing/partial/not_run。

1.27 新增协商动作 `submit_delivery`（人工动作）：保存独立、不可变的 `delivery_submission@1`，原 `save_delivery` 仍为草稿。参数严格为 project_revision、expected_revision=0、submission_id、delivery_ref{delivery_id,record_revision}、source_ref{snapshot_id,record_revision}，命令目标为submission_id。准确任务仓储修订从已保存草稿的task_revision读取；源码须核对业务content_identity、技术固定清单/全部blob与准确受控绑定来源。版本文字只作交付标签，不替代源码身份；固定源码不证明当前目录、环境、加载、执行或验证一致。挑战冻结准确项目、草稿、任务、快照、绑定修订与摘要，确认前核对这些材料仍为当前修订；四份核心确认事实、正式提交和原意图回执同一六记录短事务。新正式提交拒绝历史自填verified_in_scope；所有验收项初始仍未验证，不从自述推导结论。原意图回读准确正式提交及来源，不再确认、不改写后续草稿/绑定，不要求当前源码相同；异输入冲突，丢响应不得重复提交。未知/跨项目/损坏/无准确任务或固定来源的草稿不得自动升级。新增记录登记到现有记录/摘要索引与业务变更台账，业务变更分类暂保留unclassified；启用专用业务变更查询前须显式索引迁移，不能更改历史分类或自动提升未知旧记录。交付记录查询仍只用有限QuerySpec；这不是三期上传授权，也不扩大平台白名单。实际新执行与独立核验的交付验证投影、面板及真实宿主验收仍待接通。FR/AC数量不变，保持reviewing/partial/not_run。

# B-A 跨包需求：B 包所需端口与保存语义

版本：1.32
日期：2026-10-07
提出方：B 包（项目与计划）
接收方：A 包（本地核心底座）；第 5 节的口径冲突同时抄送裁定方
状态：**三项归属与范围已由项目负责人裁定；默认业务链及完整保存闭包继续实现与对拍**
依据：一期架构文档《01-项目与计划》第 7、8、9、11 节；《04-存储与恢复》第 2、9、13 节；需求 P1-FR01、P1-FR03；组长实施方案第 3 节

---

## 1 目的与前提

1.24按一期架构01第3/8节和架构05第9节补规则/计划受控发布。默认publish_rules/publish_plan复用受控记录保存编排，参数显式包含准确project_revision与expected_revision（等于命令外层），目标为rule_id/plan_id。规范发布正文、当前项目/旧发布记录、计划实际消费的准确Case/Scope/Rule仓储材料及模板摘要均冻结；计划请求正文必须等于准确已保存Case/Scope，不能用客户端改写正文发布同一引用。选定旧不可变修订不自动换为最新，正文revision与仓储record_revision按DEC-007保留区别。

规则的客户端confirmed布尔只参与既有领域门禁，单独不能提供许可；发布还须核心一次性交互。原规则/计划领域门禁仍负责必测/模板范围、独立核验、缺依据和未知字段，受控保存不放宽。四份核心来源、发布记录和效果回执六记录同批；原意图回原准确发布结果及同批序号，换输入冲突。发布结果既有confirmation_id提交序号语义保留，内部准确核心确认另存，不静默改变旧字段语义。

新发布记录保存规范approval_parameters，回读时重建并逐字段核对实际业务正文、材料引用/摘要及准确效果回执和核心证明。ContextGap必须经既有领域校验，已定义必需缺口不能改标非阻塞。带摘要/指纹或approval_confirmation_id的冻结正文，如落盘前过滤会改变实际字节，整批拒绝；不得静默过滤后消费原确认并报告发布成功。准备消费计划与规则、计划构造消费规则时均核对来源；旧无来源发布历史可读，不能用于新准备/发布消费，须显式受控重新发布。共享绑定旧记录保持兼容，不要求其新增发布参数字段；动作数及公开Schema不变。上述是默认业务组件合同，不授予执行/核验许可，真实宿主和默认C/D仍待接通。

1.23按一期架构01第1/8/12节和架构05第9节补绑定的保存/消费来源。默认save_binding需要CORE-001受控挑战；参数冻结规范binding、准确project_revision和expected_revision（与命令外层相同），目标为binding_id。冻结准确项目与旧绑定仓储引用，新建锁内核对不存在；绑定目录、git/plain形态、归属、基准及confirmed均属于本次正文，不由入口标签或布尔确认提供许可。核心四份来源、绑定和原保存效果回执六记录同一短事务提交，回执核对准确意图/记录修订/正文摘要/确认。所有保存结果通过原意图准确回读，异输入冲突、旧无来源历史保留，不自动伪造证明。

默认analyze_project的新固定与source_analysis核对以及prepare_run冻结材料消费，须回读准确绑定控制来源；固定前和发布锁内重复核对。缺来源旧绑定不能借confirmed=true升为有效来源，应受控重确认保存新修订并重新固定/准备。分析许可不授予源码外发或实际执行；历史意图回读不产生新的固定/执行权限。绑定正文版本与仓储读取字段继续保留各自语义，不用通用自增假设覆盖DEC-007。入口动作数量和公开Schema不变；真实宿主、布局/入口变化后的实际执行证明与其他人工发布仍待接入。

1.22按一期架构01第9节、架构05第9节与P1-AC31/32补默认模型出站策略的准确人工来源：save_model_outbound_policy保存策略、CORE-001挑战消费/交互/确认及原意图结果在同一短事务提交。挑战冻结准确项目仓储修订、先前策略仓储修订（新建时锁内核实不存在）、规范策略正文（接收目标、允许/撤销类别、源码片段及AI开关）与当前配置的凭据用途/引用名，不冻结凭据正文。策略正文revision沿既有策略规则与expected_revision一致递增，规则/计划的正文与仓储区别继续遵循DEC-007，不改为通用同义版本。

客户端policy.confirmation只作为不可信输入，不能产生许可或保留其ID/提交号；默认受控保存由核心生成OutboundConfirmation并绑定准确CORE确认引用。固定六记录批次（四份核心来源事实、策略、幂等回执）核对最终提交序号；任何暂存/提交前失败无部分消费，提交后丢响应按原意图回准确原策略引用，换输入冲突，不再次确认或覆盖后续策略。内部来源元数据不改变原公开策略字段含义，不新增存储服务或任意查询面。

实际generate_draft发送前读取准确已保存策略与核心确认、消费历史、冻结材料及配置引用范围；无来源旧策略不能发送，但保留历史，可经新的受控保存确认升级。原保存意图只回读该意图绑定的准确策略仓储修订、正文摘要与确认，不能借另一合法策略的引用替换原结果；配置引用变化不破坏冻结历史的读取，但旧确认不能授予当前配置新的发送权限。原意图已发出的结果补登记/历史回读不要求新外部发送；AI关闭或撤销仍立即阻止新请求。发送意图锁内及实际调用前再次核对策略来源，坏记录、范围/目标/配置引用变化保持待确认/阻塞，不只核对字符串ID或客户端摘要。组件注入的人工事件和合成供应方transport不代表真实用户/Trae/DeepSeek验收，默认其他发布/授权和实际宿主仍待接入。

1.21补旧版记录结构核对：事件恢复读取准确记录时，records/聚合/修订列表及载荷的非法形状须转换为材料无法核实并保留暂存，不能以AttributeError中断恢复接口或把非对象载荷当作可用记录。归属核对只接受对象载荷；此边界不放宽任何提交或事件身份条件，亦不代表完整旧版历史投影维护已全部改为有界读取。

1.20补旧版事件恢复：commit.json及调用方传入的提交序号只作查找提示，不能证明业务已发布。reconcile必须读取records权威提交，按准确request/intent/project/workspace/writer_epoch、事务内created顺序与实际记录归属核对每个record_created事件，并重新核对含聚合类别的event_id；缺记录、跨项目、错误类型/修订或不完整事件集不能发布。已有边界也须与暂存及已保存日志逐项相符后才可清理；未知暂存原样保留，核心恢复报告blocked，不能把“未找到”记成已修复或自动重放。现行current提交仍消费同一完整冻结事件根；旧事件恢复只证明原业务记录存在，不产生新执行、授权或验证事实。

旧版事件核对读取权威和暂存时采用64 MiB上限、边界采用64 KiB上限，拒绝路径链接、重复字段、非对象和无法保持安全身份的材料；超过预算保留原材料并要求人工维护，不静默丢弃或改义。预算不代表完整旧版维护的所有历史投影读取已经有界。合并前重新读取并比较已核对的完整事件集，暂存中途变化不能借用先前核对结果。已存在但无效的边界不能当作“不存在”覆盖。

1.19补恢复活动身份：增量启动与显式完整维护均在同一工作空间写锁内核对活动标记。清理只接受已发布权威提交中的准确request_id/project_id/intent_id/commit_sequence与in_progress状态，提交序号集合或commit.json投影不能证明该活动已提交。旧无current工作空间须从真实records权威台账及对应记录验证准确归属，不能用旧投影猜测。核对成功先保存永久恢复事实，删除前重复比较原标记；保存失败或标记变化保留原材料并阻塞。

活动JSON采用有界、拒绝重复字段/非对象/链接的读取。未知、损坏、缺身份或未提交的标记必须保留，不解释为“没有活动”，不能先修投影/清标记再寻找副作用。只读巡检明确返回待核实；写入者仍活动时拒绝维护写入。此合同只证明准确已提交事务残留可清理，C实际执行句柄/输出抢救和未知副作用处置仍须独立实现、验证；不自动重放执行。

1.18补未发布响应的受控补登记：新增能力协商动作`resolve_model_response`，参数为原`outbound_request_id`与准确`saved_response_ref`，命令expected_revision必须为1，并使用独立修复意图。只消费同项目原意图@1、完整generation_identity及A端口核对过的安全旁录，不发送模型请求，不依赖当前供应方/凭据可用性。缺依据、未知响应或伪造引用保持阻塞；不能从旧数字推定冻结依据。补登记重建原准确引用并核对正文摘要，在事务外观察实际源码，短事务内核对当前修订、目标占用与原意图；与正常发布共用一次原结果@2和草稿@1的原子提交。已提交的同响应只读回原结果，不重复写入。

旁录保存的observed_currency和当前核对共同约束结果：曾失效的响应不能因源码回退重新升级；人工推进优先派生superseded_by_manual，其他失效为source_changed，只保存安全历史。补登记不产生动作授权、执行或有效验证。错误正文只保留原摘要/长度，旧旁录缺长度时保存null，不猜0。提交失败仍返回unresolved和原准确引用，不能重新固定损坏材料或放宽权威完整性守卫。

`model-responses/`准确旁录索引与安全对象同属永久备份闭包；恢复需保留其原字节并逐项核对，不因只备份对象而丢失原请求到响应的索引。旧备份缺索引保持缺口，不猜对象归属。此动作及备份补闭包先用受控组件/故障注入验证，真实供应方、宿主与掉电恢复仍须独立验收。

1.17补模型依据：默认模型生成的`source_revision>0`必须同时传现有SnapshotRef形状的`source_ref`（source_snapshot_id、purpose、content_identity、record_revision），按准确业务快照读取并冻结实际绑定/正文摘要；整数1本身不证明来源。`source_revision=0`只表示本次不使用源码，草稿RevisionContext.source_revision保存null，不能夹带SOURCE_SNIPPET。`base_manual_revision>0`必须传`manual_ref`（record_id、record_revision、content_digest），固定generated_content准确仓储修订及安全正文摘要；0可不引用人工正文，或以准确record_id/revision=0/content_digest=null冻结“尚无正文”。

模型草稿的RevisionContext.binding_revision取实际source_ref保存的准确绑定仓储修订；不使用源码/绑定时为null，不猜1。此null仅表示不适用，未知依赖仍阻塞；旧数字正文保持历史读取，定向失效只比较生成时实际使用的依赖。模板人工路径已有显式绑定输入保持原校验。

新出站身份同时冻结上述依据，异输入同意图冲突；旧缺依据记录只作历史查询，不猜实际来源或自动重新发送。文件/Git/技术blob核对在意图前、发送前和响应后事务外完成；短事务内重复核对准确项目/策略/源码/绑定/人工正文引用。出站意图及当前草稿发布再次核对实际固定源码材料并登记清单路径；过期响应作为安全历史保存，不把已损坏旧源码重新标为可靠当前材料。

人工正文推进优先派生superseded_by_manual，来源/策略变更或无法证明则source_changed；两者均保留过滤后响应/原依据，不发布当前草稿。生成目标已有正文时不覆盖；响应期间目标被占用也保存为历史。意图已登记但发送前撤销时明确保存“未调用供应方”的终结结果；同意图回读，不留下可盲发的新授权。真实供应方/可信人工入口与完整实际依赖映射仍需验收。

过滤后响应先经A的ModelResponseStore端口可靠保存为永久对象及准确请求/输入摘要的不可变旁录，再尝试业务工作单元。当前提交损坏或发布结果未知时不放宽完整性守卫、不返回当前草稿；返回unresolved及saved_response_ref，同意图回读已保存材料且不重新调用。旁录不是权威业务提交或验收通过，当前源材料仍需受控恢复后核实原提交。旁录禁止凭据正文、重复字段、跨项目引用、路径链接和无界读取，使用同一安全对象存储与生命周期epoch。旧意图无旁录保持未知，不猜响应或自动发送。

1.16补消费闭包：新PreparedRun（prepared）引用的准确source_snapshot，以及初始运行登记意图引用的PreparedRun@1和对应源码，都必须在准备权威节点前重新读取同一权威根，核对项目/修订/内容身份及实际技术材料；不是只核对本批新source_snapshot。新批次source_material_files/file_digests包含这些实际消费的历史材料。不得把“本批没有改源码记录”解释为无需证明消费的固定字节。登记意图fingerprint必须等于准确准备正文摘要。

旧v2无source_material_files、旧源码记录无技术引用时仅保留可读历史与未核实语义；新准备/新登记不得沿用缺少固定字节证明的旧来源。已有技术引用的坏blob仍拒绝核对，不能因“旧记录”豁免。正文内联digest不是文件引用，只有上述明确引用按准确记录读取，不遍历无关历史或按摘要猜对象。

1.15补固定源码材料闭包：SourceSnapshotPort新增只读`verify_pinned(snapshot_id)`协商能力，返回经实际清单及全部固定blob核对的原清单，不扫描当前源码、不写材料，也不以调用方布尔证明替代核对。清单读取有明确字节预算，重复JSON字段、非法路径/身份/大小或不一致引用整体拒绝；固定blob按安全原字节流式核对摘要与大小，相同摘要只读一次、冲突大小拒绝。缺能力时阻塞新分析及实际来源核对，原意图回读仍是历史事实。

带`pinned_snapshot_id/pinned_manifest_digest`的业务source_snapshot在写入意图/权威节点前、已发布本批清单核对时，必须按项目和准确业务清单核实技术清单及固定blob；缺损不消耗新意图、不推进current，准备按既有basis_unverified返回blocked。清单v2新增兼容可选字段`source_material_files`，新批次显式保存准确所引用技术清单/blob路径及file_digests，不允许遗漏或多出路径；原v2历史缺该补充字段仍可读取，但实际固定字节核对不能省略。元数据文件预算16MiB；blob按记录大小流式读取，不套用JSON文件16MiB预算。原始坏字节保留，不用重新固定当前源码悄悄修补历史。

1.14补共享工作单元 `next_commit_seq()->str`（冻结于第8.8节的同一序号语义），供独立确认在单条提交前固定准确边界。默认来源/准备/确认见BC-001 0.11：完整准备DTO与意图同批保存，已知归属的嵌套正文不得按命令项目重新贴标签。物理身份准入先核对16KiB以内、无重复字段且类型严格的原始JSON，再在全生命周期写锁内推进epoch；拒绝材料保持原字节。公共命名空间、可信人工会话和C/D完整消费仍为部分实现。

1.13补充默认源码业务入口：analyze_project按准确已保存项目/绑定读取真实目录，经SourceSnapshotPort固定并核实字节，Git形态另经SourceControlPort.snapshot_identity核对真实仓库/分支/HEAD、tracked二进制差异与untracked文件摘要；plain不调用Git。Git绑定不一致、固定期间变化、链接/未知材料或当前绑定修订漂移须拒绝，不能用调用方摘要替代真实来源。snapshot_identity是新增协商能力，缺能力明确阻塞Git动作，不降为plain。

B用现有SourceManifest/content_identity合同保存不可变业务快照，另冻结pinned_snapshot_id与准确binding引用，业务snapshot_id含项目及绑定修订命名空间。来源操作的输入摘要、原请求/意图和返回引用同一工作单元发布；重启/跨入口同意图同输入回读原事实，不再次固定目录，异输入冲突。check_source按项目/准确业务快照读取技术固定记录并检查当前变化，仅报告来源，不推导环境/加载一致。新增动作由supported_actions协商；绑定确认仅接受受控人工入口，不从代理传入confirmed=true取得可信确认。

1.12补充主责存储分册第2、5、10节的对象引用发布边界：新增记录中的 `StoredObjectRef`（project_id、digest、size、media_type、relative_path）及现有明确对象引用键 object_digest/output_object_digest/artifact_digest 必须在发布前核对真实字节。引用项目须等于事务项目，路径须为准确的 objects/<project>/<sha256>，大小不得由布尔/浮点冒充，路径各层拒绝链接；按实际安全字节核对大小和摘要。重复引用合并验证，冲突引用拒绝。content_digest、projection_digest及普通正文digest仍是内联指纹，不推测为对象。

对象缺失、损坏、跨项目、无法安全处理或无法证明引用时，本次事务不得发布current、消耗原意图或生成已提交事件。已准备的不可变材料保留供维护核对。明确核对已发布清单时重新核实本次变化记录的对象引用；正文可解析或正文摘要正确不能代替附件存在。此补充保持既有端口签名和公开DTO，不以组件检查证明真实掉电或Trae验收。

默认正常启动按存储分册第3、8节走增量检查：读取准确current、清单、必要根及其变化材料，核对发布后端和准确活动事务，不遍历无关历史JSON或全部提交台账。恢复结果须区分当前边界核对与完整历史检查；完整性维护仍扫描历史，不能把增量启动成功写成全历史无损。活动标记仅在同一已发布根的准确请求/项目/意图/提交事实核对成功并保存恢复诊断后清除；未知标记保留并阻塞，不自动重放。旧格式转换仍走可校验备份与迁移守卫。

writer_epoch 绑定持排他锁核心的生命周期准入，同一核心的短事务不递增；新核心准入才递增。默认工作单元冻结所属 epoch，在开始及持锁发布前核对当前身份。旧核心的迟到结果不得刷新身份后借用新核心准入。实例退出不得释放仍在使用的事务锁，排队者取得串行锁后重新核实准入仍有效。

同进程短写入队列最多32个准入，等待串行锁最多2秒；过载或暂忙明确返回可重试WORKSPACE_IN_USE，不无限挂起。共享工作单元在begin之前即认领状态守卫，另一线程不能替换正在准入的锁上下文；事务在所属核心线程内完成，核实未知提交也串行认领。真实外部执行/网络/模型仍在事务外。analyze_project新固定及check_source实际核对遵守源码能力暂停；原来源意图只读回准确已保存事实，不依赖新固定能力，也不把回读事实等同于当前来源核对成功。

按存储分册第2、13节，将完整清单升级为 aitest.commit-manifest/2：commit_id/generation/parent_commit/changed_records 与旧内部字段准确相等，另含 event_range、实际生产 instance_id、idempotency_result_ref、file_digests、business_change_index_root。幂等结果及必要根/变化路径均为不可变摘要引用，同清单验证后才发布current。0007迁移先备份、固定可信历史高水位、保存业务索引构建进度、核对完整引用集合；旧清单及事件不重写，不把历史工作空间UUID补称为实际核心实例。新核心业务事件使用生产核心instance_id。

业务稀疏索引按存储类型分类，一批中每条记录独立登记，键按主责五元组排序，准确记录引用同时含正文摘要；正文版本不替代仓储读取修订。上传/游标/回执/纯诊断明确排除且复用原业务根。未知组件类型保守保存为unclassified，禁止直接作为可查询的业务白名单或上传许可；新增类型须显式登记及迁移。日常读取固定根/高水位，按指定类型seek并合并有界结果，不顺读无关清单尾部。

架构文档《01-项目与计划》第 7 节末明确：

> 依赖方向：`application → domain`，外部能力一律经 `application/ports.py` 的端口。本册不直接读写业务文件，
> 记录落盘统一走《04-存储与恢复》的工作单元。

因此 B 包**不直接读写任何业务文件**，全部落盘经 A 的工作单元与端口。本文档列出 B 所需的最小端口面，
请 A 统一加入 `application/ports.py`。

**B 包不会自行修改 `application/ports.py`**（该文件的所有者是 A），仅在本文档提出签名需求。

---

## 2 B 需要保存的记录清单

依据架构文档《01-项目与计划》第 7 节"记录字段与模块归属"表：

| 记录 | 主责模块 | B 的用途 |
| --- | --- | --- |
| `LocalProject` / `LocalProjectBinding` | `domain/project/context.py` | FR01：项目身份与目录绑定（git / plain 两形态） |
| `Module` / `Dependency` | `domain/project/context.py` | FR01：模块与依赖边 |
| `Task` / `AcceptanceItem` / `Delivery` | `domain/project/context.py` | FR02：任务与标准交付说明 |
| `EnvironmentRef` | `domain/project/context.py` | FR01：环境引用 |
| `SourceSnapshot` | `domain/execution/sources.py`（**归属存疑，见第 5 节**） | FR01：源码内容身份 |
| `TemplatePack` / `CriticalPath` | `domain/planning/templates.py` | FR04：模板与关键链路 |
| `GeneratedContent` | `application/ai_assistance.py` 编排后经工作单元保存 | FR04：草稿与来源修订 |
| `RuleDraft` / `RuleVersion` | `domain/planning/rules.py` | FR05：规则版本 |
| `Case` / `CaseLink` | `domain/planning/plans.py` | FR06：用例与关联 |
| `Plan` / `AcceptanceScope` | `domain/planning/plans.py` | FR06/07：冻结计划与验收范围 |
| `PreparationRecord` | B 的应用用例（经工作单元登记） | FR07：准备意图与幂等 |
| `PreparedRun` | B 产生，C 读取 | FR07：B 的唯一对外业务交付 |

---

## 3 端口签名需求

以下为**语义需求**，具体签名形式由 A 决定。全部方法都要求支持 `expected_revision` 与幂等结果。

### 3.1 `RecordRepository`

```text
save_project(project, expected_revision) -> RevisionRef
read_project(project_id, revision) -> LocalProject        # 按准确修订读取，不返回“最新”
list_projects(project_id) -> Page[ProjectSummary]         # 列表读摘要，详情按引用读取

save_binding(binding, expected_revision) -> RevisionRef
read_binding(binding_id, revision) -> LocalProjectBinding

save_module / read_module / list_modules
save_dependency_set / read_dependency_set

save_task / read_task
save_delivery / read_delivery
save_acceptance_item / read_acceptance_item

save_environment(environment, expected_revision) -> RevisionRef
read_environment(environment_id, revision) -> EnvironmentRef

save_template_ref / read_template_ref
save_generated_content / read_generated_content
save_rule_draft / publish_rule_version -> RevisionRef
read_rule_version(rule_id, revision) -> RuleVersion

save_case(case, expected_revision) -> RevisionRef
read_case(case_id, revision) -> Case                     # 必须能按准确修订读取
save_case_link / read_case_links
save_plan(plan, expected_revision) -> RevisionRef
read_plan(plan_id, revision) -> Plan
save_acceptance_scope(scope, expected_revision) -> RevisionRef
read_acceptance_scope(scope_id, revision) -> AcceptanceScope
```

**关键要求：**

1. **按准确修订读取。** 实施方案第 3 节明确禁止 B 向 C 传"当前最新计划"。
   所有 `read_*` 必须接受显式修订参数，且不得默默回退到最新值。
2. **`expected_revision` 冲突处理。** 架构文档第 8 节：
   "跨用例必须比对 `expected_revision`；来源过期或并发编辑冲突返回**当前修订和差异提示**，
   **不自动覆盖用户编辑**。"
3. **列表读摘要，详情按引用读取。** （`AGENTS.md` 第 4 节 / 存储第 13 节有限 `QuerySpec`）

### 3.2 准备意图登记（FR07 核心）

```text
register_preparation(project_id, client_id, prepare_request_id, payload_hash, intent_id,
                     source_revisions) -> PreparationRecord
find_preparation(project_id, client_id, prepare_request_id) -> PreparationRecord | None
find_preparation_by_intent(intent_id) -> PreparationRecord | None
```

**语义要求（架构文档第 11 节）：**

- 在工作单元内按 `(project_id, client_id, prepare_request_id)` 登记 `PreparationRecord` 及 `intent_id`；
- **同一键但输入摘要不同 → 返回冲突**，不得覆盖；
- **并发完成同一准备请求时只能发布一条 `PreparationRecord`**，其余调用方取得已发布结果；
- `intent_id` 必须与准备记录**同一次提交**；
- 跨入口恢复通过 `intent_id` 或准备查询取得已有意图，**不得用新入口的 `request_id` 替代业务身份**。

### 3.3 `WorkspaceUnitOfWork`

B 的应用用例需要：

- 明确的**短事务边界**（提交点由用例决定）；
- 在**同一事务内**提交记录、引用、索引与幂等结果；
- **提交后可见性**（提交成功即对后续读取可见）；
- 事务外的外部调用（源码读取与摘要计算、模型调用）——这点由 B 保证，但需要 A 的事务不隐含持有长锁。

架构文档第 11 节：

> 准备需执行的源码读取与摘要计算在**短事务外**完成；提交时校验项目、绑定、计划、规则、模板、环境修订及
> 准备请求是否仍有效。

### 3.4 `Clock`

B 的领域对象不使用系统时间。架构文档第 9 节：`Clock` 提供记录和控制所需时间。
B 需要 `now()`；业务顺序以**提交序号**判断，不按可调整的系统时间选最新（功能文档第 5 节）。

### 3.5 `SourceSnapshotPort`、`SourceControlPort`、`ModelProvider`、`SecretPort`、`ProjectionPort`

**先厘清"端口定义归谁"与"适配器实现归谁"是两件事**（原文依据见下表"归属依据"列）：

| 端口 | 端口定义的归属 | 适配器实现 | B 的用途 | 关键约束 |
| --- | --- | --- | --- | --- |
| `SourceSnapshotPort` | **B**（《01-项目与计划》第 9 节"本册的端口"） | A（`infrastructure/`） | 建立 analysis / prepare 用途快照，按实际字节计算内容摘要 | 元数据（mtime）只是变化提示，**不证明内容相同** |
| `SourceControlPort` | **B**（同上） | A（`infrastructure/adapters/source_control.py`） | 本地 Git 元信息/差异；可选 GitHub 只读 | **plain 形态不注册、不调用**；GitHub 不可用不阻塞本地 |
| `ModelProvider` | **B**（同上） | A（`infrastructure/`） | 策略校验后的脱敏投影草案请求 | 模型**只输出草稿**；迟到响应标过期 |
| `SecretPort` | **A**（《04-存储与恢复》第 9 节"本册的端口"） | A | 按用途解析引用（模型 / 被测 HTTP / 核验数据库 / GitHub 分别授权） | 只返回引用解析结果，不向视图返回正文；不能只允许模型密钥 |
| `ProjectionPort` | **待明确**（两份分册的"本册的端口"表都未列） | A | 生成安全投影 | 源码片段默认关闭；无法安全投影则排除并显示分析缺口 |

**需要一并裁定的两处口径冲突**（不自行选一种解释，依根 `AGENTS.md` 第 1.3 节）：

1. **`Clock`**：《01-项目与计划》第 9 节与《04-存储与恢复》第 9 节**都把 `Clock` 列为本册的端口**，重复列了同一个端口。
2. **`ProjectionPort`**：B 在《01-项目与计划》第 9 节的正文里被指定为使用者，但该节"本册的端口"表未收录它；
   《04-存储与恢复》第 9 节也未列。它归谁定义、由谁实现，目前没有原文可依。

**B 的诉求**：`SourceSnapshotPort`、`SourceControlPort`、`ModelProvider` 三个端口的**协议定义由 B 提供**
（B 是其一期唯一的主要使用者），**A 提供适配器实现**；`SecretPort` 按《04》归 A，B 只作为使用者。
若 A 或组长认为端口定义应统一由 A 维护，请一并裁定，B 按裁定调整。

---

## 4 关于 `application/ports.py` 的协作方式（重要）

**现状**：C 包已在 `origin/feat/package-c-execution`（commit `d60781d`）中修改了 `application/ports.py`，
新增 `SpoolStore` 协议并为 `ExecutionPort` 补充了 4 个方法签名。改动本身符合依赖方向
（`application` 依赖 `domain` 是允许的）。

**建议的协作约定：**

1. **`application/ports.py` 保持单文件，不要拆成包。** 原因：
   `tests/architecture/test_boundaries.py` 有一条硬编码断言——

   ```python
   if relative.parts[0] == "infrastructure" and name.startswith("aitest.application"):
       assert name in {"aitest.application.ports", "aitest.application.errors"}
   ```

   拆成 `application/ports/` 会让该断言失败，属于跨包破坏性改动。

2. **各包只在自己的段落追加，不重排、不整理他人已有的类。** git 对非相邻 hunk 的合并是可靠的，
   重排会导致所有人的 PR 冲突。

3. **A 是唯一所有者与合并仲裁人。** B/C/D 需要新端口时先在本目录提需求文档，由 A 统一加入。

4. **合并顺序**：若 B 的 PR 与 C 的 PR 都动了该文件，后合并方负责解冲突并重跑
   `uv run pytest tests/architecture -q`。

---

## 5 裁定前问题与方案记录

> 本节保留裁定前的冲突、证据与备选方案供追溯。现行结论以第 10 节为准，不再按本节的“待裁定”措辞阻塞实施。

### 5.1 `SourceSnapshot` 归属

**冲突事实：**

| 来源 | 原文 |
| --- | --- |
| 架构文档《01-项目与计划》第 7 节记录表 | `SourceSnapshot` 的主责模块是 `domain/execution/sources.py` |
| 架构文档《01-项目与计划》第 1、2 节 | 源码快照的建立、内容身份、排除规则、复取依赖是 **FR01（B 包）** 的职责 |
| 需求文档 P1-FR01 | "**源码快照**：Git 保存基准提交、未提交新增/修改/删除内容及排除规则……" |
| 仓库现状 | C 包已修改 `domain/execution/sources.py`（117 行新增） |

**即：领域对象的物理位置在 C 的目录，但"何时建立快照、快照用途、内容身份规则"是 B 的职责。**

#### 5.1.1 需要裁定的具体内容（B 的主张）
B 主张按**"定义层"与"位置层"分开**处理，而不是把整个记录判给某一方：

| 层 | 内容 | B 的主张 |
| --- | --- | --- |
| **定义层** | 快照建立时机、`purpose` 取值（`analysis` / `prepare`）、选定范围与依赖闭包的选择规则、排除规则、**内容身份的计算口径**、`git`／`plain` 两形态的身份统一、复取依赖与可复取范围的登记、有效性（"变了没有"）判定 | **归 B**：这些是 P1-FR01 的内容身份职责，且与 B 的 `PreparedRun`（`InputRevisions.snapshot_revision`）直接耦合 |
| **位置层** | `SourceSnapshot`／`SourceFile` 等 **Python 类定义放在哪个文件** | **保留在 `domain/execution/sources.py`**，不移动文件（避免破坏 C 的代码、C 的测试与架构测试） |

这样处理的三个理由：

1. **C 的改动面最小**：只需**补字段**（见 5.2），不需要重构或搬迁；
2. **职责与需求一致**：需求 P1-FR01 把源码快照的建立与内容身份列在 B 的 FR 内；
3. **不产生两套实现**：位置只有一个，规则只有一套，B 与 C 各自识别出的风险（"源码身份字段可能出现两套实现"）从结构上消失。

**请 A（或组长）裁定该分工**，并同步修正架构文档中可能引起歧义的表述（第 7 节记录表与第 1、2 节的措辞）。

#### 5.1.2 支撑该主张的实测证据：C 现有字段与架构要求的差集

**实测**（`develop` = `8a72b0e`）：C 已在 `src/aitest/domain/execution/sources.py` 定义
`SourceFile` 与 `SourceSnapshot`。以架构文档《01-项目与计划》第 2 节"源码快照与检查有效性"表
要求的字段为基准，逐项比对：

| 架构文档第 2 节要求的字段 | C 现有 `SourceSnapshot` | 差集 |
| --- | --- | --- |
| `source_snapshot_id` | `snapshot_id` | 有（命名不同） |
| 仓库与基准提交（`git` 形态） | 无（只有 `binding_revision`） | **缺** |
| 或文件清单摘要（`plain` 形态） | 无（只有 `content_identity` 单值） | **缺** |
| 工作目录范围（选定路径） | 无 | **缺** |
| 排除规则 | 无 | **缺** |
| 文件清单摘要（逐文件相对路径/大小/内容摘要） | `files: tuple[SourceFile, ...]`（`relative_path`／`size`／`sha256`） | 有 |
| 差异摘要 | 无 | **缺** |
| 内容引用 | 无 | **缺** |
| 创建时间 | 无 | **缺** |
| 复取依赖与可复取范围 | 无 | **缺** |

**结论（只陈述差集事实，不评价 C 的实现）**：C 当前实现覆盖了"逐文件摘要 + 一个内容身份字符串"，
其余 **7 项架构要求字段尚未出现**。这说明该记录目前**没有单一所有者**：物理位置在 C 的目录，
而字段语义的绝大部分（`git`／`plain` 身份、排除规则、复取范围）正是 B 的主责内容。

**给 A／组长的三选一**（B 推荐第一项）：

| 选项 | 做法 | 影响 |
| --- | --- | --- |
| **甲（B 推荐）** | 定义层归 B、位置层留 C 目录；C 按 5.1.2 差集补字段，B 提供规则与判据 | 符合两份分册；C 只需补字段；一套实现 |
| 乙 | `SourceSnapshot` 整体归 B（含类定义） | 需移动 C 的代码并改架构第 7 节表，C 的现有测试与 import 需跟着改 |
| 丙 | 整体归 C，B 只提供"选哪些路径、按什么规则排除"的输入 | B 失去内容身份定义权，与 P1-FR01 对 B 的职责分配不符 |

**裁定前 B 的行为**：不新建第二套 `SourceSnapshot`，不修改 `domain/execution/`，
`git` 形态源码身份暂不实现；`plain` 形态的既有最小内容身份（`domain/project/context.py` 的 `SourceManifest`）
保持不变，等裁定后再决定它与 `SourceSnapshot` 的关系。

### 5.2 三个端口的定义归属与两处口径重复

**冲突事实（原文可核对）：**

| 端口 | 《01-项目与计划》第 9 节"本册的端口" | 《04-存储与恢复》第 9 节"本册的端口" | 现状 |
| --- | --- | --- | --- |
| `ModelProvider` | **列出（B）** | 未列 | `ports.py` 中无签名 |
| `SourceSnapshotPort` | **列出（B）** | 未列 | `ports.py` 中无签名 |
| `SourceControlPort` | **列出（B）** | 未列 | `ports.py` 中无签名 |
| `Clock` | **列出** | **列出（重复）** | 已有签名 |
| `ProjectionPort` | 正文指定 B 为使用者，**表内未列** | 未列 | `ports.py` 中无签名 |

**也就是说**：这三个端口按原文是 **B 自己的端口**，但 `application/ports.py` 的所有者按第 4 节约定是 A，
于是形成"**定义权与提交权分离**"——B 有定义权却无提交权，A 缺席时端口无法落地。

**B 的诉求（三选一，B 推荐第一项）：**

| 选项 | 做法 | 影响 |
| --- | --- | --- |
| **甲（B 推荐）** | 端口定义仍以本文件第 8 节草案为准，**由 B 在自己的 PR 内、按第 4 节"只追加本包段落"的约定加入 `application/ports.py`**，PR 内注明并请 A 事后复核 | 与 C 当年的做法一致（C 已改过两次）；不重排他人段落，冲突面小；**需要先修改 B 包 AI 规则第 2 节**（该规则现禁止 B 修改 `ports.py`） |
| 乙 | 维持现状：B 只提需求，等 A 加入 | 零风险，但 A 缺席期间 B 的 Sprint 2/5 端口类工作全部停在原地 |
| 丙 | 由组长指定专人统一维护 `ports.py` | 职责最清晰，但需要有人实际接手 |

**需要一并裁定的两处口径重复**：`Clock` 被两份分册同时列出；`ProjectionPort` 的归属两册都未列。

### 5.3 GitHub 只读：B 需要哪些远端信息（B-Q04）

需求与架构只规定"GitHub 只读为**可选**能力、绿色状态不能替代业务通过、`plain` 不注册不调用 Git 能力"，
**未规定获取方式**（`git remote` 元信息 / `gh` CLI / HTTPS API）与失败降级口径。本节给出 B 侧的最小需求面，
供 A 实现 `SourceControlPort` 适配器时对照。

**B 需要的信息（只读，共 4 类）：**

| # | 需要的信息 | B 的用途 | 缺失时的行为 |
| --- | --- | --- | --- |
| 1 | 本地 Git 元信息：仓库标识、当前分支、基准提交 | 建立 `git` 形态绑定的基准（`BindingForm.GIT` 的三个字段） | 本地 Git 也可用时正常降级为"无远端信息"，不阻塞 |
| 2 | 工作区相对基准的变更：新增/修改/删除文件清单 | 源码快照的差异摘要、变更影响与回归范围（FR03） | 保留未知，不用其他字段冒充 |
| 3 | 远端变更：目标分支相对本地的落后/领先提交 | 提示"依据可能已过期" | **不阻塞任何本地运行** |
| 4 | 远端已有检查状态（CI 结果） | 仅供报告**分开展示**，不参与判定 | 报告中标注"未获取"，**不得默认通过** |

**硬性约束（B 侧已实现/将实现）：**

- `plain` 形态**不注册、不调用**任何 Git 能力（`tests/contracts/test_project_vocabulary.py` 已锁定键的省略）；
- GitHub 绿色状态**永远不能**作为 L1 通过或业务通过的依据；
- 远端不可达时按"**可选能力不可用**"处理，与"本地 Git 不可用"分开报告；
- 凭据经 `SecretPort` 按 `GitHub` 用途解析，**不进入配置、日志、面板、导出**。

**请 A 确认**：① 获取方式（建议优先本地 `git` 元信息，GitHub 远端走 HTTPS，避免依赖 `gh` CLI 是否安装）；
② 第 3、4 类信息是否纳入一期实现范围（若纳入，B 需要适配器的返回结构）；③ 失败降级口径。

---

## 6 待 A 确认问题清单

1. 第 3 节列出的端口方法是否照单加入，还是希望 B 先提交一版签名草案？
2. `register_preparation` 的"同键异摘要返回冲突"是在端口层实现，还是由 B 的应用用例在事务内检查？
3. **`SourceSnapshot` 的归属请裁定**（第 5.1 节，B 推荐选项甲）。
4. **三个端口的定义归属与提交方式请裁定**（第 5.2 节，B 推荐选项甲）；
   并请一并明确 `Clock` 的重复列出与 `ProjectionPort` 的归属。
5. **GitHub 只读的获取方式与范围请确认**（第 5.3 节，B-Q04）。
6. `PreparedRun` 是否需要注册为查询结果类型（供 D 展示）？若需要，请给出查询入口建议。
5. 工作单元是否提供"提交后可见性"的读取入口，还是 B 需要单独的查询端口？

---

## 7 确认记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿 | B 包（待 A 回复） |

---

## 8 端口签名草案（B 包提供，待 A 采用或修正）

> **本节性质**：B 在等 `WorkspaceUnitOfWork` / `RecordRepository` / `SourceSnapshotPort` 的签名期间，
> 按第 3 节的语义需求写了一版**可直接照抄的草案**，供 A 采用或据以反驳。
> **B 不修改 `application/ports.py`**（该文件所有者按第 4 节约定是 A）。
> 项目负责人已裁定由 A 维护 `application/ports.py` 的物理文件，B 提供并确认业务语义；详见第 10 节。
> A 若采用别的形态，B 按 A 的形态改自己的应用用例，不改本节以外的既有代码。
>
> 草案遵守 A 已建立的既有约定：`application/ports.py` **保持单文件**、
> 各包只在自己的段落追加（第 4 节）。

### 8.1 建议同时放在 `application/ports.py` 的公共类型

```python
from dataclasses import dataclass
from typing import Literal

AggregateKind = Literal[
    "project", "binding", "module", "dependency_set", "task", "delivery",
    "acceptance_item", "environment", "source_snapshot", "template_ref",
    "generated_content", "rule_draft", "rule_version", "case", "case_link",
    "plan", "acceptance_scope", "preparation_record", "model_outbound_policy",
]


@dataclass(frozen=True, slots=True)
class RevisionRef:
    """一次写入产生的不可变修订引用。"""

    aggregate_kind: AggregateKind
    record_id: str
    revision: int
    digest: str


@dataclass(frozen=True, slots=True)
class CommitResult:
    """一次提交的结果。

    `commit_seq` 是**提交序号**：B 的业务顺序一律按它判断，不使用系统时间
    （功能文档第 5 节）。`created` 按 `(aggregate_kind, record_id)` 索引本次提交
    产生的修订，调用方据此拿到新修订号，不必再读一次。
    """

    commit_seq: str
    created: Mapping[tuple[AggregateKind, str], RevisionRef]


@dataclass(frozen=True, slots=True)
class Page[T]:
    """项目范围内的稳定分页；列表读摘要，详情按引用读取。"""

    items: tuple[T, ...]
    next_cursor: str | None
```

### 8.2 `WorkspaceUnitOfWork`（草案）

```python
class WorkspaceUnitOfWork(Protocol):
    """短事务边界；同一提交内保存记录、引用、索引与幂等结果。"""

    def commit_seq(self) -> str:
        """当前提交序号；已提交状态下的业务顺序依据。"""
        ...

    def stage_record(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> RevisionRef:
        """在**同一事务内**暂存一条不可变修订。

        `expected_revision` 为 `None` 表示"新建"。与当前修订不一致时抛
        `StaleRevisionError`，并带上**当前修订与差异提示**，**不自动覆盖用户编辑**。
        """
        ...

    def stage_preparation(
        self,
        record: PreparationRecord,
        *,
        payload: Mapping[str, object],
    ) -> RevisionRef:
        """登记准备记录与 `intent_id`——**必须在同一提交内**（架构文档第 11 节）。

        同一 `(project_id, client_id, prepare_request_id)`：
        摘要相同返回原记录的修订；摘要不同抛 `PreparationConflictError`
        （**不覆盖**）。
        """
        ...

    def commit(self) -> CommitResult:
        """提交并发布索引；提交成功后对后续读取可见。"""
        ...

    def rollback(self) -> None:
        """放弃本次暂存；不产生任何可见修订。"""
        ...
```

**待 A 定的三处**：

1. **谁提供实例**：`bootstrap.py` 注入，还是另有工厂？B 的应用用例需要一个入口拿到它。
2. **暂存顺序**：B 希望"先 `stage_*` 再 `commit`"，以便在提交前知道修订号（`payload_hash`
   要引用各来源修订）。若 A 采用"提交时才分配修订号"，B 需要改为两阶段写法。
3. **提交后可见性**：第 6 节第 5 问——工作单元是否提供提交后的读入口，还是 B 走
   `RecordRepository`？B 的用例倾向后者（读写分开，便于测试）。

### 8.3 `RecordRepository`（草案）

```python
class RecordRepository(Protocol):
    """不可变修订的读取与项目范围分页；**所有 read 必须接受显式修订**。"""

    # --- 项目与绑定 -------------------------------------------------
    def read_project(self, project_id: str, revision: int) -> Mapping[str, object]: ...
    def list_projects(self, *, cursor: str | None = None, limit: int = 50) -> Page[Mapping[str, object]]: ...
    def read_binding(self, binding_id: str, revision: int) -> Mapping[str, object]: ...

    # --- 环境 -------------------------------------------------------
    def read_environment(self, environment_id: str, revision: int) -> Mapping[str, object]: ...

    # --- 计划与用例 -------------------------------------------------
    def read_plan(self, plan_id: str, revision: int) -> Mapping[str, object]: ...
    def read_acceptance_scope(self, scope_id: str, revision: int) -> Mapping[str, object]: ...
    def read_case(self, case_id: str, revision: int) -> Mapping[str, object]: ...
    def read_case_links(self, case_id: str, revision: int) -> Mapping[str, object]: ...

    # --- 规则与模板 -------------------------------------------------
    def read_rule_version(self, rule_id: str, revision: int) -> Mapping[str, object]: ...

    # --- 幂等查询（按业务身份，不返回"最新"）------------------------
    def find_preparation(
        self, project_id: str, client_id: str, prepare_request_id: str
    ) -> Mapping[str, object] | None: ...
    def find_preparation_by_intent(self, intent_id: str) -> Mapping[str, object] | None: ...
```

**关键要求（对应第 3.1 节）**：

1. **按准确修订读取**：所有 `read_*` 必须接受显式修订参数，**不得默默回退到最新值**。
   "B 不向 C 传当前最新计划"这条约束就落在签名上。
2. **返回原始 payload**：B 用自己的 `application/project/serialization.py` 还原领域对象，
   因此端口不必了解 B 的领域类型（**避免端口依赖 domain 具体类**）。
   若 A 更愿意直接回领域对象，B 也接受，但需要在 `ports.py` 里 import B 的类型，请 A 判断。
3. **列表读摘要**：`list_*` 只返回摘要，详情按引用读取（第 3.1 节第 3 条）。

### 8.4 `SourceSnapshotPort`（待 A 按裁定冻结）

```python
class SourceSnapshotPort(Protocol):
    """建立与复取真实被测内容，读取源码变化；内容身份来自实际字节摘要。"""

    def pin(
        self,
        *,
        canonical_path: str,
        purpose: Literal["analysis", "prepare"],
        selected_paths: Sequence[str] = (),
        exclusion_rules: Sequence[str] = (),
    ) -> Mapping[str, object]:
        """按实际字节固定一份快照；`mtime` 只作变化提示，不证明内容相同。"""
        ...

    def materialize(self, snapshot_id: str, destination: str) -> Mapping[str, object]:
        """把已固定内容物化到指定目录；返回实际路径映射与内容摘要。"""
        ...

    def read_pinned(self, snapshot_id: str) -> Mapping[str, object]:
        """按稳定标识读取已固定快照的元数据（不重新扫描目录）。"""
        ...

    def detect_changes(self, snapshot_id: str) -> Mapping[str, object]:
        """与已固定快照比较，返回变化清单；无法证明未变时不得报"未变"。"""
        ...
```

**已裁定的类型边界**（第 10 节）：
`SourceSnapshot` 的**领域对象**在 C 的 `domain/execution/sources.py`，而**建立时机、`purpose`
取值、排除规则与内容身份计算规则**归 B。B 的 `InputRevisions.snapshot_revision` 需要把它
规约成一个整数修订号。上图草案用 `Mapping[str, object]` 回避了类型归属问题，
字段名以《一期架构01：项目与计划》的冻结语义和唯一 `SourceSnapshot` 模型为准，不再以归属未定为由暂停接线。

### 8.5 方法 → 产品要求对照

| 方法 | 对应要求 | 依据 |
| --- | --- | --- |
| `WorkspaceUnitOfWork.commit` | "记录、引用、索引与幂等结果**同一事务**提交" | 架构文档第 8 节；根 `AGENTS.md` 第 3 节 |
| `WorkspaceUnitOfWork.stage_record(expected_revision)` | "比对 `expected_revision`；冲突返回当前修订和差异提示，**不自动覆盖用户编辑**" | 架构文档第 8 节末 |
| `WorkspaceUnitOfWork.stage_preparation` | "在工作单元内按 `(project_id, client_id, prepare_request_id)` 登记 `PreparationRecord` 及 `intent_id`；**输入摘要不同返回冲突**；**与准备记录同一次提交**" | 架构文档第 11 节 |
| `RecordRepository.read_*(revision)` | "所有 `read_*` 必须接受显式修订参数，不得默默回退到最新值" | 实施方案第 3 节；本文件第 3.1 节 |
| `RecordRepository.find_preparation*` | "prepare/start 响应丢失通过同键查询返回原结果"；"跨入口恢复通过 `intent_id` 或准备查询" | 架构文档第 11 节 |
| `RecordRepository.list_projects` | "列表读摘要，详情按引用读取"；有限 `QuerySpec` | 存储与恢复第 13 节 |
| `SourceSnapshotPort.pin` | "内容身份必须来自实际字节摘要；元数据（mtime）仅作为变化提示" | B 包 AI 规则第 3.6 节；架构文档第 2 节 |

### 8.6 并发语义（第 6 节第 2 问的 B 方建议）

**建议在端口层（事务内）实现**，理由是"并发完成同一准备请求只能发布一条
`PreparationRecord`，其余取得已发布结果"（架构文档第 11 节）——这句话描述的是
**并发下的写入结果**，应用用例在事务外无法证明它。因此：

- `stage_preparation` 在同一事务内按三个身份键检查已有记录；
- 摘要相同 → 返回原记录修订，**不新建**；
- 摘要不同 → 抛 `PreparationConflictError`，携带原记录的 `intent_id`、`payload_hash`
  与 `created_at_commit`，供调用方给出明确提示；
- B 的 `decide_preparation()` 只负责**单线程下的判定与提示内容**，不承担并发保证。

### 8.7 B 已按本草案实现的规则（可独立验证，不含 I/O）

| 产物 | 位置 |
| --- | --- |
| `InputRevisions`、`PreparationRequest`、`PreparationRecord` | `src/aitest/application/planning/preparation.py` |
| `payload_hash()`（业务输入摘要；不含传输层参数） | 同上 |
| `decide_preparation()` 四态判定（`new`/`reused`/`conflicted`/`needs_reprepare`） | 同上 |
| 绑定序列化：不适用键真正省略、可往返 | `src/aitest/application/project/serialization.py` |

设计依据见 `docs/文档-feix-a/B包/10-准备意图与幂等规则设计说明.md`。
**A 的签名一旦落地，B 只需在这些规则外面加编排，规则本身不再改动。**

### 8.8 B 接线后确认需要的三个底层方法（2026-10-01）

B 已按第 8.2／8.3 节把 `application/planning/substrate_adapter.py` 从骨架实现为可用转接头，
并在 `tests/unit/test_substrate_adapter.py` 里用 A 的 `FileUnitOfWork` /
`FileRecordRepository` **真落盘**跑通 `prepare_run`（含"重启后按业务身份读回"）。

A 的现有实现已经具备其中大部分能力，但下面三项**只存在于具体实现里、没有进
`application/ports.py` 的协议**，因此 B 现在只能靠"注入什么用什么"接线，
无法在类型与合同层面确认它们会一直存在：

| 需求 | A 现状 | 为什么 B 需要它 |
| --- | --- | --- |
| `RecordRepository.current_revision(aggregate_kind, record_id) -> int` | `FileRecordRepository` 已有同名方法 | ① 修订冲突时 B 必须返回**当前修订与差异提示**（架构 01 第 8 节末），A 的 `ValueError("revision conflict")` 不带这个值；② 按业务身份查回准备记录要读"当前修订" |
| `WorkspaceUnitOfWork.commit_seq() -> str` | 无 | 准备登记的 `created_at_commit` 与"依据需重新准备"提示都要在**未提交**时读当前提交序号；`prepare_run` 的阻塞与复用分支**不暂存任何记录** |
| `WorkspaceUnitOfWork.next_commit_seq() -> str` | 无 | `PreparationRecord.created_at_commit` 必须在 `commit()` **之前**写进不可变 payload。A 的提交序号**按记录递增**，B 侧语义是"本次提交完成后会得到的序号" |

**建议签名（A 可采用或改形态）**：

```python
class RecordRepository(Protocol):
    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int: ...

class WorkspaceUnitOfWork(Protocol):
    def commit_seq(self) -> str: ...
    def next_commit_seq(self) -> str: ...
```

**兼容性**：三者都是**只读新增**，不改变任何已发布字段、记录形状或错误语义。
`FileUnitOfWork` / `FileRecordRepository` 已经持有对应事实
（`records.json` 的提交计数、按 `(kind, record_id)` 的修订条数），
补齐属于**暴露**，不是新增能力。

**B 侧的临时接法（A 冻结后移除）**：`PortsUnitOfWork` 接受可选的 `CommitSequenceSource`；
集成测试用 A 的 `RecoveryOrchestrator.inspect()["committed_sequences"]` 提供提交序号，
不访问 A 的存储内部文件。A 冻结签名后由装配点换成正式访问器，
B 的用例与测试不改。这三个方法缺失时，转接头抛 `SubstrateContractError` 并在消息里指到本节，
不用默认值顶替。

**另需一并确认的一处口径**：`commit_sequence` 是**工作空间全局**计数（`records.json` 的 `commit`），
B 目前只依赖它在**同一项目内单调**。一期若允许多项目共用一个工作空间，
`created_at_commit` 的跨项目可比性需要明确；B 不自行假定。

### 8.9 B 侧已完成的接线（2026-10-01）

| 项 | 位置 | 状态 |
| --- | --- | --- |
| 薄转接头 | `src/aitest/application/planning/substrate_adapter.py` | 已实现；`application` 层不 import `infrastructure`，底层由装配点注入 |
| 真实存储集成测试 | `tests/unit/test_substrate_adapter.py` | 14 项通过（真落盘 + 重启读回 + 修订冲突 + 索引缺失显式报维护） |
| 准备记录身份与落盘形状 | `src/aitest/application/planning/preparation.py` | 记录标识/意图标识由三元组派生摘要，**带项目与客户端命名空间** |
| 摘要口径 | `src/aitest/application/planning/prepare_run.py` | 摘要键集合与 `PAYLOAD_FIELDS` 逐字一致，有对照测试 |

**仍未接通**：B 的用例**进入产品统一入口**还缺装配点改造——`bootstrap.CoreBootstrap.create()`
目前只把 `FileUnitOfWork` 交给 `LocalAPI(transaction_port=...)`，
`register_use_cases` 注册的 handler 拿不到工作单元与只读仓储
（`Handler = Callable[[Command], Mapping]`，没有依赖注入）。
装配点归 A；本包不修改该文件。所需的端口面见第 3 节。

### 8.10 B 的用例已可经统一入口运行（2026-10-01，B 侧自证）

上一节的"仍未接通"**在本轮被绕过一步，但没有被取消**：B 改成
**在注册时把依赖闭包进 handler**，因此不需要 A 先改 `Handler` 签名也能跑通。实测如下。

| 项 | 位置 | 状态 |
| --- | --- | --- |
| 动作表与依赖包 | `src/aitest/application/usecase_registry.py`（新增） | `BUseCaseDependencies` + `build_b_use_case_registry()`；**不 import** `bootstrap` / `interfaces` / `infrastructure` |
| 注册到入口 | `src/aitest/interfaces/local/b_registration.py`（新增） | `register_b_use_cases(api, deps)`：把 B 的动作并进 `LocalAPI.handlers`，同名动作**拒绝覆盖** |
| 合同测试 | `tests/contracts/test_b_use_case_registration.py`（新增，10 项通过） | 用**真实 `Command` + 真实 `FileUnitOfWork`** 经 `LocalAPI.dispatch()` 写入并读回；含重启读回、只读动作免写身份、修订冲突、索引待重建、参数非法五类反例 |

本轮暴露并已固定的动作（**仅项目上下文类**）：

| 动作 | 语义 | 记录类别 |
| --- | --- | --- |
| `save_context` | 保存项目（模块随项目一起） | `project` |
| `save_binding` | 保存 Git/plain 绑定 | `binding` |
| `save_environment` | 保存环境引用 | `environment` |
| `save_dependency_graph` | 保存模块依赖图 | `dependency_set` |
| `query` | 有界查询（读动作） | — |

错误码（由 `BUseCaseError.code` 透到 `Response.error.code`）：
`B_INVALID_PARAMETER`、`B_REVISION_CONFLICT`、`B_PREPARATION_CONFLICT`、
`B_INDEX_MAINTENANCE_REQUIRED`、`B_INVALID_QUERY_CURSOR`。

两处细节值得 A/C/D 知悉：

1. **写动作的成功结果只报 `aggregate_kind` / `record_id` / `revision`，不报提交序号**。
   原因：`application/project/persistence.py` 的 `save_*` 自带 `open`/`commit` 并只返回
   `StagedRevision`，返回时提交序号已经前进，事后补读会拿到**下一次**的序号。
   B 选择少报一个字段，而不是报一个会误导"业务顺序"的值。若将来需要随写返回提交序号，
   接口形态需要改（由拥有 `save_*` 语义的一方决定），B 不在本轮自行发明。
2. **`query` 在索引缺失时返回 `B_INDEX_MAINTENANCE_REQUIRED`，不返回空列表**。
   实测依据：`PortsRecordReader.query()` 把 A 的 `status=maintenance_required` 翻成
   `IndexMaintenanceRequired`；若直接透传会变成 `INTERNAL_ERROR`，
   调用方无法把"索引待重建"与"真的没有数据"分开。

**两项仍然只归 A，本包不动**：

1. **装配点接线**：产品路径目前是 `CoreBootstrap.create()` 一次性装配；
   要让 B 的动作在**跨进程唯一核心**里也生效，仍需装配点把
   `BUseCaseDependencies` 交给 `register_b_use_cases()`。本包不修改 `bootstrap.py`。
2. **`Handler` 依赖注入（可选）**：若 A 愿意把 `Handler` 扩成可收依赖，
   B 的改动只是把"注册时闭包"换成"装配时注入"，动作表与测试不变。
   本包不主张必须改签名——现有形态已经可用。

**尚未包括**：`publish_rules`、`publish_plan`、`generate_draft`、
模型出站类动作。它们各自需要参数字段的适配（`PreparationInputs` 有二十余个字段），
另行分批，不在此节声称已接通。

### 8.11 `prepare_run` 已进统一入口；B 侧交付说明另立文件（2026-10-02）

- `prepare_run` 的**参数适配层已实现并注册**（`usecase_registry.py` 的
  `_preparation_inputs()` / `handle_prepare_run()`）：键名与 `PreparationInputs` 逐字一致，
  成功返回 `PreparedRun.model_dump(mode="json")`，与 `BC-001` 同一套字段。
  合同测试 `tests/contracts/test_prepare_run_entrypoint.py`（**10 项**）。
- **一条需要 A 注意的实测事实**：`prepare_run` **已经依赖提交序号**——
  其阻塞分支与 `created_at_commit` 都要在未提交时读 `commit_seq` / `next_commit_seq`；
  缺少注入时转接头抛 `SubstrateContractError`。也就是说第 8.8 节那三个方法
  **不再是"将来才需要"，而是准备链路的现行前置**。
- B 侧的实现／验证／缺口按 `接口对接/AGENTS.md` 第 4 节单独登记在
  [`delivery-B.md`](delivery-B.md)，本文件不再重复。

### 8.12 保存语义收紧：项目归属统一校验、发布核对预期修订（2026-10-03）

对应 `docs/一期工程检查-B包.md`（2026-10-03 版）的 **B-12** 与 **B-14**，
逐条反例见 `docs/一期端到端深入检查-2026-10-03.md` 第 4 节。**本节只登记 B 侧语义变化，
不改任何跨包 Schema 字节，不需要 A/C/D 改代码**；以下两处行为变化供 A/C/D 核对。

**① 项目归属：写入与读取都要求正文自带项目，且必须一致（B-12）**

- 入口侧：`save_delivery` / `save_task` 在构造领域对象之前比对正文 `project_id` 与
  命令 `project_id`，不一致报 `B_INVALID_PARAMETER`。
  **过去的实际行为是用命令项目覆盖正文项目再落盘**——调用方以为存的是 B 的记录，
  实际落成了 A 的。这是本轮修掉的主要问题。
- 落盘形状：`Delivery` 的 payload 现在**自带 `project_id`**（与 `case` /
  `acceptance_scope` / `rule_draft` 一致）；`delivery_to_payload()` 的调用方需要传项目。
- 读取侧：`_load_payload` 对"正文缺 `project_id`"与"正文项目不一致"**都拒绝**。
  依据是检查文档"旧记录归属未知应显式阻塞或迁移"，
  **不再把"没有项目"读成"任何项目都能读"**。
- 模型出站：`request_model_draft()` 在准入之前比对 `policy.project_id` 与 `project_id`，
  不一致直接阻塞，**不调用供应方、不落出站记录**。

> **A 侧范围说明**：本项只做 B 侧的准入与引用校验。
> 检查文档同时要求"A 守住持久命名空间"（即 **A-11** 的跨项目底层归属），
> 该条属 A 的主责范围，不在本合同的实现与交付之内。

**② 发布核对调用方声明的预期修订（B-14）**

- `publish_rules()` / `publish_plan()` 新增 `expected_revision` 参数；
  入口从 `Command.expected_revision` 原样透传（`0` = "我认定这是新建"）。
- 校验在**同一次事务内**完成（新增 `publish._revision_to_stage()`）：与当前修订不符即
  `ConcurrentEditError`（携带当前修订），入口翻成 `B_REVISION_CONFLICT`。
  过去发布自己读当前修订当 `expected_revision`，**等于替调用方接受最新基线**，
  于是旧编辑也能发布成功、旧正文成为最新发布版。
- **调用方行为变化**：第二次及以后的发布必须声明"我看到的是 `@N`"。
  不声明（`0`）而当前已有记录时会被拒绝——这是刻意行为，不是回归。
- 入口**不回退**到草稿/计划自己的修订号来"补"一个基线：
  那仍然是替调用方猜基线，正是本项要消除的行为。

### 8.13 模型出站：记录标识规则与落盘前过滤事实（2026-10-03）

对应 `docs/一期工程检查-B包.md`（2026-10-03 版）的 **B-10** 与 **B-03**；
实现与实测见 `docs/修改日志/feix-a/2026-10-03-B包模型生成意图与落盘前过滤.md`。
**本节不改端口签名、不改跨包 Schema 字节**，登记以下三处供 A/C/D 核对。

**① 出站记录的标识规则多了一种（B-10）**

- 过去：`outbound:{project_id}:{policy_revision}:{task_type}`
  ——两次不同业务意图会**共用同一条记录**。
- 现在：调用方给出 `generation_request_id` 时用
  `outbound:{project_id}:{generation_request_id}`；
  **不给时沿用旧规则**，因此既有记录与既有行为仍可复现。
- 影响面：出站记录是**B 自己的聚合类别**（`model_outbound_request`），
  存储层不需要为它新增索引键；A 侧无改动。

**② 出站结果的 payload 多了两个键（B-10 / B-03）**

| 键 | 含义 |
| --- | --- |
| `generated_content_id` / `generated_content_revision` | 复用分支按引用读回**原草稿**的依据 |
| `credential_filter` | `{policy, replacements, filtered}`：落盘前过滤掉了几个已知凭据。**只有计数与策略版本，没有凭据原值，也没有其摘要** |

意图修订的 payload 同时多一个 `generation_identity`（"同键是否同输入"的判定依据）。
三个键都是**新增可选键**，旧消费方忽略即可；`credential_filter` 在模板生成路径**不出现**。

**③ 调用方需要认识的新状态与参数（B-10）**

- `request_model_draft()` 新增 `generation_request_id`、`known_credentials` 两个参数；
- `OutboundOutcome` 新增状态 **`OUTBOUND_UNRESOLVED`**：同一业务请求号已有出站意图、
  但结果未提交（响应丢失）。此时**不重发**——外部调用不可撤销，
  由调用方先核对原出站事实，再用新的 `generation_request_id` 明确重新生成。
  在 D 侧（面板/CLI/MCP relay）接入该状态之前，它只在核心与测试层可见。

**仍归 A 的部分**：真实 `ProjectionPort` / `SecretPort` / `ModelProvider` 接线；
"哪些值算已知凭据"的来源解析。本包只在既有端口协议内实现编排语义，
`known_credentials` 由调用方给出，集合不全时过滤必然不全（已如实登记为缺口）。

### 8.14 保存语义：正文修订必须等于分配的仓储修订（2026-10-03）

对应 `docs/一期工程检查-B包.md`（2026-10-03 版）的 **B-11**
（`B-REVISION-01-payload-storage`）。**不改端口签名与跨包 Schema 字节**。

**问题**：`case` / `acceptance_scope` 的落盘过去把 `expected_revision` 交给底座当
**仓储修订**，而正文 payload 里写的是对象自己的 `revision`，两者从不比对。
于是"新建一条 `revision=9` 的用例"会落成**记录 `@1`、正文 `@9`**：
按记录修订读回得到另一个修订号，按正文修订又读不到东西。

**现在的规则（B 侧不变量）**：

```
正文修订 == 这次会分配的仓储修订 == (expected_revision or 0) + 1
```

- 适用：`save_case()`、`save_acceptance_scope()`；
- 不符时抛 `ConcurrentEditError`，入口透出 **`B_REVISION_CONFLICT`**；
- 校验在事务内、`stage_record` 之前完成，因此底座的并发校验**先生效**，
  本核对只处理"修订号本身的错配"。

**调用方行为变化**：把一条正文修订不等于"当前修订 + 1"的 `Case` / `AcceptanceScope`
直接保存会被**拒绝**。要新增修订必须按顺序递增（`@1` 新建、随后 `expected_revision=1`
写 `@2`），不能跳号或复用旧修订号。

**未纳入（待定口径）**：`rule_draft` 与 `plan` 不适用本条——它们的修订号在
**导入**路径上来自外部规则包（`portable.import_rule_payloads()` 原样还原文件里的
`revision`），`publish_plan` 的 `plan` 记录 payload 修订也来自计划对象、
仓储修订是独立序号。是否需要一致属设计决定，见
`docs/文档-feix-a/B包/09-待解决问题清单.md` 第 7 节的待定问题。

### 8.15 新增两个规则 Markdown 出口动作（2026-10-03）

对应 `docs/一期工程检查-B包.md`（2026-10-03 版）**B-04** 末句
"已有 JSON 往返尚未实现合同的规则 Markdown 导入导出"。**不改端口签名与跨包 Schema 字节**。

**B 的动作表由 17 增至 19**，新增两个动作（与既有 `export_rules` / `import_rules` 对称）：

| 动作 | 参数 | 结果 |
| --- | --- | --- |
| `export_rules_markdown` | `rule_versions`（同 `export_rules`） | `documents`：每项 `{rule_id, revision, markdown}` |
| `import_rules_markdown` | `markdown`：单份字符串或字符串列表 | `imported`：与 `import_rules` 同一形状（`rule_draft` 落盘结果） |

**两条行为约定**：

1. **导入恒为草稿**：与 `import_rules` 同一口径——不接受 `confirmed` / `enablement`，
   产出的 `rule_draft` 恒为未确认、未启用；
2. **Markdown 不承载本地发布追溯**：`export_rule_version()` 另带的
   `published_confirmation_id` / `published_digest` **不进 Markdown**。
   它们是"某个实例已发布"的本地事实，而 Markdown 是给人编辑、可跨实例搬的格式。

**方言**：写入 `application/planning/rules_markdown.py` 的模块 docstring（唯一权威）。
要点：文首一级标题承载 `rule_id @revision`；固定三行元数据；
正文 / 步骤 / 证据要求 / 未识别字段各成一段；空列表写 `_（无）_`；
未识别字段放 ```json 围栏块。**不引入 YAML 依赖**（`pyproject.toml` 无 YAML，
新增依赖属第③级决定），因此不用 front matter。

**给 D 的知悉项**：面板/CLI/MCP relay 若要暴露"以 Markdown 导入导出规则"，
可直接调用这两个动作；方言由本包定义，D 侧不做第二套渲染。

### 8.16 模型出站与运行修订：端口现状、装配与逐条待确认项（2026-10-03）

对应 `docs/一期工程检查-B包.md`（2026-10-03 版）**B-01**（"模型与运行修订动作未注册"）
与 **B-03／B-10** 的真实端口部分。**本节不改端口签名**，只登记实测现状与要 A 确认的事项。

#### 8.16.1 实测现状（2026-10-03，`develop 7563aeb`）

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| B 的三个只读方法 | `application/ports.py` 第 191／198／222 行已有 `commit_seq` / `next_commit_seq` / `current_revision` | **第 8.8 节的待冻结项已完成**，B 的转接头可直接用 |
| A 的三个模型端口签名 | `ports.py` 第 362／368／423 行有 `ModelProvider` / `ProjectionPort` / `SecretPort` | **已有** |
| 类型是否两套 | A 的 `ports.py` 第 10—25 行**直接 import B 的 `model_ports` 类型**（`ModelCall as ModelCall` 等） | **同一套类型**，不存在两套同义定义 |
| A 的适配器实现 | `infrastructure/projections.py`（`SafeMaterialProjector.project`）、`infrastructure/adapters/model.py`（`HttpModelProvider.call`）、`infrastructure/credentials.py` | **已有实现** |
| 默认装配 | `bootstrap.py` 第 243 行只构造 `BUseCaseDependencies(unit_of_work, reader, clock)`；**未注入任何模型端口** | **装配缺口在此** |
| B 的动作注册 | 注册表当前 17 个动作（`save_context`…`query`）；**没有** `save_confirmation`、`request_model_draft`、`revise_pending_steps`／`narrow_driver` | **注册缺口** |

#### 8.16.2 三处需要 B 适配、其余无差异（实测对比）

| B 的窄协议 | A 的端口／适配器 | 差异 |
| --- | --- | --- |
| `MaterialProjector.project(*, material: Mapping[MaterialKind, str], source_snippets_enabled) -> Projection` | `SafeMaterialProjector.project(...) -> Projection`（**同一 `Projection` 类**） | **无** |
| `ModelCaller.call(request: ModelCall) -> ModelCallResult` | `HttpModelProvider.call(request: ModelCall) -> ModelCallResult`（**同一套类**） | **无** |
| `CredentialResolver.resolve(*, purpose: str) -> CredentialResolution` | `SecretPort.resolve(reference: str, *, purpose: str) -> ResolvedSecret` | **有**（见 8.16.3 第 3 条） |

#### 8.16.3 要 A 确认／决定的事项（逐条）

| # | 事项 | 现状 | 请 A 确认什么 |
| --- | --- | --- | --- |
| 1 | **把三个模型端口注入默认装配** | `bootstrap` 只注入 `unit_of_work`/`reader`/`clock` | A 把 `ProjectionPort`／`ModelProvider`／凭据解析实现注入 `BUseCaseDependencies`（或给出等价装配位置）。B 侧只消费注入对象，不改 `bootstrap.py` |
| 2 | **模型动作与运行修订动作注册进统一入口** | 注册表无这三个动作 | 确认由谁把 `request_model_draft`、`revise_pending_steps`／`narrow_driver` 的 handler 接进入口并做能力声明。B 提供 handler，装配归 A（B-01 的"装配点由谁改"） |
| 3 | **凭据解析的形状冲突（最关键）** | B 调 `credentials.resolve(purpose="model")`，要"只有状态、永不回传正文"；A 的 `SecretPort.resolve(reference, *, purpose) -> ResolvedSecret`（`ports.py` 第 423 行）**要求 `reference`，B 的编排从不提供**，且返回类型是 `ResolvedSecret` 而不是 B 的 `CredentialResolution` | **A 决定收敛方式**，B 给两个候选：**（甲）** A 在装配处提供窄适配器，把 `SecretPort` 包成 B 的 `CredentialResolver` 形状（`reference` 由装配方按用途配置，B 侧仍只拿到状态、拿不到 `ResolvedSecret`）；**（乙）** 把 `CredentialResolver` 提升为公共合同并进 `ports.py`（破坏性变更，走完整流程）。**B 倾向甲**：不动 A 的端口签名，且 `ResolvedSecret` 不进入 B 的应用层类型 |
| 4 | **`capabilities` 的能力声明** | 未声明模型类动作 | A／B 谁改共享的 `contracts/capabilities.py`：确认后由能改的一方加，不两边同时改 |

**B 侧已具备**（供 A 判断对接成本）：`application/planning/model_orchestration.py` 的
`request_model_draft()` 已把准入→凭据→投影→调用→登记串好，并有内存实现验证；
`application/planning/run_mode.py` 的 `request_runtime_revision()` 已组装运行修订的领域入参。
两处的依赖面就是上表的三个协议。

**B 侧明确不做**：不改 `bootstrap.py`、不改 `contracts/capabilities.py`、
不改 `application/ports.py`、不自行执行真实供应方调用。

#### 8.16.4 运行修订的落盘与消费（B→C）

`RuntimeRevision` 未持久写入实际运行序列、C 的 runner 未消费接受／失效清单
（检查文档 **B-05**）。B 侧已给出领域门禁与决策结果
（`RuntimeRevisionDecision` 的 `affected`／`preserved`／`invalidated_basis`／
`rejudge`／`confirmation_required`／`pause_required`）；**保存与消费归 C**。
该条已追加到 `已完成/BC-001`（B↔C）合同（见其第 16 节）。

---

## 9 变更记录

| 日期 | 版本 | 变更 | 确认方 |
| --- | --- | --- | --- |
| 2026-09-24 | 0.1 | 初稿：记录清单、端口语义需求、协作约定、`SourceSnapshot` 归属冲突 | B 包（待 A 回复） |
| 2026-09-28 | 0.2 | 补第 8 节：**可直接照抄的端口签名草案**（公共类型、三个 Protocol、方法→要求对照、并发语义建议）；补第 8.7 节 B 已实现的纯规则部分 | B 包（待 A 采用或修正） |
| 2026-09-28 | 0.3 | 第 5 节拆为 5.1 `SourceSnapshot` 归属（补 B 主张与**字段差集实测证据**）、5.2 端口定义归属与 `Clock`／`ProjectionPort` 口径重复、5.3 **GitHub 只读 B 侧需求（B-Q04）**；修正第 3.5 节端口归属记述 | B 包（待裁定） |
| 2026-09-28 | 0.4 | 正文的提出方／接收方／确认方统一改用**包名**（不使用成员名），与本目录其余文档一致 | B 包 |
| 2026-09-30 | 0.5 | 项目负责人裁定 `SourceSnapshot` 分工、端口维护方式、横切端口归属及 Git/GitHub 一期边界；三项由待裁定转为待实现 | 袁（项目负责人） |
| 2026-10-01 | 0.6 | 补第 8.8 节：B 接线后确认需要 A 冻结的**三个只读方法**（`current_revision` / `commit_seq` / `next_commit_seq`）及缺少时的行为；补第 8.9 节记录 B 侧已完成的接线与**仍未接通的产品入口**。本节只提需求，不改 B 侧协议 | B 包（待 A 确认并冻结） |
| 2026-10-01 | 0.7 | 补第 11 节：**`SourceSnapshot` 字段口径**（B 主责，按第 10.1 节裁定给出字段、形式互斥、内容身份与失效判据），供 C 评审执行兼容后在其唯一模型里落地。**只冻结字段语义，不改变任何现行 Schema 字节**。本节内容于 2026-09-30 写成于 `feat/b-sourcesnapshot-fields`，该分支未及时提交评审；现基于当前 `develop` 重新施加 | B 包（待 C 评审） |
| 2026-10-01 | 0.8 | 补第 8.10 节：B 把依赖**闭包进 handler**，因此不必先等装配点改造即可经统一入口运行项目上下文类动作；登记 5 个动作、5 个错误码、合同测试 10 项，并说明"写动作不报提交序号"与"索引缺失不返回空列表"两处细节。装配点接线与 `Handler` 依赖注入仍归 A | B 包（知悉性登记，待 A 确认装配点接法） |
| 2026-10-02 | 0.9 | 补第 8.11 节：`prepare_run` 参数适配层已实现并注册（合同测试 10 项）；**登记"准备链路已依赖提交序号"这一实测事实**——第 8.8 节三个方法由"将来需要"变为现行前置。B 侧交付说明另立 `delivery-B.md`，本节不重复 | B 包（知悉性登记，待 A 确认接法与冻结签名） |
| 2026-10-02 | 1.0 | 第 11 节标题明确为**最终口径**：该口径按第 10.1 节裁定写成，属 B 主责范围内的字段定义，可据以实施 | B 包（字段口径已定；待 C 回写 Q1／Q2 兼容性并按新契约 PR 实施） |
| 2026-10-03 | 1.1 | 补第 8.12 节：保存语义收紧——**项目归属统一校验**（写出/读入都要求正文自带项目且一致，`Delivery` payload 新增 `project_id`；旧记录缺归属显式拒绝）与**发布核对调用方 `expected_revision`**（入口透传、同事务校验、不符报 `B_REVISION_CONFLICT`；第二次发布须声明 `@N`）。对应检查文档 2026-10-03 版 B-12／B-14。**不改跨包 Schema 字节，不需 A/C/D 改代码**；A-11 的底层命名空间归属仍归 A | B 包（知悉性登记） |
| 2026-10-03 | 1.2 | 补第 8.13 节：模型出站——**出站记录标识多一种规则**（给 `generation_request_id` 时按"（项目, 业务请求号）"，不给则沿用旧规则）、**结果 payload 新增 `generated_content_id` / `credential_filter`、意图 payload 新增 `generation_identity`**（均为新增可选键，**无凭据正文或摘要**）、**新增 `OUTBOUND_UNRESOLVED` 状态**与两个新参数。对应检查文档 2026-10-03 版 B-10／B-03。**不改端口签名与跨包 Schema 字节**；真实 `ProjectionPort` / `SecretPort` / `ModelProvider` 接入与凭据来源解析仍归 A | B 包（知悉性登记） |
| 2026-10-03 | 1.3 | 补第 8.14 节：保存语义——**`case` / `acceptance_scope` 的正文修订必须等于这次分配的仓储修订**（`expected_revision + 1`），不符报 `B_REVISION_CONFLICT`；**调用方不能再跳号或复用旧修订号**。对应检查文档 2026-10-03 版 B-11。**不改端口签名与跨包 Schema 字节**；`rule_draft` 与 `plan` 未纳入，待定口径登记在待解决问题清单第 7 节 | B 包（知悉性登记） |
| 2026-10-03 | 1.4 | 补第 8.15 节：**新增两个规则 Markdown 出口动作**（`export_rules_markdown` / `import_rules_markdown`，动作表 17 → 19），登记两条行为约定（导入恒为草稿、Markdown 不承载本地发布追溯）与方言要点（不引入 YAML 依赖）。对应检查文档 2026-10-03 版 B-04 末句。**不改端口签名与跨包 Schema 字节**；方言由 B 定义，D 侧不做第二套渲染 | B 包（知悉性登记） |
| 2026-10-03 | 1.5 | 补第 8.16 节：**模型出站与运行修订的端口现状、装配与逐条待确认项**（对应 B-01／B-03／B-10 的真实端口部分）。实测登记：B 的三个只读方法**已冻结**、A 的三个模型端口**已有签名与适配器**、类型经 `ports.py` **共用同一套**；缺口在**默认装配**与**动作注册**，另有**凭据解析形状冲突**（B 要"只有状态"，A 的 `SecretPort.resolve` 要求引用且返回明文）给出甲乙两案。同步更新第 11.7 节（B 侧 `content_identity` 落地与 Q3 迁移说明）。**本节不改端口签名** | B 包（待 A 逐条确认） |
| 2026-10-03 | 1.6 | **第 11.5 节由"待 C 确认"改为"C 侧执行兼容性结论（已回写）"**：Q1 `plain` 必须真正省略 Git 键（C 按"键不存在"处理）、Q2 纯新增不复制第二套模型且主版本待 Q3 定；新增 **第 11.7.1 节函数接口规格**（模块／输入类型／规范字节／返回／异常／版本标识 `SOURCE_CONTENT_IDENTITY_VERSION`）与 **第 11.7.2 节 `content_digest` 前缀口径**（实测裸十六进制与带前缀会算出**不同身份**，故统一为构造处加 `sha256:` 前缀）。**不改端口签名与跨包 Schema 字节；代码仅新增一个版本常量** | B 包（待 C 落地） |

---

## 10 项目负责人裁定（2026-09-30）

裁定人：袁（项目负责人）。以下结论覆盖第 5、6、8 节中对应的“待裁定”或备选方案表述。

### 10.1 `SourceSnapshot`

- 采用第 5.1 节方案甲：建立时机、`purpose`、选定范围与依赖闭包、排除规则、内容身份算法、Git/plain 身份、复取范围和失效判据由 B 的《项目与计划》主责。
- `SourceSnapshot` / `SourceFile` 类继续唯一放在 `domain/execution/sources.py`，不搬迁、不复制；C 只消费冻结快照并维护实际执行来源核对事实。
- A 实现 `SourceSnapshotPort`、物化与持久化适配；B 提供字段/判据，C 评审执行兼容。现有缺失字段是待实现项，不再是归属问题。

### 10.2 端口定义与提交

- `application/ports.py` 保持协议唯一来源并由 A 维护物理文件、处理合并冲突；业务包提交完整签名草案并确认业务语义，A 确认基础设施可实现性。A 无法及时维护时只能由项目负责人显式指定代维护人，禁止另建同名 Protocol。
- 属于一期的端口必须在一期冻结方法签名、实现和合同测试。`stage_preparation` 所表达的同键幂等/冲突和同次提交语义属于一期；允许复用通用工作单元原语，不强制保留同名专用方法。
- `Clock` 和 `ProjectionPort` 的唯一技术归属是总体架构第 12 节与 `application/ports.py`。各业务分册只定义使用场景或冻结策略；A 维护相应端口及基础设施实现。
- 合并顺序为：业务合同确认 → A 写入端口 → 适配器与调用方接线 → 架构/合同/故障测试。多个端口改动按合同确认顺序合并，后合并方负责解冲突和复测。

### 10.3 Git 与 GitHub

- 本地仓库身份及工作区变更清单是一期 `git` 形态必需能力；由受控 `git` CLI 取得并按所需命令做能力探测，不冻结无证据的最低版本号。
- 远端领先/落后和远端检查状态是一期可选 GitHub 能力，不作为一期本地闭环的完成阻塞；统一通过 HTTPS API 获取，不依赖 `gh` CLI，凭据只经 GitHub 用途的 `SecretRef` / `SecretPort`。
- 本地 Git 不可用会阻塞依赖 Git 身份的固定/准备且不得静默转成 `plain`；远端未配置、未认证、限流、网络失败或服务不可用只降级远端状态，不阻塞本地固定、准备和运行。
- 远端状态只分开展示，永不直接产生 L1 或业务通过；`plain` 不注册、不调用 Git 能力，也不出现仓库字段。

---

## 11 `SourceSnapshot` 字段口径（B 主责，本节为最终口径）

依据第 10.1 节裁定，`SourceSnapshot` 的**建立时机、`purpose`、范围、排除规则、内容身份算法、Git/plain 身份、
复取范围与失效判据**由 B 主责。本节给出**冻结字段口径**，由 C 在 `domain/execution/sources.py` 落地
（类位置不变）。

**本节只冻结字段语义，不改变任何现行 Schema 字节。** 落地前 `SourceSnapshot` 仍按现状运行。
本口径按第 10.1 节裁定写成，属 B 主责范围内的字段定义，**无需另行确认即可据以实施**。

### 11.1 形式互斥（与 `LocalProjectBinding.bind_form` 同一模式）

`SourceSnapshot` 采用与项目绑定**完全相同**的形态互斥规则，不引入第二种表达方式：

| `source_form` | 必须存在 | 必须省略（**不是 `null`、不是空串**） |
| --- | --- | --- |
| `git` | `git_base_commit`、`git_diff_digest` | `plain_manifest_digest` |
| `plain` | `plain_manifest_digest` | `git_base_commit`、`git_diff_digest` |

依据：B 包 AI 规则第 3.6 节"`plain` 形态完全省略 Git 字段，不使用 `None`、空值或'未知'占位"；
序列化时**真正省略该键**（`application/project/serialization.py` 已实现该行为，并有 `key not in payload` 断言）。

### 11.2 冻结字段表

| 字段 | 类型 | 必填 | 含义与判据 |
| --- | --- | --- | --- |
| `snapshot_id` | `str` | 是 | 稳定标识；同一逻辑快照的重新固定产生**新** `snapshot_id`，不复用 |
| `project_id` | `str` | 是 | 归属项目 |
| `purpose` | `Literal["analysis", "prepare"]` | 是 | **取值只有这两个**；`analysis` 用于显式分析，`prepare` 用于准备运行 |
| `binding_revision` | `int` (≥1) | 是 | 固定时的绑定修订，用于判"绑定已变" |
| `source_form` | `Literal["git", "plain"]` | 是 | 决定下列形态字段的**存在性** |
| `selected_paths` | `tuple[str, ...]` | 是 | 工作目录范围（选定路径）；相对路径，POSIX 分隔符，不得为空 |
| `exclusion_rules` | `tuple[str, ...]` | 是 | 排除规则；无排除时为空元组（**合法**，不写 `null`） |
| `files` | `tuple[SourceFile, ...]` | 是 | 逐文件 `relative_path`／`size`／`sha256`；路径唯一、无盘符、无 `..` |
| `content_identity` | `str` | 是 | 见 11.3 的计算口径 |
| `plain_manifest_digest` | `str \| None` | `plain` 必填 | 文件清单摘要；**取值来自 B 的 `SourceManifest.manifest_digest`**，不另起算法 |
| `git_base_commit` | `str \| None` | `git` 必填 | 基准提交 |
| `git_diff_digest` | `str \| None` | `git` 必填 | 工作区相对基准的未提交新增/修改/删除内容摘要 |
| `content_ref` | `str \| None` | 否 | 内容引用（对象摘要）；内容未留存时为 `None` 并同时登记缺口 |
| `created_at` | `datetime \| None` | 否 | 创建时间；经 `Clock` 取得，不使用系统时间 |
| `refetch_dependencies` | `tuple[str, ...]` | 是 | 复取依赖（仓库对象、LFS、子模块）；无则为空元组 |
| `refetch_scope` | `str \| None` | 否 | 可复取范围；**缺失即为缺口**，不留空冒充完整 |

**与 B 既有实现的关系**：`plain` 形态的身份值以 `domain/project/context.py` 的 `SourceManifest`
（`source_scope`／`manifest_digest`／`files`／`exclusion_rules`／`refetch_dependencies`／`refetch_scope`）为准，
本节**不新定义第二套**；`SourceSnapshot` 是其上游的不可变固定事实。

### 11.3 `content_identity` 计算口径

1. **输入**：`source_form`、`files`（按 `relative_path` 升序规范化后）与形态身份
   （`git`：`git_base_commit` ＋ `git_diff_digest`；`plain`：`plain_manifest_digest`）。
2. **规范字节**：对每个文件按 `relative_path`、`size`、`sha256` 生成规范记录行，按路径升序拼接；
   再拼入形态身份；最后取摘要。
3. **不使用**：文件系统时间戳、绝对路径、盘符、目录遍历顺序、`purpose`、`snapshot_id`。
   `mtime` **只作变化提示，永不参与身份计算**。
4. **跨平台**：路径判定必须使用 `PureWindowsPath`；**入记录的路径统一为 POSIX 相对路径**，
   避免同一内容在 Windows／Linux 上算出不同身份。
5. **可复现**：同一输入必须得到同一 `content_identity`；夹具与测试据此做逐字节断言。

### 11.4 `SourceSnapshotPort`（B 提供语义，A 实现）

端口方法的**语义**如下（具体签名形式由 A 按第 10.2 节冻结；B 不自行改 `application/ports.py`）：

```text
pin(canonical_path, purpose, selected_paths, exclusion_rules) -> SourceSnapshot 的 payload
    按实际字节固定；mtime 只作变化提示，不证明内容相同
read_pinned(snapshot_id) -> payload
    按稳定标识读取已固定快照的元数据（不重新扫描目录）
materialize(snapshot_id, destination) -> 实际路径映射与内容摘要
    物化到指定目录；物化副本不进入永久对象库
detect_changes(snapshot_id) -> 变化清单
    无法证明"未变"时不得报"未变"
```

### 11.5 C 侧执行兼容性结论（2026-10-03 已回写）

| # | 事项 | C 侧结论（2026-10-03） | 后续 |
| --- | --- | --- | --- |
| Q1 | §11.1 形式互斥是否符 C 对 `plain` 的解析预期 | **符合**。`plain` **必须真正省略** Git 键，**不能写 `null`、空串或 `unknown`**；C 按"**键不存在**"处理 | 已确认，C 按此落地 |
| Q2 | §11.2 字段名与类型是否与 C 侧 `sources.py` 兼容 | **兼容**。与现有 `SourceFile` 兼容；`SourceSnapshot` 这些字段按**纯新增**处理，**不复制第二套模型**；只补字段、不改旧字段语义，**原则上不需提升主版本**。`binding_revision` 保持 `int`、语义收紧为 `>= 1`；`purpose` 在 C 落地时按 `analysis`／`prepare` 约束 | **是否提升主版本等 Q3 的 `content_identity` 迁移说明确认后再定**（见第 11.7 节） |
| Q3 | `content_identity` 迁移说明 | **B 已给**（第 11.7 节）。C 明确要求：**不得由 C 自行重算**，必须复用 B 的 `source_content_identity()`；**C 只保存返回值与引用，不复制算法** | 见第 11.7 节的函数接口 |
| Q4 | `SourceSnapshot` 是否登记为 `PreparedRun.InputRevisions.snapshot_revision` 的来源修订 | **已查明该字段存在语义冲突，转裁定**：A 的快照元数据**不含 `revision`**（内容寻址、清单不可变），B 的快照记录**每次为 `@1`**，而 `changed_inputs()` 按值比对、其失效描述为"source bytes changed"——**该判定项在现行实现下无法触发**。三个候选见 [`待裁定/DEC-009`](../../归档/裁定/DEC-009-源码快照的修订语义.md) | 已由负责人裁定，按第 12 节与 DEC-009 实施；原冲突描述保留作历史依据 |

**C 侧落地前提已满足**：C 明确"把 Q1／Q2 的回复和函数接口补到对应合同后，再按新契约 PR 落地"。
本节与第 11.7 节即为该前提。**字段实现不在本合同内散改**，仍在 `domain/execution/sources.py` 走新的契约 PR。

### 11.6 本节不改变的事项

- **不改动 `docs/项目文档/**`**：架构第 7 节的表述歧义由裁定记录引用，不在本项目改动。
- **不新建第二套 `SourceSnapshot`**：类位置仍唯一在 `domain/execution/sources.py`。
- **不手工改生成 Schema 与夹具**：字段落地后由声明所有者重新生成。
- **不把本节的"冻结字段"写成"已实现"**：`provider_implementation` 仍为 `partial`。

### 11.7 B 侧实现落地与 Q3 迁移说明（2026-10-03）

**B 侧已实现**（第 11.3 节的算法不再只是口径，有代码与测试）：

| 项 | 位置 |
| --- | --- |
| `SourceForm`（形式互斥，取值同 `BindingForm`） | `domain/project/context.py` |
| `SourceManifest` 形式化（`git`：基准提交＋差异摘要；`plain`：清单摘要） | 同上 |
| **`source_content_identity()`** —— 第 11.3 节算法的**唯一实现** | 同上 |
| 快照 payload 编解码（形式互斥**落到字节**：另一形态的键真正不出现） | `application/project/serialization.py` |
| 快照落盘 `save_source_snapshot()` / `load_source_snapshot()`（类别 `source_snapshot`；`purpose` 只允许 `analysis`/`prepare`） | `application/project/persistence.py` |
| 漂移核对按**内容身份**比对 | `application/planning/drift.py` |
| 测试 | `tests/unit/test_source_identity.py`（21）、`tests/unit/test_source_snapshot_persistence.py`（12）、`test_frozen_basis_drift.py`（+3） |

**Q3 迁移说明（B 给 C）**：

1. **算法唯一来源**：C 的 `SourceSnapshot.content_identity` 应**调用**
   `aitest.domain.project.context.source_content_identity()`（或按同一规范字节自行实现并加
   交叉断言）。**不要**再自行构造——两套算法一旦分叉，同一份源码在 B 与 C 会得到不同身份。
2. **规范字节**：逐文件行 `<relative_path>\t<size>\t<content_digest>`，
   **按 `relative_path` 升序**，用 `\n` 连接；末尾追加一行形态身份
   （`git:<git_base_commit>:<git_diff_digest>` 或 `plain:<plain_manifest_digest>`）；
   取该字符串的 sha256，前缀 `sha256:`。
3. **不参与计算**：`mtime`、`source_scope`、`exclusion_rules`、`refetch_*`、
   `snapshot_id`、`purpose`、绝对路径与盘符。
4. **是否提升 Schema 主版本**：由 C 按 Q1／Q2 结论判断。若 C 现行构造与本节规范字节不同，
   则**同一记录的身份值会变**，应按第 8 节"变更和兼容规则"处理
   （旧记录身份不静默覆盖，登记迁移方式）。
5. **B 侧不做的事**：不新加快照端口签名、不改 `domain/execution/sources.py`、
   不自行执行 `git`——`git_base_commit` / `git_diff_digest` 由 A 的端口给出。

#### 11.7.1 函数接口规格（C 落地时按此引用，**不得复制算法**）

C 于 2026-10-03 明确要求："请把函数所在模块、输入类型、编码/摘要前缀、异常语义和版本标识固定下来；C 只保存返回值与引用，不复制算法。" 以下为固定值（**2026-10-03 实测**，非约定值）：

| 项 | 固定值 |
| --- | --- |
| **模块** | `aitest.domain.project.context` |
| **函数** | `source_content_identity(manifest: SourceManifest) -> str` |
| **输入类型** | **`SourceManifest` 对象**（不是文件列表）；其 `files` 为 `tuple[SourceFileDigest, ...]`，`SourceFileDigest` 的字段是 `relative_path` / `size` / `content_digest` / `mtime_hint` |
| **规范字节** | 每个文件一行 `<relative_path>\t<size>\t<content_digest>`，**按 `relative_path` 升序**，用 `\n` 连接；**末尾追加一行**形态身份：`git:<git_base_commit>:<git_diff_digest>` 或 `plain:<plain_manifest_digest>` |
| **返回值** | `"sha256:"` + sha256(规范字节 UTF-8) 的 64 位小写十六进制（总长 71） |
| **异常语义** | 该函数**不抛异常**；输入校验发生在 `SourceManifest`／`SourceFileDigest` **构造时**（`ValueError`） |
| **版本标识** | 常量 `SOURCE_CONTENT_IDENTITY_VERSION = "aitest.source-content-identity/1.0"`（同模块，2026-10-03 新增） |

**版本标识的口径**（避免与 A 的版本混用）：

- `SOURCE_CONTENT_IDENTITY_VERSION` 描述的是**规范字节的写法**，不是任何记录的 Schema 版本；
- 算法**任何**改动（字段顺序、分隔符、摘要前缀、参与计算的字段集合）都必须同时升版；
- 它与 `aitest.source-snapshot/1.0`（**A** 的快照 blob 记录版本）**不是一回事**，不得互相替代。

#### 11.7.2 `content_digest` 的前缀口径（**落地前必须统一**）

**实测事实（2026-10-03）**：

| 位置 | 现状 |
| --- | --- |
| B 的 `SourceFileDigest.content_digest` | **不校验格式**，接受任意非空字符串（`'abc'`、`'ABC'` 均通过） |
| B 的夹具 | 写作**带前缀** `sha256:...` |
| A 的 `infrastructure/adapters/source_snapshot.py` | 逐文件记录**裸十六进制** `sha256`（字段名也叫 `sha256`，不是 `content_digest`） |
| C 的 `domain/execution/sources.py` 的 `SourceFile` | 字段名 `sha256`、**裸十六进制** |

**风险（已实测，不是推测）**：同一个文件、同样大小，
`content_digest='deadbeef'` 与 `'sha256:deadbeef'` 算出的身份**不同**
（实测 `sha256:393f0fef…` vs `sha256:5f2e536e…`）。因为 `content_digest` **原样进入**规范字节，
前缀风格不同即身份不同——**同一份源码会出现两个身份**。

**统一口径（本节即契约）**：

1. **进入 `SourceManifest` 之前必须统一为带前缀形式** `sha256:<64 位小写十六进制>`；
2. A 的裸十六进制与 C 的 `SourceFile.sha256` **在构造 `SourceFileDigest` 时加前缀**，
   转换只做一次、位置在**构造处**，不在算法内；
3. **不修改 A 的端口输出格式、不修改 C 的 `SourceFile` 字段名**（属各自目录）；
   转换由需要构造 `SourceManifest` 的一方完成；
4. C 引用 B 的函数时，**必须**用带前缀的清单，否则身份与 B 不一致。

**仍然待办（不因本节完成而关闭）**：

- **A**：`pin` / `read_pinned` / `materialize` / `detect_changes` 的实现与默认装配（第 11.4 节）；
- **C**：按 Q1／Q2 回写兼容性结论并在 `sources.py` 补齐字段（走新的契约 PR）；
- **B**：Q4 已按 DEC-009 裁定并同步 BC-001：`snapshot_revision` 用于读取，源码漂移直接比较 `content_identity`。本节先前待办现已解决；实际源码与默认准备链仍待贯通。

## 12 2026-10-03 裁定同步与已实现边界

- DEC-007：保存保留正文版本；记录读取与 expected_revision 使用仓储修订。规则/计划发布返回 `record_revision`，B 构造冻结规则引用时使用它。版本错配不靠重写导入正文解决。
- DEC-008：PreparedRun 冻结 `scope_id`；缺标识要求重新准备。按准确范围引用读取，并校验项目归属。
- DEC-009：第 11.5 节 Q4 已裁定：`snapshot_revision` 是读取修订，源码变化直接比较 `observed_snapshot_content_identity`，不转换为整数摘要；该观察字段持久保存，不进请求摘要。
- 当前模型配置已注入 B 默认依赖，`generate_draft(generation_mode=model)` 调用实际 provider；未配置时返回阻塞，人工模板路径仍可用。策略修改与发布依然属受控人工动作；这不表示真实供应方或 Trae 接入已验收。
- 源码验证事实增加可选 `observed_interpreter_ref`，没有入口/加载/解释器事实不能因摘要或 PASSED 就证明来源一致。

旧记录和故障分支回归、源码入口、日志见 [修复证据清单](../../../ABC包问题修复证据清单-2026-10-03.md)。领域与基础设施不另起模型端口；基础设施经 `application/ports.py` 获取统一声明。

### 12.1 C 启动认领的现行合同落实（2026-10-04）

按一期架构《执行与证据》第 12、14 节，启动认领须固定准确的步骤修订和实际消费依赖，不能只固定 ExecutionRequest。C 内部授权引用补充 `step_revision_ref`；旧引用仍可读取，但缺修订时禁止新启动，须按当前步骤重新确认。它不是新的集合授权，也不能代替受控人工确认记录。

同一 A 工作单元保存启动意图、检查点和授权占用；授权标识在工作空间内只供一个准确的项目/Run/Step/Attempt 使用。原请求恢复核对已保存的占用，新增 Attempt 不能靠调用方仍传“未使用”绕过。意图指纹包含请求、步骤修订、尝试序号、适配器版本、消费的输出/条件及业务幂等引用，运行态与输出进度不参与指纹。

以上收紧不改变公开 ExecutionFacts Schema。旧检查点缺完整指纹时不能冒充已核实原请求恢复；保留材料，进入核实路径。当前仍需贯通受控人工授权入口、当前依赖/整用例复用同提交和默认业务执行链，不据此登记 C-01/C-02/C-05 完成。

### 12.2 已发布 ExecutionFacts 的准确当前引用

C 保存一致快照时，在同一 A 工作单元保存按项目/Run 寻址的当前引用，绑定准确 `snapshot_commit_id`、读取修订和快照正文摘要；原快照永久不改写。引用先暂存、快照后暂存，`snapshot_cursor` 仍指该工作单元最后发布的快照记录，不能因新增引用错位。读取按引用核对项目/Run、修订和摘要；损坏或无法读取须阻塞，不能退回“最新成功”。

本项是保存实现的收敛，不增加公开 ExecutionFacts 字段，不自动把旧快照认定为当前活动。新 Attempt 对当前引用/依赖/未使用授权的原子更新以及 D 的生产消费仍需后续落实。

### 12.3 源码快照的采集安全

按一期架构《执行与证据》第 13 节，源码也是不可信采集材料。固定字节必须在写入临时文件或 blob 之前通过已知凭据/认证字段保护；若过滤会改变源码，拒绝固定并保留缺口，不能把过滤副本伪装成原始源码。现有 blob 的复用和物化亦须核对实际摘要与安全性；不以“对象已存在”跳过检查。清单损坏不能静默解释为空源码。此要求落实现行采集合同，不改变快照身份算法或公开 SourceSnapshot 字段。

### 12.4 精确备份与当前事实发布（2026-10-04）

永久备份是准确字节闭包，不能把过滤副本冒充原摘要。创建和恢复先检查路径、完整清单、实际摘要与落盘前安全，故障拒绝不改写原材料；复制阶段再次核对并fsync。结构化材料超过安全核对预算须显式阻塞，不能以未知大文件绕过安全检查。

当前ExecutionFacts的Attempt使用组装同一投影逐字段核对真实检查点，其他当前步骤亦须读取同项目权威检查点。spool到对象的证据发布核对冻结块、对象回执及实际落盘字节；任何后置过滤改变字节，不能登记原完整证据。新增守卫不改变公开Schema，不使未使用授权/复用/历史证据闭包自动完成。

### 12.5 已登记运行的新 Attempt 与当前事实（2026-10-04）

按一期功能第15节及执行架构第14节，已有权威当前快照时，启动认领必须在同一工作单元替换对应步骤的当前 Attempt、保存新检查点，并按已保存的实际输出/条件消费关系使下游及传递依赖依据过期。旧 Attempt、输出、证据及旧快照保留；无依赖分支保持有效。存在活动或结果未知的受影响执行时，拒绝替换，先停止并核实，不能用失效状态掩盖仍在运行的副作用。

检查点的后续进度须同步投影到准确当前快照；普通发布不得把当前引用退回旧 Attempt 或丢掉已保存步骤。新尝试后旧结果引用、证据等级和覆盖摘要不再作为当前结论，重新判定仍归唯一领域规则。失败事务不能只发布授权占用、当前引用或部分下游失效；提交结果失联时按原意图核实，不再次启动。

此修复使用已有 ExecutionFacts 字段，不增加新的复用/判定合同。尚无登记运行快照的组件路径不能补造 Run/Step；受控人工确认、未使用授权存储、整用例复用与 D 的正式判定消费仍按原主责合同接入，不因此宣称 C-01/C-02/C-05 整项完成。

### 12.6 连接事实与已配置凭据出口（2026-10-04）

连接能力的自动条件为同端点已保存探测事实的投影；最新成功事实可以恢复旧自动故障，不能解除人工暂停。实际可达性必须为bool，时长为非负整数；台账按原Schema/字段/分类核对，重复字段、缺项、矛盾值与未封口尾行不能解释为就绪。不可核对记录仅降级连接依赖，保存失败返回安全错误，不把本地动作一起关闭。

诊断在完整字段过滤后才截断与JSON转义。协议异常和正常结果均通过默认装配的已知凭据出口；该安全过滤器失败不得退回原正文或仅按模式过滤。普通布局投影器不取代此安全边界。以上落实已有动作级能力与落盘前采集合同，不增加业务授权或新的公开字段，不等于完整产品/真实环境验收。

能力降级按具体动作保存机械条件。自动事实与人工暂停分别持久保存和水合，人工恢复不抹真实自动故障，历史就绪不提供本核心配置。有限条件记录属核心恢复事实，不新增业务领域规则或独立数据库。

### 12.7 有界查询目录与旧格式迁移（2026-10-04）

按一期存储第13节，查询目录元数据和报告/问题的当前物理键账同样须有界，不能只分片数据正文。实现使用内容寻址有序树：单节点最多16个子引用、叶页有界；小提交从已发布根读取准确身份键，复制相关路径，最后切换查询根。损坏键账不得当作空账；上一中断留下的目录meta和节点不得带入下一提交。永久旧根与节点保留，旧游标在原提交边界续读。

可读的旧v3平铺目录经0004迁移，在活动守卫和完整备份校验后转换；恢复不先静默改该可读布局。前态在改目录之前固定，重试沿用前态，回滚最后恢复旧根。格式转换晋升generation，旧游标明确刷新；有新业务写入时阻止回滚。公开QuerySpec和游标仍为原版本，这只是内部目录布局变化。

12.7目录阶段仍有选择器余过滤/字段忽略和完整CommitManifest索引闭包缺口。选择器问题随后按12.8修复；完整提交闭包与真实产品验收继续保留，不据目录回归关闭A-05。

### 12.8 有限选择器组合与拒绝回执（2026-10-04）

12.7阶段查出的两种余过滤/字段忽略已修。对象修订必须给准确project/record_type/record_id；问题列表不能混用报告或对象选择器，报告编号入口只接受一个Run或Report准确标识，报告业务结果过滤仅用于报告入口。非法组合在读文件前返回QUERY_UNSUPPORTED_FILTER，不扫描其他对象或忽略字段。合法问题facet/级别、报告编号/结果及对象修订保持可用。

内部model_copy/model_construct也须重新校验，不能绕过公开合同。应用RecordQuery同样拒绝缺类型的对象标识，接口保留准确错误码。首提交与显式重建也核对行/提交序号边界，非法输入不能先创建索引文件。公开字段、QuerySpec/游标版本未变，当前仍需完整提交索引闭包、产品历史/固定排序、默认业务链与真实验收。

### 12.9 核心认领、实际进程与停机事实（2026-10-04）

按一期存储第2节与Trae接入第1—2节，启动使用真实OS互斥；缺发现文件不等于无写入者，先核实真实写锁可取得。实际创建后核对进程出生身份，再发布实例指针；创建失败不发布，正在启动/存活的同实例不再创建替代核心。未知创建/存活保留材料并阻塞，不能按mtime或磁盘PID强行接管。子进程在准确认领和指针发布前不进入业务装配/恢复；创建不等于READY，READY仍来自已核对管道连接。

Windows当前Python的venv转启动须区分launcher PID与实际核心PID。默认当前解释器直接使用真实运行镜像，并按CPython的__PYVENV_LAUNCHER__合同保留venv；捕获的Popen句柄、持久PID/出生身份与实际管道服务端一致才可核实。客户端核对服务端用户/会话和准确PID；有限帧传输须处理短读短写、错误返回及长度上限。发布失败只停止本次捕获进程，不终止由磁盘PID找到的外来进程；无法确认停止则保留不确定事实。

shutdown_requested与实际exited分开保存。停机后的管道缺失不能作为退出证明，下一认领核实旧进程退出并取得真实写锁后才开始恢复。早退保存有限退出码，原始异常/凭据不进启动诊断；已知凭据的结构值及序列化字节在写历史/当前事实前核对，路径祖先链接拒绝。

67新增回归及140启动/管道/架构专项通过，只证明组件边界。默认C/D、可信人工/权威排空、未知旧创建/其他自定义转启动解释器的恢复、writer_epoch发现字段及真实Trae仍缺；不改变reviewing/partial/not_run，不新增FR/AC或把内部启动文件当业务提交权威。

### 12.10 公开发现路径与Windows实例文件名（2026-10-04）

公开acquire/shutdown必须先核对调用方提供的原始目录及祖先，再resolve；不能因下游只看到已解析目标而接受Junction。拒绝发生在工作空间、台账、连接器或启动器访问前。实例指针限定单一Windows常规文件名，拒绝设备、ADS、尾点/尾空白与非法字符；父启动器和子进程认领门共用同一守卫。真实Junction及12类反例已验证，仍不代替默认C/D、可信人工/权威排空或真实Trae验收。

完整提交的记录/索引/事件统一发布仍缺。已实际确认：投影持续失败后普通查询可返回旧根且后续健康增量漏行；事件失败可缺当前快照游标。下一实现需按现行存储合同冻结同一CommitManifest，不以高提交号/索引健康或重启自愈代替完整闭包；状态保持reviewing/partial/not_run。

### 12.11 同一发布边界组件原型与剩余主责合同

默认装配先备份再0005迁移；材料准备后单一current共同发布记录/幂等结果、准确点查/有序目录和事件页。失败前保持旧完整边界，切换后按原持久意图回读；变化正文/归属/修订/摘要/事件与回执逐项核对。旧快照和事件游标一致，旧/新正式事件材料保留，迁移投影不产生新的执行/核验。未知来源/缺事件/同序异内容阻塞，不自动重放。

该原型只证明当前故障分支的组件修复，未完成主责current字段、Windows ReplaceFileW/候选与前指针恢复材料、对象/业务变更索引闭包、实际核心实例来源和有界正常启动。下一实施必须回到项目分册现行合同，不以内部schema/commit_sequence/parent_manifest等存储字段另立平行规范。18个B动作的公开端口签名和生成Schema未修改；状态继续reviewing/partial/not_run。

1928 passed、2 skipped（270.06s），根因专项573 passed（216.11s）；Ruff和mypy137文件、生成物、构建、制品对拍及隔离wheel冒烟通过；源码、节点、摘要及未验证范围见[证据清单](../../../ABC包问题修复证据清单-2026-10-03.md)。组件对拍不是跨包完整验收。

### 12.12 主责七字段current与Windows发布组件（2026-10-04）

按项目存储分册第2节落实schema_version/workspace_id/generation/commit_id/manifest_digest/index_root/event_cursor；七字段同清单核对，旧四字段只保留读取，通过活动守卫及可校验备份0006迁移而不改写业务根。Windows发布核对同一固定本地卷，检查FlushFileBuffers/ReplaceFileW及备份路径；候选/前指针/描述符摘要永久保留。未知发布不自动回退或提升候选；新根后flush失败不返回提交成功、不重复意图，默认装配拒绝新写。

1967 passed、2 skipped（333.34s），解析修复前提交/Windows/迁移专项97 passed（98.37s），解析修复后新增2场景通过（2.42s）；Ruff和mypy138文件、生成物、wheel/sdist构建、制品字节对拍及隔离wheel冒烟通过。面板/VSIX源未改，沿用前轮日志并对拍字节，未声明重新构建。 39新增场景及源码字节证据见[Windows阶段](../../../validation/p1-abc-fix-20261003/WINDOWS-PUBLICATION.md)。完整主责CommitManifest及幂等结果引用/文件摘要合同、证据对象与business_change_index完整闭包、实际核心实例来源、普通启动有界恢复、未知发布人工恢复、目标设备名称元数据耐久/物理掉电、产品历史/固定排序、默认业务入口与真实AC仍需落实。 当前内部manifest/1不是完整规范清单；不得据API调用证明掉电耐久。18个B公开动作及生成Schema语义未变，状态继续reviewing/partial/not_run。

### 12.13 非默认隔离环境的准确确认与消费（2026-10-06）

依一期项目与计划第1/4/8/12节，EnvironmentRef是声明；客户端isolation_confirmed不能代替确认来源，也不能证明实际解释器、依赖、数据或目标服务一致。普通venv声明继续允许原自动保存；none/unmanaged或明确携带确认挑战的save_environment必须走同一受控写适配器。

准确输入包括已保存project_revision、expected_revision与完整规范环境正文，正文版本与下一仓储修订一致，拒绝跨项目、布尔修订、未知字段及过滤后不同正文。确认摘要覆盖解释器要求、依赖声明、隔离方式、业务数据/复位、目标部署、网络/超时及按用途SecretRef；凭据仅引用。确认消费、交互、核心确认/意图、environment与原效果回执六记录同一短事务，任一失败原环境/挑战保持，重试不重复发布。

prepare、C新初始登记及运行修订读取冻结的准确环境仓储材料并回读受控来源。旧无来源非默认隔离只保留历史，不能作为新准备/修订许可；原受控保存意图可按准确原回执读取，不授予其他动作。真实环境解析、未用执行授权和宿主事件继续另行实现/验证，接口仍reviewing/partial/not_run。

受控计划发布仍先共用原嵌套项目归属输入守卫，scope/case显式冲突返回B_INVALID_PARAMETER，不以等待确认替代非法输入。该预检只解析请求正文、不授权、不写入；合法材料继续完整受控来源守卫。

### 12.14 可信被测环境解析（2026-10-06）

依据一期项目与计划第1、8、11、12节，EnvironmentRef仍为声明。EnvironmentResolver属于application/ports.py；核心装配注入已登记载体，客户端不得通过路径、解释器要求文本或自报身份登记可执行文件。登记绑定project/environment、绝对可执行文件与预期内容摘要、依赖根及venv配置；未配置时阻塞该环境准备，禁止回退核心sys.executable。

探测在短事务外以隔离启动、禁用site的固定脚本核对Python版本与真实可执行文件，过滤环境变量，不导入项目、sitecustomize或执行.pth。依赖根逐文件内容摘要，缺根、链接逃逸、扫描/输出/超时超限、非映射.pth及系统包混入均明确阻塞；不只读取包名称、METADATA或mtime。前后内容核对，无法证明稳定则拒绝。此实现保守阻塞需特殊加载映射的环境；不声称证明实际业务服务、数据复位、加载来源或安全沙箱。

核心覆盖客户端的解析观察值并冻结可读resolution事实；客户端自报身份/依赖摘要只作兼容输入，不作为证明。原EnvironmentRef、Binding准确仓储修订与正文摘要在短提交边界再次核对。环境内容身份作为PreparationRecord观察事实独立比较，变化或旧记录缺证明时needs_reprepare；不把它混入人工请求摘要、不覆盖原意图。

启动前核对venv.cfg home解析到登记基础解释器，非venv载体不忽略活动venv.cfg。只读版本探测直接使用显式登记的基础解释器以避开Windows venv启动器子进程，不默用核心解释器；载体文件/配置观察与基础解释器进程观察分开，不声称业务通过载体启动器已执行。可靠停止后临时目录清理失败不覆盖原超限/超时诊断，未确认停止的业务材料不按此路径回收。

1.27同批补analyze_project新响应的source_current_ref{record_id,record_revision}，表示本意图准确保存的绑定来源指针修订；source_snapshot.record_revision仍是不可变快照读取修订，不借其恒为1判断源码变化或猜下一次指针CAS。新操作在实际固定前及锁内两次核对指针expected_revision；旧意图缺该可选引用只回历史事实，不补猜当前值。此补充不改变content_identity和PreparedRun字段。

1.27同批补正式交付的发布引用闭包：A按准确snapshot_id/snapshot_record_revision回读项目源码，核对content_identity与绑定引用，再核对实际固定清单/全部blob，并将这些路径及摘要纳入本次完整清单。审批后至发布前损坏或丢失不得发布current/消费确认；应用层先前的读取成功不能替代发布前核对。

1.28同批标识新source_pin_intent为aitest.source-pin-intent/1.0，规范inputs与摘要同存；A按新schema核对原快照/绑定/内容身份及准确本意图来源指针，并纳入实际固定清单/blob闭包，即使同字节未新增source_snapshot也不能省略。新schema缺输入/引用/摘要时拒绝发布。旧无schema的已存意图/清单保持原字节与历史分类，回读仍核对实际材料，不重新固定或回写旧记录。

新source_pin_intent用aitest.source-pin-intent/1.0标识冻结inputs；同字节复用也须将原快照实际清单/blob带入本提交闭包。提交前核对本批快照/指针时只建只读候选视图，不提前添加权威节点；原持久事务意图重传核对原已提交视图与准确材料，不因旧expected_revision重复新增或误拒绝。旧无Schema记录按原闭包兼容读取，不追写历史。
