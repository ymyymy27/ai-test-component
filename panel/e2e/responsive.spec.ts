import { expect } from "@playwright/test";

import { createAndRun, test } from "./fixtures";

const widths = [320, 768, 1200];

test("320 / 768 / 1200 宽度下概览与报告无横向滚动", async ({ page }) => {
  await createAndRun(page, "响应式验证", "D:/work/flow-responsive");
  const resultUrl = page.url();
  await page.locator(".report-strip").getByRole("button", { name: "查看报告" }).click();
  await page.waitForURL(/\/reports\//);
  const reportUrl = page.url();
  const overviewUrl = resultUrl.replace(/\/tasks\/[^/]+\/result$/, "/overview");

  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 });
    for (const url of [overviewUrl, reportUrl]) {
      await page.goto(url);
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(scrollWidth, `${url} 在 ${width}px 不应横向滚动`).toBeLessThanOrEqual(width);
    }
  }
});

test("窄屏侧栏是抽屉，Esc 关闭并把焦点还给菜单按钮", async ({ page }) => {
  await createAndRun(page, "抽屉验证", "D:/work/flow-drawer");
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto(page.url().replace(/\/tasks\/[^/]+\/result$/, "/overview"));

  const toggle = page.locator(".sidebar-toggle");
  await expect(toggle).toBeVisible();
  await expect(page.locator("#project-sidebar")).toBeHidden();
  await toggle.click();
  await expect(page.locator("#project-sidebar")).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");

  await page.keyboard.press("Escape");
  await expect(page.locator("#project-sidebar")).toBeHidden();
  await expect(toggle).toBeFocused();
});

test("桌面宽度保留 248px 项目树", async ({ page }) => {
  await createAndRun(page, "桌面验证", "D:/work/flow-desktop");
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.goto(page.url().replace(/\/tasks\/[^/]+\/result$/, "/overview"));
  const width = await page.locator("#project-sidebar").evaluate((element) => {
    return element.getBoundingClientRect().width;
  });
  expect(Math.round(width)).toBe(248);
});
