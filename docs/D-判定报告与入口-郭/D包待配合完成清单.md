# D包待配合完成清单（2026-10-05）

本清单只记录 D 包无法单独闭合、必须由 A/C/团队/宿主环境提供的外部条件。

## A 包

- A-D-01：正式 `WorkspaceUnitOfWork` 装配与调用方式，合并保存问题、复核、修复、回归、报告和导出登记。
- A-D-02：`RecordRepository` 按准确修订读取真实新回归、历史报告和问题主链。
- A-D-03：`ReportArtifactPort` 可调用实现，生成并校验 Markdown 摘要和证据包。
- A-D-04：`issues.list` 物理索引合同及 14 种掩码、OPEN/ALL、同 commit 分页。
- A-D-05：统一公开 CoverageDTO/DecisionDTO/问题列表 DTO，未知不补 0/false。
- A-D-06：事件载荷和快照/游标续读方式。

## C 包

- C-D-01：完成 C-01—C-13 修复提交。
- C-D-02：明确业务断言结果的正式生产来源，生成 PASSED/FAILED/UNKNOWN。
- C-D-03：提供结构化 `error_ref` 目录或读取入口。
- C-D-04：提供一致快照和对象引用读取链路。
- C-D-05：提供 timeout、非 UTF-8、多流游标的新夹具。

## B 包

- B-D-01：BD-001 第 8 节动作桥接结果。
- B-D-02：发布冲突和出站未知的安全差异信息。

## 团队与 Trae

- T-D-01：选定 Trae 稳定版和安装包。
- T-D-02：Git 项目和 plain 项目各一个。
- T-D-03：Windows 11 x64、Python 3.13、模型或无模型路径。
- T-D-04：D 牵头 16 项 AC 的真实验收窗口与端口分配。

## 当前不可关闭项

- D-02：待 C-D-01、C-D-02、C-D-04。
- D-04：待 A-D-01、A-D-02、A-D-03。
- D-05：待 A-D-05、A-D-06。
- D-06：待 A-D-01、A-D-06、B-D-01、T-D-01。
- D-07：待团队与 Trae 环境全部就绪。