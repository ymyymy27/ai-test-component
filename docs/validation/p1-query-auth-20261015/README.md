# 鉴权与动态对象核验续修（2026-10-15）

基线 fix@ad92ce5352da42e273184e43a97e3b57cc044a21，交付版本 0.4.4。主责规范和验收登记不变；真实 Trae/人工/业务部署验收未代填。

## 实现

- 宿主查询登记可选 credential_reference，经默认 SecretPort 按 http 用途解析，仅 HTTPS Bearer；使用后清空，失败不回退匿名。凭据进入 HTTP 适配器时登记落盘前过滤。
- 依据 /2 从当前 Attempt 完整永久 stdout JSON 按明确对象键路径提取动态 ID，保存证据引用。拒绝错归属、缺块、错序/偏移、摘要损坏、过滤/缺口及重复 JSON 键。执行输出只供编号，不作为独立预期或通过结果。
- /1 固定 ID 与原命令形状兼容；无新业务动作、无新 HTTP 服务。

## 检查回执

- boundaries：32 项中 30 通过、2 失败；测试误写错误枚举为 error，现行合同为 query_error，已修正断言，保留原回执。
- boundaries-fixed：75 项通过，含既有 HTTP 查询全文件与新增鉴权/提取边界。后续新增跨块 JSON 用例另记最终回归。
- chain：首次失败，真实命令执行成功，但默认适配器未接持久 spool，导致 stdout 缺过滤摘要；动态 ID 校验正确阻塞。已修默认装配，chain-fixed 重跑。
- chain-fixed：鉴权/动态 ID/保存报告成功，ZIP 导出因证据编号含冒号而被路径守卫拒绝；已改为稳定摘要文件名并在清单保存 evidence_id 映射。
- chain-export-fixed：48 项中 44 通过、4 失败；真实鉴权动态整链通过（含 ZIP、重启和工作空间凭据扫描），四个旧测试仍要求拒绝特殊业务编号，现改为验证编码后的安全文件名与准确编号映射。原危险 ZIP 路径拒绝检查保持；最终回归另列。范围：真实登记 venv、真实 TLS 和动态创建 ID、报告/ZIP/重启回读；人类事件夹具。只显式信任仓库测试证书，保留证书与主机名校验；凭据来源是显式登记的测试环境变量，不冒称 Windows 凭据管理器现场验收。

## 未验证

完整全量、真实业务/Trae、Windows 凭据现场登记、纯函数核验、非 stdout 的对象提取、全部步骤与恢复仍需补齐。原 27 项开放问题不自动关闭。配置与操作见[0.4.4 指南](../../一期工程整改/04-交接/0.4.4-鉴权与动态对象核验.md)。

新增 native-boundaries：34 项通过，含跨块 JSON、Windows 凭据管理器真实写入/按用途读取/删除与清空检查。只使用唯一随机测试引用及虚构凭据；不替代客户真实凭据配置和业务部署验收。

## 最终制品与复核

[final-release-commands.json](final-release-commands.json) 全部退出 0：ruff、strict mypy 256、版本/锁/Schema、宿主 25、浏览器 17、VSIX、wheel、制品检查和隔离安装。[facts.json](facts.json) 为 242 Python 模块、362 测试文件、5540 节点仅收集；[wheel-source-parity.json](wheel-source-parity.json) 核对全部 Python 源码字节，不能写成全量通过。

最终 [regression.xml](regression.xml)：261 项全部通过，退出 0，覆盖默认执行、来源、过滤摘要、已有固定 ID 核验、凭据、动态提取、导出、架构及三个期次验收登记。四个导出旧断言已更新并通过；原危险 ZIP 路径拒绝检查继续通过。原始批次分别列出，重叠用例不累计。

日志/XML 入库只清理行尾空格与末尾空白行，失败正文和结果保留；原始字节另存本机 `.runtime/query-auth-raw-evidence/`。修改前文档可由 ad92ce5 或本机 `.runtime/auth-docs-ad92ce5.zip` 恢复。

真实鉴权动态整链的单项通过见 [chain-export-fixed.xml](chain-export-fixed.xml)；该批另有四个旧断言失败，已由最终 261 项回归补验。回归启动后只将一处测试函数签名换行以满足 ruff，无语义改动；产品源码未再变动，最终指纹与制品重新核对一致。
