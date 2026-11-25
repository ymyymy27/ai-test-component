import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { onHostTheme, readHostTheme, type HostProgressEvent } from "@/host/bridge";

import type { ThemeChoice } from "@/domain/types";

export interface ActiveTaskState {
  token: string;
  command: string;
  label: string;
  message: string;
  done?: number;
  total?: number;
  cancel?: () => void;
}

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
/** 全站统一 3 断点口径：抽屉折叠 900px / 中屏收敛 768px / 窄屏卡片化 620px */
export const BREAKPOINT_DRAWER = 900;
export const BREAKPOINT_MEDIUM = 768;
export const BREAKPOINT_COMPACT = 620;
const NARROW_QUERY = `(max-width: ${BREAKPOINT_DRAWER}px)`;
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

function hasVscodeDarkClass(): boolean {
  if (typeof document === "undefined" || !document.body) {
    return false;
  }
  return (
    document.body.classList.contains("vscode-dark") ||
    document.body.classList.contains("vscode-high-contrast")
  );
}

const hostThemeSyncs = new Set<(theme: "dark" | "light") => void>();
const vscodeThemeSyncs = new Set<(dark: boolean) => void>();
let globalThemeListenersInstalled = false;

function ensureGlobalThemeListeners(): void {
  if (globalThemeListenersInstalled || typeof window === "undefined") {
    return;
  }
  globalThemeListenersInstalled = true;
  onHostTheme((next) => {
    hostThemeSyncs.forEach((cb) => cb(next));
  });
  if (typeof MutationObserver !== "undefined" && typeof document !== "undefined" && document.body) {
    const observer = new MutationObserver(() => {
      const dark = hasVscodeDarkClass();
      vscodeThemeSyncs.forEach((cb) => cb(dark));
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["class"],
    });
  }
}

export const useUiStore = defineStore("ui", () => {
  const theme = ref<ThemeChoice>(readTheme());
  const systemDark = ref(false);
  const hostTheme = ref<"dark" | "light" | null>(readHostTheme());
  const vscodeDark = ref(hasVscodeDarkClass());
  const sidebarOpen = ref(false);
  const narrow = ref(false);
  const envOpen = ref(false);
  const commandPaletteOpen = ref(false);
  const quickCheckDrawerOpen = ref(false);
  const newTaskDrawerOpen = ref(false);
  const toast = ref<ToastState | null>(null);
  const toastVisible = ref(false);
  const confirmRequest = ref<ConfirmRequest | null>(null);
  const activeTask = ref<ActiveTaskState | null>(null);
  let taskSeq = 0;
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  let hideTimer: ReturnType<typeof setTimeout> | null = null;
  const toastQueue: ToastState[] = [];
  let listenersInstalled = false;

  const resolvedTheme = computed<"light" | "dark">(() => {
    if (theme.value === "auto") {
      if (hostTheme.value) {
        return hostTheme.value;
      }
      if (vscodeDark.value) {
        return "dark";
      }
      return systemDark.value ? "dark" : "light";
    }
    return theme.value;
  });

  function applyTheme(): void {
    if (typeof document === "undefined") {
      return;
    }
    document.documentElement.dataset.theme = resolvedTheme.value;
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
    hostTheme.value = readHostTheme();
    vscodeDark.value = hasVscodeDarkClass();
    systemDark.value = window.matchMedia(DARK_QUERY).matches;
    narrow.value = window.matchMedia(NARROW_QUERY).matches;
    applyTheme();
    if (listenersInstalled) {
      return;
    }
    listenersInstalled = true;
    ensureGlobalThemeListeners();
    hostThemeSyncs.add((next) => {
      hostTheme.value = next;
      applyTheme();
    });
    vscodeThemeSyncs.add((dark) => {
      vscodeDark.value = dark;
      applyTheme();
    });
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

  function openCommandPalette(): void {
    commandPaletteOpen.value = true;
  }

  function closeCommandPalette(): void {
    commandPaletteOpen.value = false;
  }

  function toggleCommandPalette(): void {
    commandPaletteOpen.value = !commandPaletteOpen.value;
  }

  function openQuickCheckDrawer(): void {
    quickCheckDrawerOpen.value = true;
  }

  function closeQuickCheckDrawer(): void {
    quickCheckDrawerOpen.value = false;
  }

  function openNewTaskDrawer(): void {
    newTaskDrawerOpen.value = true;
  }

  function closeNewTaskDrawer(): void {
    newTaskDrawerOpen.value = false;
  }

  function toggleTheme(): void {
    setTheme(resolvedTheme.value === "dark" ? "light" : "dark");
  }

  function clearToastTimers(): void {
    if (toastTimer) {
      clearTimeout(toastTimer);
      toastTimer = null;
    }
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function presentToast(next: ToastState): void {
    toast.value = next;
    toastVisible.value = true;
    // 错误要保留到用户主动关闭；自动消失会让对话框内的保存失败无从追查。
    if (next.tone === "error") {
      toastTimer = null;
      return;
    }
    toastTimer = setTimeout(dismissToast, 3400);
  }

  function dismissToast(): void {
    if (!toastVisible.value) {
      return;
    }
    clearToastTimers();
    toastVisible.value = false;
    hideTimer = setTimeout(() => {
      toast.value = null;
      const next = toastQueue.shift();
      if (next) {
        presentToast(next);
      }
    }, 180);
  }

  function showToast(message: string, tone: ToastState["tone"] = "info"): void {
    const next: ToastState = { message, tone };
    if (toastVisible.value && toast.value?.tone === "error" && tone !== "error") {
      toastQueue.push(next);
      if (toastQueue.length > 3) {
        toastQueue.shift();
      }
      return;
    }
    clearToastTimers();
    presentToast(next);
  }

  function askConfirm(options: ConfirmOptions): Promise<string | null> {
    return new Promise<string | null>((resolve) => {
      // 并发确认会把第一个 Promise 永久挂起；先让旧请求明确取消。
      const previous = confirmRequest.value;
      if (previous) {
        previous.resolve(null);
      }
      confirmRequest.value = { ...options, resolve };
    });
  }

  function resolveConfirm(value: string | null): void {
    const request = confirmRequest.value;
    confirmRequest.value = null;
    request?.resolve(value);
  }

  function startTask(
    command: string,
    label: string,
    cancel?: (() => void) | null,
  ): string {
    taskSeq += 1;
    const token = `task_${taskSeq}`;
    activeTask.value = {
      token,
      command,
      label,
      message: "正在处理...",
      ...(cancel ? { cancel } : {}),
    };
    return token;
  }

  function updateTaskProgress(token: string, event: HostProgressEvent): void {
    if (!activeTask.value || activeTask.value.token !== token) {
      return;
    }
    activeTask.value = {
      ...activeTask.value,
      message: event.message ?? (event.stage ? `阶段：${event.stage}` : activeTask.value.message),
      ...(typeof event.done === "number" ? { done: event.done } : {}),
      ...(typeof event.total === "number" ? { total: event.total } : {}),
    };
  }

  function finishTask(token: string): void {
    if (activeTask.value?.token === token) {
      activeTask.value = null;
    }
  }

  return {
    theme,
    systemDark,
    sidebarOpen,
    narrow,
    envOpen,
    commandPaletteOpen,
    quickCheckDrawerOpen,
    newTaskDrawerOpen,
    toast,
    toastVisible,
    confirmRequest,
    activeTask,
    resolvedTheme,
    initTheme,
    setTheme,
    toggleTheme,
    openSidebar,
    closeSidebar,
    toggleSidebar,
    toggleEnv,
    closeEnv,
    openCommandPalette,
    closeCommandPalette,
    toggleCommandPalette,
    openQuickCheckDrawer,
    closeQuickCheckDrawer,
    openNewTaskDrawer,
    closeNewTaskDrawer,
    showToast,
    dismissToast,
    askConfirm,
    resolveConfirm,
    startTask,
    updateTaskProgress,
    finishTask,
  };
});
