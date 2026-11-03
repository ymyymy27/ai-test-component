# 本轮检查证据（2026-10-03）

本目录对应取证基线 `40c82c34ae01bdb01deff76d133ce317bec65daf`，产品源码仍 `9bd4337c1a0696db9cd8bd21e7b67f3a751923bb`。本轮为审查，没有整改产品或替代真实一期验收。

| 文件 | 内容与边界 |
| --- | --- |
| [review-inventory.json](review-inventory.json) | 153个跟踪产品/资源/集成/构建文件的路径、原始行数、SHA256、检查方式。144非生成文件逐文件读取可执行正文，7份Schema重生成、2份npm锁结合build检查；不是动态覆盖率 |
| [ocr-scope.json](ocr-scope.json) | 差异预览415变化文件、125候选、290工具排除项；125中100与全产品阅读清单重合，25不宣称逐文件审查 |
| [findings.json](findings.json) | 15项新问题的主责、建议级别、准确源码位置、合同/FR/AC、预期、实际、根因、影响限制与修复验证 |
| [probes.py](probes.py) / [observations.json](observations.json) | 7组身份、交付、发布、绑定、单模块及执行恢复观察 |
| [extended_probes.py](extended_probes.py) / [extended-observations.json](extended-observations.json) | 7组快照、迁移、事件、Win32 SID、运行修订、长步骤/输出与空full观察 |
| [validation-results.json](validation-results.json) | 本轮Python、静态、Schema、面板/制品检查与真实AC边界；pytest1148通过/2跳过，Playwright1失败 |
| [artifacts.json](artifacts.json) | 本轮wheel/VSIX SHA256与共享panel字节核对；只证明制品内容一致，不证明Trae兼容 |
| [wheel-smoke.json](wheel-smoke.json) | 新虚拟环境安装的site-packages来源、依赖与6模板/Schema/panel、CLI诊断；非业务验收 |
| [pytest-summary.txt](pytest-summary.txt) | 全量pytest结尾与两个跳过用例的原因 |

在项目根目录、仓库锁环境运行：

```powershell
uv run python -m docs.validation.p1-e2e-audit-20261003.probes
uv run python -m docs.validation.p1-e2e-audit-20261003.extended_probes
uv run ruff check docs/validation/p1-e2e-audit-20261003
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python scripts/check_versions.py
uv run python scripts/generate_schemas.py
uv build
```

制品/面板执行使用各自package.json中的build、test和package。探针依赖源码仓库的tests辅助夹具；不是可脱离仓库的安装包验收套件。两个探针都只使用临时工作空间、合成材料和本机受控只读进程，没有向真实模型供应方发送请求。

**退出0表示断言观察到当前缺陷**，不表示问题修好。修复后探针中的旧缺陷断言应失败，随后需改成对应正常/拒绝行为的产品测试并验证默认消费者。运行中门禁、模型外部端口和部分恢复检查使用夹具或受控模拟，findings逐项说明；真实命令、Win32本机身份查询、文件事务和迁移等原语实验也不替代Trae/掉电/实际业务验收。

旧证据保留在 [p1-audit-20261002](../p1-audit-20261002/README.md)，本目录不覆盖旧观察。全部现行规范、接口状态和真实AC登记保持原样。
