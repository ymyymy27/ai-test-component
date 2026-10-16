import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { postHostMessage } from "./host-outcome";

// Browser/host fixtures only; a fixture cannot grant an actual core approval.
test("controlled editor preserves intent through refusal and never sends a user-event assertion", async ({ page }) => {
  const errors: string[] = []; page.on("pageerror", error=>errors.push(error.message));
  await page.addInitScript(() => {
    (window as unknown as { __sent: unknown[] }).__sent = [];
    (window as unknown as { acquireVsCodeApi: unknown }).acquireVsCodeApi = () => ({
      postMessage: (message: unknown) => (window as unknown as { __sent: unknown[] }).__sent.push(message),
    });
  });
  await page.goto("about:blank");
  await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.addScriptTag({path:resolve("dist/index.js")});
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await postHostMessage(page, {kind:"projectContext",project_id:"project",context_id:"scope"});
  await postHostMessage(page, {kind:"outcome",action:"doctor",status:"ok",
    response:{workspace_id:"workspace",result:{status:"READY",transport_capabilities:["native-confirmation/1"]}}});
  await page.getByRole("button", {name:"填写新建项目"}).click();
  await expect(page.getByTestId("controlled-entry")).toContainText("请填写项目名称和实际测试目标");
  await page.getByRole("textbox",{name:"项目名称",exact:true}).fill("真实项目");
  await page.getByRole("textbox",{name:"实际测试目标",exact:true}).fill("核对订单创建与查询");
  await page.getByRole("button", {name:"填写新建项目"}).click();
  const input=page.getByRole("textbox", {name:"受控操作 JSON"});
  const draft=JSON.parse(await input.inputValue());
  expect(draft.parameters.project.workspace_id).toBe("workspace");
  expect(draft.parameters.project.name).toBe("真实项目");
  expect(draft.parameters.project.goal).toBe("核对订单创建与查询");
  expect(draft.parameters.project.created_at_commit).toBe("@core-assigned");
  await page.getByRole("button",{name:"请求核心确认并提交"}).click();
  const request=await page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.at(-1));
  expect(request?.kind).toBe("controlledDispatch"); expect(request?.intentId).toBe(draft.intent_id);
  expect(request?.contextId).toBe("scope");
  expect(request?.projectId).toBe("project"); expect(request).not.toHaveProperty("user_confirmed");
  await postHostMessage(page,{kind:"outcome",action:"save_context",status:"refused",code:"USER_DECLINED",message:"取消"});
  await expect(page.getByTestId("controlled-entry")).toContainText("USER_DECLINED");
  expect(JSON.parse(await input.inputValue()).intent_id).toBe(draft.intent_id);
  expect(errors).toEqual([]);
});

test("old or disconnected core cannot enable the controlled entry", async ({ page }) => {
  await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.addScriptTag({path:resolve("dist/index.js")});
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await expect(page.getByRole("button",{name:"请求核心确认并提交"})).toBeDisabled();
  await expect(page.getByRole("button",{name:"填写新建项目"})).toBeDisabled();
});
