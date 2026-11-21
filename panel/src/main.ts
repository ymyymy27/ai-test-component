import { createApp } from "vue";
import { createPinia } from "pinia";

import App from "./App.vue";
import router from "./router";
import { useUiStore } from "./stores/ui";
import { useWorkspaceStore } from "./stores/workspace";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";
import "./styles/motion.css";

async function start(): Promise<void> {
  const app = createApp(App);
  const pinia = createPinia();
  app.use(pinia);
  useUiStore().initTheme();
  app.config.errorHandler = (error) => {
    console.error(error);
    useUiStore().showToast("界面出现异常，请刷新或重试。", "error");
  };
  app.use(router);
  app.mount("#app");
  try {
    await useWorkspaceStore().bootstrap();
    await router.isReady();
  } catch (error) {
    console.error(error);
    useUiStore().showToast("应用启动失败，请刷新重试。", "error");
  }
}

void start();
