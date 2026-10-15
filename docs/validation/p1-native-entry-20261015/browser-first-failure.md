# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: controlled-entry.spec.ts >> controlled editor preserves intent through refusal and never sends a user-event assertion
- Location: tests\controlled-entry.spec.ts:6:1

# Error details

```
SyntaxError: Unexpected end of JSON input
```

# Page snapshot

```yaml
- main [ref=e3]:
  - generic [ref=e4]:
    - heading "AI 辅助测试" [level=1] [ref=e5]
    - paragraph [ref=e6]: 核心回执 · doctor · READY · 可派发动作 0
    - button "连接核心" [ref=e7] [cursor=pointer]
    - button "刷新核心状态" [ref=e8] [cursor=pointer]
    - paragraph [ref=e9]: 当前项目：project
  - generic [ref=e10]:
    - generic [ref=e11]:
      - heading "当前测试" [level=2] [ref=e12]
      - generic [ref=e13]:
        - generic [ref=e14]:
          - article [ref=e15]:
            - heading "运行前" [level=3] [ref=e16]
            - paragraph [ref=e17]: 等待准备计划
            - paragraph [ref=e18]: 确认范围、来源、环境、依据和逐项动作授权。
          - article [ref=e19]:
            - heading "运行时" [level=3] [ref=e20]
            - paragraph [ref=e21]: 尚未开始
            - paragraph [ref=e22]: 显示当前步骤、尝试、待处理事项和准确阻塞原因。
          - article [ref=e23]:
            - heading "运行后" [level=3] [ref=e24]
            - paragraph [ref=e25]: 暂无报告修订
            - paragraph [ref=e26]: 显示业务结论、证据等级、未验证范围和下一步。
        - button "刷新本次视图" [disabled] [ref=e27]
        - paragraph [ref=e28]: 尚未取得当前运行的准确视图。
    - generic [ref=e29]:
      - heading "核心能力（真实回执）" [level=2] [ref=e30]
      - paragraph [ref=e31]: 核心 READY，当前可派发 0 个动作。
      - paragraph
    - generic [ref=e32]:
      - heading "受控操作（预览）" [level=2] [ref=e33]
      - paragraph [ref=e34]: 先配置项目编号。此处请求核心窗口确认完整输入；窗口确认前不会提交。计划、环境和执行仍须满足核心门禁。未知结果保留原意图查询，不自动重跑。
      - button "填写新建项目" [ref=e35] [cursor=pointer]
      - textbox "受控操作 JSON" [ref=e36]:
        - /placeholder: "{\"action\":\"…\",\"intent_id\":\"稳定业务意图\",\"expected_revision\":0,\"parameters\":{}}"
      - button "请求核心确认并提交" [ref=e37] [cursor=pointer]
      - paragraph [ref=e38]: 项目上下文变化，请重新核对操作输入。
      - generic [ref=e39]: "{ \"workspace_id\": \"workspace\", \"result\": { \"status\": \"READY\", \"transport_capabilities\": [ \"native-confirmation/1\" ] } }"
    - generic [ref=e40]:
      - heading "只读查询" [level=2] [ref=e41]
      - generic [ref=e42]:
        - button "问题清单（未关闭）" [ref=e43] [cursor=pointer]
        - button "报告修订" [ref=e44] [cursor=pointer]
        - button "空间诊断" [ref=e45] [cursor=pointer]
        - button "事件续读" [ref=e46] [cursor=pointer]
      - paragraph [ref=e47]: 只读入口：写动作必须经受控用户入口并携带授权，界面点击不等于用户确认。
    - generic [ref=e48]:
      - heading "已提交事件（增量续读）" [level=2] [ref=e49]
      - paragraph [ref=e50]: 尚未取得游标（首次读取从头开始）。
      - paragraph [ref=e51]: 尚未读取事件。
    - generic [ref=e52]:
      - heading "最近回执" [level=2] [ref=e53]
      - generic [ref=e54]: "{ \"workspace_id\": \"workspace\", \"result\": { \"status\": \"READY\", \"transport_capabilities\": [ \"native-confirmation/1\" ] } }"
    - generic [ref=e55]:
      - heading "让 AI 准备什么" [level=2] [ref=e56]
      - textbox "描述本次测试目标，或改为人工/模板创建计划。" [disabled] [ref=e57]
      - button "生成计划草稿" [disabled] [ref=e58]
    - generic [ref=e59]:
      - heading "运行测试" [level=2] [ref=e60]
      - generic [ref=e61]:
        - button "运行快速检查" [disabled] [ref=e62]
        - button "运行所选检查" [disabled] [ref=e63]
        - button "运行完整验证" [disabled] [ref=e64]
    - generic [ref=e65]:
      - heading "下一步" [level=2] [ref=e66]
      - paragraph [ref=e67]: 配置模型或人工创建计划；发布计划后，动作区才会获得真实范围与授权。
    - generic [ref=e68]:
      - heading "详情" [level=2] [ref=e69]
      - generic [ref=e70]:
        - button "计划" [ref=e71] [cursor=pointer]
        - button "测试工作台" [ref=e72] [cursor=pointer]
        - button "问题与报告" [ref=e73] [cursor=pointer]
        - button "设置" [ref=e74] [cursor=pointer]
```

# Test source

```ts
  1  | import { test, expect } from "@playwright/test";
  2  | import { resolve } from "node:path";
  3  | import { postHostMessage } from "./host-outcome";
  4  |
  5  | // Browser/host fixtures only; a fixture cannot grant an actual core approval.
  6  | test("controlled editor preserves intent through refusal and never sends a user-event assertion", async ({ page }) => {
  7  |   await page.addInitScript(() => {
  8  |     (window as unknown as { __sent: unknown[] }).__sent = [];
  9  |     (window as unknown as { acquireVsCodeApi: unknown }).acquireVsCodeApi = () => ({
  10 |       postMessage: (message: unknown) => (window as unknown as { __sent: unknown[] }).__sent.push(message),
  11 |     });
  12 |   });
  13 |   await page.goto("about:blank");
  14 |   await page.setContent("<ai-test-panel></ai-test-panel>");
  15 |   await page.addScriptTag({path:resolve("dist/index.js")});
  16 |   await postHostMessage(page, {kind:"projectContext",project_id:"project",context_id:"scope"});
  17 |   await postHostMessage(page, {kind:"outcome",action:"doctor",status:"ok",
  18 |     response:{workspace_id:"workspace",result:{status:"READY",transport_capabilities:["native-confirmation/1"]}}});
  19 |   await page.getByRole("button", {name:"填写新建项目"}).click();
  20 |   const input=page.getByRole("textbox", {name:"受控操作 JSON"});
> 21 |   const draft=JSON.parse(await input.inputValue());
     |                    ^ SyntaxError: Unexpected end of JSON input
  22 |   expect(draft.parameters.project.workspace_id).toBe("workspace");
  23 |   expect(draft.parameters.project.created_at_commit).toBe("@core-assigned");
  24 |   await page.getByRole("button",{name:"请求核心确认并提交"}).click();
  25 |   const request=await page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.at(-1));
  26 |   expect(request?.kind).toBe("controlledDispatch"); expect(request?.intentId).toBe(draft.intent_id);
  27 |   expect(request?.projectId).toBe("project"); expect(request).not.toHaveProperty("user_confirmed");
  28 |   await postHostMessage(page,{kind:"outcome",action:"save_context",status:"refused",code:"USER_DECLINED",message:"取消"});
  29 |   await expect(page.getByTestId("controlled-entry")).toContainText("USER_DECLINED");
  30 |   expect(JSON.parse(await input.inputValue()).intent_id).toBe(draft.intent_id);
  31 | });
  32 |
  33 | test("old or disconnected core cannot enable the controlled entry", async ({ page }) => {
  34 |   await page.setContent("<ai-test-panel></ai-test-panel>");
  35 |   await page.addScriptTag({path:resolve("dist/index.js")});
  36 |   await expect(page.getByRole("button",{name:"请求核心确认并提交"})).toBeDisabled();
  37 |   await expect(page.getByRole("button",{name:"填写新建项目"})).toBeDisabled();
  38 | });
  39 |
```
