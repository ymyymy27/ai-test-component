import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  reporter: process.env.CI ? [["list"], ["junit"]] : "list",
  use: { browserName: "chromium", channel: process.env.AITEST_BROWSER_CHANNEL || undefined,
    trace: "retain-on-failure", screenshot: "only-on-failure" },
});
