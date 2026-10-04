# ABC剩余问题验证材料

取证基线fix/1289e5b640a0312ae2386b93c656ddf6063abccf。组件检查与真实AC分开记录。逐项根因、源码、结果及剩余边界见[证据清单](../../ABC剩余问题修复证据-2026-10-04.md)。

## 不可变阶段

- `history/object-reference-incremental-startup/manifest.json`固定首阶段325份工程源码、26个AST函数/行号、8份原始日志及source ZIP。该阶段不含后续epoch/清单/来源/准备/确认修改，不能作为当前全量证明。
- [canonical-source-preparation-2065/manifest.json](history/canonical-source-preparation-2065/manifest.json)固定第二阶段342份工程文件、1032个AST符号/行号和58份原始日志/制品核对记录。完整Python2065 passed/2 skipped（627.73秒），Ruff、Mypy160文件、Schema/版本、wheel/sdist构建和隔离wheel冒烟通过；档案与当前工程字节核对通过。
- [source-closure-initial-registration-2083/manifest.json](history/source-closure-initial-registration-2083/manifest.json)固定后续源码blob与初始登记阶段。全量2083 passed/1 failed/2 skipped（866.47秒）；失败测试在运行时加载了更正前的“传输重试作为新准备”夹具，pytest显示的源码可能是随后更正版本，不能据回溯文字声称更正后夹具仍失败。实际更正单项1 passed，全部新增19项204.42秒通过。工程Ruff、Mypy162文件、生成物/版本、构建/隔离冒烟及现有VSIX面板字节核对通过；不改写为全量2084通过。
- 后续修复另建独立阶段，原档案不覆写。每阶段清单记录源码成员原始/换行规范化SHA256、AST符号行号、日志SHA256与准确结果；不以当前修改后的行号替代当时源码。
- [consumed-source-closure-2092/manifest.json](history/consumed-source-closure-2092/manifest.json)固定准备/初始登记实际消费历史源码的提交闭包：347份工程文件、1087个AST符号、101份原始记录。完整2092 passed/2 skipped（947.00秒），Ruff、Mypy162文件、Schema/版本、构建/隔离wheel冒烟及现有VSIX面板字节核对通过。模型依据四个新反例在此版本仍未修复，后续另建阶段。
- [model-basis-safe-receipt-2125/manifest.json](history/model-basis-safe-receipt-2125/manifest.json)固定模型准确依据、迟到响应与安全旁录阶段，基线为已推送d53f6d5。351份工程文件、407个AST符号、131份原始记录；完整2125 passed/2 skipped（1082.92秒），专项414 passed，Ruff、Mypy164文件、Schema/0.4.0版本、构建/隔离wheel冒烟及现有VSIX面板字节核对通过，档案/当前工程核对通过。未发布响应受控恢复、完整实际依赖和真实产品验收仍缺；其后运行事实身份7个实际反例尚未在此版本修复，重复步骤对照已被现有规则拒绝。此次全量于2026-10-05跨日完成。
- [runtime-facts-identity-287/manifest.json](history/runtime-facts-identity-287/manifest.json)固定2026-10-05的后续事实身份守卫，基线为已推送bcdb0d0。352份工程文件、7个本次AST符号、146份原始记录（含原探针源码）；受影响回归/全部合同/架构287 passed，原探针8 passed，Ruff/Mypy164文件、生成物/版本、构建/隔离wheel冒烟及制品核对通过，档案/当前工程核对通过。本局部增量未再跑全量，2125项全量只属于上一351文件版本。档案如实保留首次错误文本匹配失败及文档函数链接L52误标；源码正确行号为53，更正文档后166个本地链接及源码符号核对通过，独立日志未覆写原记录。

## 重复验证

```powershell
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/object-reference-incremental-startup/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/canonical-source-preparation-2065/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/source-closure-initial-registration-2083/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/consumed-source-closure-2092/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/model-basis-safe-receipt-2125/manifest.json
.venv/Scripts/python.exe scripts/verify_abc_phase_evidence.py docs/validation/p1-abc-remaining-20261004/history/runtime-facts-identity-287/manifest.json
.venv/Scripts/python.exe -m pytest
.venv/Scripts/ruff.exe check .
.venv/Scripts/mypy.exe
.venv/Scripts/python.exe scripts/generate_schemas.py
.venv/Scripts/python.exe scripts/check_versions.py
uv build
.venv/Scripts/python.exe scripts/smoke_wheel.py
```

`--against-checkout`只用于核对与该阶段相同的工程源码；后续继续修改时应仅核对不可变档案，差异是正常的新阶段边界。

## 原始记录

`*-before*.log`记录实际反例；部分初次测试写错临时夹具调用/路径，名称明确保留，后续`*-corrected`/`*-behavior`记录纠正后的实际输入。`full-pytest-current.log`为首轮2028 passed/37 failed/2 skipped，`full-regression-repairs.log`为修正真实对象/权威材料后的150 passed，`full-pytest-corrected.log`为第二轮2065 passed/2 skipped。不能把没有收集到测试、夹具错误或历史档案格式错误写成工程测试通过。

静态检查、构建与wheel冒烟各有单独日志。所有真实AC状态未改变；不宣称本轮真实Trae、真实人工页面、物理掉电或供应方故障已经通过。

- [model-response-resolution-2177/manifest.json](history/model-response-resolution-2177/manifest.json)：本轮完整Python为2177 passed、2 skipped（1298.72秒），包括全部27个新增补登记节点。Ruff、Mypy165文件、Schema/0.4.0版本、wheel/sdist、隔离wheel冒烟及现有VSIX面板字节核对通过。冻结354份工程文件、147个AST符号与170份原始记录；源码ZIP SHA256为`1b776098d8b2c559923a0cb4f87cc302bedda7d08816172436d8179af469dd06`，档案及当时工作区核对通过。原2项反例、fixture导入/关键字参数错误和首次中断全量均原样保留；未知维护活动标记、完整依赖/执行链及真实验收仍缺。
