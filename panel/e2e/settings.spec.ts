import { expect } from "@playwright/test";

import { createProject, test } from "./fixtures";

test("设置保存项目资料、运行配置、规则、主题与备份", async ({ page }) => {
  const projectId = await createProject(page, "设置验证", "D:/work/flow-settings");
  await page.goto(`/projects/${projectId}/settings`);

  await expect(page.locator(".settings-index")).toContainText("项目资料");
  await expect(page.locator(".settings-index")).toContainText("运行配置");
  await expect(page.locator(".settings-index")).toContainText("环境");
  await expect(page.locator(".settings-index")).toContainText("项目规则");
  await expect(page.locator(".settings-index")).toContainText("GitHub");
  await expect(page.locator(".settings-index")).toContainText("备份");

  await page.fill("#project-name", "设置验证-改名");
  await page.fill("#project-goal", "验证设置保存");
  await page.getByRole("button", { name: "保存项目资料" }).click();
  await expect(page.locator(".page-subtitle")).toContainText("设置验证-改名");

  await page.locator(".settings-index").getByRole("tab", { name: "运行配置" }).click();
  await page.fill("#config-build", "pnpm build");
  await page.fill("#config-test", "pnpm test");
  await page.getByRole("button", { name: "保存运行配置" }).click();
  await expect(page.locator(".toast")).toContainText("运行配置已保存");

  await page.fill("#config-json", '{"services":{"password":"x"}}');
  await page.getByRole("button", { name: "解析并填入表单" }).click();
  await expect(page.locator("#settings-config")).toContainText("不能出现凭据字段");
  await page.fill("#config-json", '{"l1":{"build":"pnpm build"}}');
  await page.getByRole("button", { name: "解析并填入表单" }).click();
  await page.getByRole("button", { name: "保存运行配置" }).click();
  await expect(page.locator(".toast")).toContainText("运行配置已保存");

  await page.locator(".settings-index").getByRole("tab", { name: "GitHub" }).click();
  await page.fill("#github-repo", "team/service");
  await page.fill("#github-commit", "abc123");
  await page.fill("#github-files", "src/main.py");
  await page.fill("#github-checks", "CI / pytest");
  await page.getByRole("button", { name: "保存缓存快照" }).click();
  await expect(page.locator("#settings-github")).toContainText("缓存快照，未确认最新");
  await expect(page.locator("#settings-github")).toContainText("team/service");
  await page.getByRole("button", { name: "清除缓存快照" }).click();
  await page.locator(".dialog-confirm").click();
  await expect(page.locator("#settings-github")).toContainText("未连接 GitHub");

  await page.locator(".settings-index").getByRole("tab", { name: "项目规则" }).click();
  await page.fill("#rule-name", "接口检查");
  await page.fill("#rule-version", "2");
  await page.fill("#rule-steps", "按接口契约执行检查。");
  await page.fill("#rule-ai", "预期引用验收项原文");
  await page.fill("#rule-evidence", "接口响应");
  await page.getByRole("button", { name: "保存规则版本" }).click();
  await expect(page.locator("#settings-rules")).toContainText("接口检查");
  await page
    .locator(".rule-row")
    .filter({ hasText: "接口检查" })
    .getByRole("button", { name: "绑定" })
    .click();
  await expect(page.locator("#settings-rules")).toContainText("当前绑定：接口检查 v2");

  await page.locator(".settings-index").getByRole("tab", { name: "外观" }).click();
  await page.getByRole("button", { name: "深色" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "跟随系统" }).click();

  await page.locator(".settings-index").getByRole("tab", { name: "GitHub" }).click();
  await expect(page.locator("#settings-github")).toContainText("未连接 GitHub");
  await page.locator(".settings-index").getByRole("tab", { name: "数据边界" }).click();
  await expect(page.locator("#settings-boundary")).toContainText("同一项目同时只运行一个任务");

  await page.locator(".settings-index").getByRole("tab", { name: "备份" }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "创建备份" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/aitest-backup-.*\.zip/);
  await expect(page.locator(".toast")).toContainText("已导出备份");

  const path = await download.path();
  if (!path) {
    throw new Error("backup path missing");
  }
  await page.setInputFiles('input[type="file"][accept*="zip"]', path);
  await expect(page.locator("#confirm-dialog-title")).toBeVisible();
  await page.locator(".dialog-confirm").click();
  await expect(page.locator(".toast")).toContainText("备份已恢复");
});

test("高级 JSON 未点解析也能随保存一起生效", async ({ page }) => {
  const projectId = await createProject(page, "JSON 保存验证", "D:/work/flow-settings-json");
  await page.goto(`/projects/${projectId}/settings`);
  await page.locator(".settings-index").getByRole("tab", { name: "运行配置" }).click();
  await page.fill("#config-json", '{"l1":{"build":"echo json-direct"}}');
  await page.getByRole("button", { name: "保存运行配置" }).click();
  await expect(page.locator(".toast")).toContainText("运行配置已保存");

  await page.reload();
  await page.locator(".settings-index").getByRole("tab", { name: "运行配置" }).click();
  await expect(page.locator("#config-build")).toHaveValue("echo json-direct");
});

test("设置分区可从地址恢复并支持方向键切换", async ({ page }) => {
  const projectId = await createProject(page, "设置分区验证", "D:/work/flow-settings-tabs");
  await page.goto(`/projects/${projectId}/settings?section=settings-backup`);
  await expect(page.locator("#settings-backup")).toBeVisible();
  const active = page.locator('.settings-index [role="tab"][aria-selected="true"]');
  await expect(active).toHaveText("备份");

  await active.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page).toHaveURL(/section=settings-profile/);
  await expect(page.locator("#settings-profile")).toBeVisible();
  await expect(page.locator('.settings-index [role="tab"][aria-selected="true"]')).toHaveText(
    "项目资料",
  );
});
