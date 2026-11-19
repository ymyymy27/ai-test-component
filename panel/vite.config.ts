import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const extension = mode === "extension";
  return {
    // 宿主 webview 的资源 URI 与 cspSource 可能不同源；扩展构建打成单文件，
    // 由扩展把 panel.js / panel.css 内联进 HTML，避免依赖资源 URL。
    base: extension ? "./" : "/",
    plugins: [vue()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    build: {
      outDir: "dist",
      assetsDir: "assets",
      sourcemap: false,
      target: "es2020",
      ...(extension
        ? {
            cssCodeSplit: false,
            rollupOptions: {
              output: {
                format: "iife" as const,
                inlineDynamicImports: true,
                entryFileNames: "panel.js",
                assetFileNames: (assetInfo) =>
                  assetInfo.name?.endsWith(".css") ? "panel.css" : "assets/[name][extname]",
              },
            },
          }
        : {}),
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./tests/setup.ts"],
      include: ["tests/**/*.spec.ts"],
      css: false,
    },
  };
});
