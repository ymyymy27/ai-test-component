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
  const workspace = useWorkspaceStore();
  await workspace.bootstrap();
  app.use(router);
  await router.isReady();
  app.mount("#app");
}

void start();
