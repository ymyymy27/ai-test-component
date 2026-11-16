import { createApp } from "vue";

import App from "./App.vue";
import "./styles/tokens.css";
import "./styles/base.css";

async function start(): Promise<void> {
  // 仅开发模式提供静态 mock 宿主，用于视觉检查与截图；生产构建会被移除。
  if (import.meta.env.DEV && window.location.search.includes("mock")) {
    const { installMockHost } = await import("./mock/host");
    installMockHost(new URLSearchParams(window.location.search).get("mock") ?? "result");
  }
  createApp(App).mount("#app");
}

void start();
