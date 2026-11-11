import { test, expect } from "@playwright/test";
import { connect, mountPanel, sentMessages } from "./support";

test("standalone preview is honest about the missing editor host", async ({ page }) => {
  await mountPanel(page, false);
  await expect(page.getByText("未连接编辑器宿主")).toBeVisible();
  await expect(page.getByText("独立浏览器只用于界面预览")).toBeVisible();
  for (const label of ["本次测试", "问题与报告", "设置"]) {
    await expect(page.getByRole("button", { name: label, exact: true }).first()).toBeVisible();
  }
});

test("connected project without a run starts from preparation", async ({ page }) => {
  await mountPanel(page);
  await connect(page);
  await expect(page.getByText("项目已就绪")).toBeVisible();
  await expect(page.getByRole("button", { name: "继续准备测试", exact: true })).toBeVisible();
});

test("navigation renders each page without inventing facts", async ({ page }) => {
  await mountPanel(page);
  await connect(page);

  await page.getByRole("button", { name: "计划", exact: true }).click();
  await expect(page.getByText("已发布的检查计划")).toBeVisible();

  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await expect(page.getByText("按步骤执行与核验")).toBeVisible();
  await expect(page.getByText("还没有可用的运行")).toBeVisible();

  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await expect(page.getByText("报告快照与问题记录")).toBeVisible();

  await page.getByRole("button", { name: "设置", exact: true }).first().click();
  await expect(page.getByText("模型、连接与本地数据")).toBeVisible();

  await page.getByRole("button", { name: "诊断", exact: true }).first().click();
  await expect(page.getByText("连接、依赖与高级操作")).toBeVisible();
});

test("connected panel never sends a write action through the read-only dispatch channel", async ({ page }) => {
  await mountPanel(page);
  await connect(page);
  const messages = await sentMessages(page);
  const writeActions = new Set([
    "save_context", "save_binding", "save_environment", "save_case", "save_acceptance",
    "save_model_outbound_policy", "analyze_project", "generate_draft", "publish_plan",
    "prepare_run", "register_run", "prepare_execution", "authorize_step", "execute_step",
    "verify_pending", "create_report", "export_report",
  ]);
  for (const message of messages) {
    const action = typeof message["action"] === "string" ? message["action"] : "";
    if (!writeActions.has(action)) continue;
    expect(message["kind"]).not.toBe("dispatch");
  }
});
