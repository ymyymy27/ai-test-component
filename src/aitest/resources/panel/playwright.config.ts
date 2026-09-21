import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  use: { browserName: "chromium", channel: process.env.AITEST_BROWSER_CHANNEL || undefined },
  reporter: "list",
});
