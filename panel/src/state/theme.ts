/** 主题：只影响本机视觉；默认跟随系统，不改变任何业务事实。 */
import { ref } from "vue";

export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEMES: { id: ThemeMode; name: string; summary: string }[] = [
  { id: "system", name: "跟随系统", summary: "与编辑器的明暗保持一致" },
  { id: "light", name: "浅色", summary: "白色工作台，适合亮环境" },
  { id: "dark", name: "深色", summary: "深灰工作台，适合暗环境" },
];

const STORAGE_KEY = "aitest.theme";

function readMode(): ThemeMode {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" || value === "system" ? value : "system";
  } catch {
    return "system";
  }
}

function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "light";
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export const themeMode = ref<ThemeMode>(readMode());
export const resolvedTheme = ref<ResolvedTheme>(
  themeMode.value === "system" ? systemTheme() : themeMode.value,
);

function apply(mode: ThemeMode): void {
  themeMode.value = mode;
  resolvedTheme.value = mode === "system" ? systemTheme() : mode;
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = resolvedTheme.value;
  }
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // 宿主禁用 localStorage 时只影响本次会话。
  }
}

export function setThemeMode(mode: ThemeMode): void {
  apply(mode);
}

export function toggleTheme(): void {
  apply(resolvedTheme.value === "light" ? "dark" : "light");
}

export function initTheme(): void {
  apply(themeMode.value);
  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    query.addEventListener?.("change", () => {
      if (themeMode.value === "system") {
        apply("system");
      }
    });
  }
}
