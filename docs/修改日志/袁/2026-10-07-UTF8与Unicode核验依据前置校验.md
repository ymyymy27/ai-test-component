# UTF-8与Unicode核验依据前置校验

涉及A-02/09、C-03及共同材料读取，ABC整项仍21，真实AC仍0/35。

根因：公共json.loads直接接受bytes会自动探测UTF-16/32，CLI可解析再转写成合法命令，回执/MCP也可能接受超出约定的编码；冻结比较材料没有检查未配对Unicode代理字符，非法键/值或核验范围可在查询后、UTF-8保存/回包时才失败。现公共解码先严格UTF-8，再检查全部解码键/值；同一纯领域规则检查冻结材料，应用层冻结完整核验字段（引用列表显式转JSON数组），在供应方调用之前拒绝。HTTP保留实际原字节，非法文本不变成断言匹配或已知失败。

[项目采用的JSON互通边界](https://www.rfc-editor.org/rfc/rfc8259.html#section-8)为UTF-8且精确保留字符；不替换/丢弃字符，不转换旧坏材料，合法中文、emoji及转义代理对正常可读。源码：[公共解码/字符校验](../../../src/aitest/domain/json_material.py)、[比较材料冻结](../../../src/aitest/domain/execution/assertions.py)、[核验完整范围](../../../src/aitest/application/evidence/evidence_review.py)、[反例及实际CLI/MCP](../../../tests/unit/test_json_unicode_boundaries.py)。CORE-001 1.55保持reviewing/partial/not_run，DTO/FR/AC不变。

验证：原30 failed/1 passed，含编码误接收、非法范围仍调用、非法材料参与比较及MCP回包编码崩溃；先前两条回执夹具误写协议版本已按真实Response修正，不计原3通过为编码已拒绝。32新增节点覆盖合法字符和实际Windows CLI拒绝后MCP继续使用原核心/epoch。第一版387项通过；保留既有未提交日志源码锚点的重构中曾误用slots记录vars、未将tuple引用转数组，18/11失败分别保留，均已修。最终源码387 passed（273.02秒），范围为协议/CLI/MCP/HTTP/核验、默认真实文件业务写→独立读→保存/重启回原意图、合同和架构。Ruff/Mypy207、生成物/0.4.0与最终wheel/sdist通过。最终安装三模块字节相等，51项字符/核验范围/依据及实际Windows入口再次通过。[原始记录](../../validation/p1-abc-remaining-20261004/README.md)。

复跑：pytest tests/unit/test_json_unicode_boundaries.py tests/unit/test_wire_json_identity.py tests/unit/test_mcp_stdio_read.py tests/unit/test_cli_core_forwarding.py tests/unit/test_verification_json_semantics.py tests/unit/test_verification_request_scope.py tests/unit/test_verification_basis_freeze.py tests/unit/test_saved_business_verification.py tests/unit/test_default_business_verification.py tests/unit/test_http_json_material.py tests/architecture tests/contracts -o addopts= -q --tb=short。

边界：合成项目/确认/来源仍是夹具，实际命令/文件读取仅证明这些组件；五类生产核验配置、最终一致性、完整关联、真实Trae/人工及AC仍需完成。
