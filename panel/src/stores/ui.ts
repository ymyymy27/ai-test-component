import { computed, ref } from "vue";
import { defineStore } from "pinia";

import type { ThemeChoice } from "@/domain/types";

export interface ToastState {
  message: string;
  tone: "info" | "success" | "error";
}

export interface ConfirmOptions {
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
  requireText?: boolean;
  textLabel?: string;
  placeholder?: string;
}

export interface ConfirmRequest extends ConfirmOptions {
  resolve: (value: string | null) => void;
}

const THEME_KEY = "aitest.theme";
const NARROW_QUERY = "(max-width: 900px)";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function readTheme(): ThemeChoice {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark" || stored === "auto") {
      return stored;
    }
  } catch {
    // 存储不可用时跟随系统。
  }
  return "auto";
}

export const useUiStore = defineStore("ui", () => {
  const theme = ref<ThemeChoice>(readTheme());
  const systemDark = ref(false);
  const sidebarOpen = ref(false);
  const narrow = ref(false);
  const envOpen = ref(false);
  const toast = ref<ToastState | null>(null);
  const toastVisible = ref(false);
  const confirmRequest = ref<ConfirmRequest | null>(null);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  let hideTimer: ReturnType<typeof setTimeout> | null = null;
  let listenersInstalled = false;

  const resolvedTheme = computed<"light" | "dark">(() => {
    if (theme.value === "auto") {
      return systemDark.value ? "dark" : "light";
    }
    return theme.value;
  });

  function applyTheme(): void {
    if (typeof document === "undefined") {
      return;
    }
    document.documentElement.dataset.theme = resolvedTheme.value;
    document.documentElement.dataset.themeChoice = theme.value;
  }

  function setTheme(next: ThemeChoice): void {
    theme.value = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // 主题偏好写不进去不影响使用。
    }
    applyTheme();
  }

  function initTheme(): void {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return;
    }
    systemDark.value = window.matchMedia(DARK_QUERY).matches;
    narrow.value = window.matchMedia(NARROW_QUERY).matches;
    applyTheme();
    if (listenersInstalled) {
      return;
    }
    listenersInstalled = true;
    const darkQuery = window.matchMedia(DARK_QUERY);
    darkQuery.addEventListener?.("change", (event) => {
      systemDark.value = event.matches;
      applyTheme();
    });
    const narrowQuery = window.matchMedia(NARROW_QUERY);
    narrowQuery.addEventListener?.("change", (event) => {
      narrow.value = event.matches;
      if (event.matches) {
        sidebarOpen.value = false;
      }
    });
  }

  function openSidebar(): void {
    sidebarOpen.value = true;
  }

  function closeSidebar(): void {
    sidebarOpen.value = false;
  }

  function toggleSidebar(): void {
    sidebarOpen.value = !sidebarOpen.value;
  }

  function toggleEnv(): void {
    envOpen.value = !envOpen.value;
  }

  function closeEnv(): void {
    envOpen.value = false;
  }

  function showToast(message: string, tone: ToastState["tone"] = "info"): void {
    if (toastTimer) {
      clearTimeout(toastTimer);
    }
    if (hideTimer) {
      clearTimeout(hideTimer);
    }
    toast.value = { message, tone };
    toastVisible.value = true;
    toastTimer = setTimeout(() => {
      toastVisible.value = false;
      hideTimer = setTimeout(() => {
        toast.value = null;
      }, 180);
    }, 3400);
  }

  function askConfirm(options: ConfirmOptions): Promise<string | null> {
    return new Promise<string | null>((resolve) => {
      confirmRequest.value = { ...options, resolve };
    });
  }

  function resolveConfirm(value: string | null): void {
    const request = confirmRequest.value;
    confirmRequest.value = null;
    request?.resolve(value);
  }

  return {
    theme,
    systemDark,
    sidebarOpen,
    narrow,
    envOpen,
    toast,
    toastVisible,
    confirmRequest,
    resolvedTheme,
    initTheme,
    setTheme,
    openSidebar,
    closeSidebar,
    toggleSidebar,
    toggleEnv,
    closeEnv,
    showToast,
    askConfirm,
    resolveConfirm,
  };
});
