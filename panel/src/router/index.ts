import {
  createRouter,
  createWebHashHistory,
  createWebHistory,
  type RouteLocationNormalized,
  type RouterHistory,
} from "vue-router";

import { findTask, runningTask } from "@/domain/derive";
import type { Task } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

function projectIdOf(to: RouteLocationNormalized): string {
  return String(to.params.projectId ?? "");
}

function taskIdOf(to: RouteLocationNormalized): string {
  return String(to.params.taskId ?? "");
}

function notFound(to: RouteLocationNormalized): {
  name: "not-found";
  params: { pathMatch: string[] };
  query: RouteLocationNormalized["query"];
  hash: string;
} {
  return {
    name: "not-found",
    params: { pathMatch: to.path.split("/").filter(Boolean) },
    query: to.query,
    hash: to.hash,
  };
}

/** Plan route redirect by task stage; goal tasks without a plan stay on plan. */
export function planRedirectFor(
  task: Task,
): { name: "scope" | "run" | "result"; params: Record<string, string> } | null {
  const params = { projectId: task.projectId, taskId: task.id };
  if (task.stage === "running") {
    return { name: "run", params };
  }
  if (task.stage === "result") {
    return { name: "result", params };
  }
  if (task.stage === "scope" || task.stage === "fresh") {
    if (task.origin === "goal" && task.plans.length === 0) {
      return null;
    }
    return { name: "scope", params };
  }
  return null;
}

function createHistory(): RouterHistory {
  if (typeof window === "undefined") {
    return createWebHistory("/");
  }
  // Web 预览使用 history；webview/file 需要 hash 才能在刷新时重新打开同一个入口文档。
  // 不同宿主 webview 的 location.protocol 可能是 vscode-webview: 或 https:，
  // 因此同时用 acquireVsCodeApi 判断是否运行在宿主 webview 中。
  const hostWebview =
    typeof (window as { acquireVsCodeApi?: unknown }).acquireVsCodeApi === "function";
  const webview =
    window.location.protocol === "vscode-webview:" ||
    window.location.protocol === "file:" ||
    hostWebview;
  return webview ? createWebHashHistory() : createWebHistory("/");
}

const router = createRouter({
  history: createHistory(),
  routes: [
    {
      path: "/",
      name: "root",
      component: () => import("@/views/RootRedirectView.vue"),
      meta: { title: "" },
    },
    {
      path: "/setup",
      name: "setup",
      component: () => import("@/views/SetupView.vue"),
      meta: { title: "建立项目" },
    },
    {
      path: "/projects/:projectId",
      component: () => import("@/views/ProjectLayout.vue"),
      children: [
        {
          path: "",
          redirect: (to) => ({
            name: "overview",
            params: { projectId: to.params.projectId },
          }),
        },
        {
          path: "overview",
          name: "overview",
          component: () => import("@/views/OverviewView.vue"),
          meta: { title: "项目概览" },
        },
        {
          path: "tasks/new",
          name: "new-task",
          component: () => import("@/views/NewTaskView.vue"),
          meta: { title: "开始测试" },
        },
        {
          path: "tasks/:taskId/details",
          name: "task-details",
          component: () => import("@/views/TaskDetailsView.vue"),
          meta: { title: "任务资料" },
        },
        {
          path: "tasks/:taskId/scope",
          name: "scope",
          component: () => import("@/views/ScopeView.vue"),
          meta: { title: "本次范围" },
        },
        {
          path: "tasks/:taskId/plan",
          name: "plan",
          component: () => import("@/views/PlanView.vue"),
          meta: { title: "计划" },
        },
        {
          path: "tasks/:taskId/run",
          name: "run",
          component: () => import("@/views/RunView.vue"),
          meta: { title: "运行" },
        },
        {
          path: "tasks/:taskId/result",
          name: "result",
          component: () => import("@/views/ResultView.vue"),
          meta: { title: "结果" },
        },
        {
          path: "quick-check",
          name: "quick-check",
          component: () => import("@/views/QuickCheckView.vue"),
          meta: { title: "快速检查" },
        },
        {
          path: "issues",
          name: "issues",
          component: () => import("@/views/IssueListView.vue"),
          meta: { title: "问题" },
        },
        {
          path: "issues/:issueId",
          name: "issue-detail",
          component: () => import("@/views/IssueDetailView.vue"),
          meta: { title: "问题详情" },
        },
        {
          path: "history",
          name: "history",
          component: () => import("@/views/HistoryView.vue"),
          meta: { title: "运行记录" },
        },
        {
          path: "settings",
          name: "settings",
          component: () => import("@/views/SettingsView.vue"),
          meta: { title: "设置" },
        },
        {
          path: "reports/:reportId",
          name: "report",
          component: () => import("@/views/ReportView.vue"),
          meta: { title: "测试报告" },
        },
      ],
    },
    {
      path: "/:pathMatch(.*)*",
      name: "not-found",
      component: () => import("@/views/NotFoundView.vue"),
      meta: { title: "页面不存在" },
    },
  ],
  scrollBehavior(to, _from, savedPosition) {
    if (savedPosition) {
      return savedPosition;
    }
    if (to.hash) {
      if (typeof document === "undefined") {
        return { top: 0 };
      }
      // 目标元素可能由异步视图渲染；找不到时短暂重试，最后退回顶部而不是抛错。
      return new Promise<{ el?: string; top: number }>((resolve) => {
        let attempts = 8;
        const find = (): void => {
          const element = document.querySelector(to.hash);
          if (element) {
            resolve({ el: to.hash, top: 16 });
            return;
          }
          attempts -= 1;
          if (attempts <= 0) {
            resolve({ top: 0 });
            return;
          }
          window.setTimeout(find, 60);
        };
        find();
      });
    }
    return { top: 0 };
  },
});

router.beforeEach(async (to) => {
  if (to.path.endsWith("/index.html")) {
    return { path: "/" };
  }
  const store = useWorkspaceStore();
  if (!store.initialized && store.status !== "error") {
    await store.bootstrap();
  }
  const ui = useUiStore();
  const projectId = projectIdOf(to);
  if (!projectId) {
    return true;
  }
  if (store.status === "error") {
    // 数据不可用不是“URL 错误”；回到入口显示可重试的错误页。
    return { name: "root" };
  }
  let project = store.projects.find((entry) => entry.id === projectId) ?? null;
  if (!project) {
    return notFound(to);
  }
  if (store.currentProjectId !== projectId) {
    try {
      // 摘要项目没有任务明细，必须用完整项目重新做后续守卫判断。
      project = (await store.setCurrentProject(projectId)) ?? project;
    } catch {
      ui.showToast("切换项目失败，请重试。", "error");
      return false;
    }
  }
  const activeRunTask = runningTask(project);
  if (activeRunTask && ["new-task", "quick-check"].includes(String(to.name))) {
    ui.showToast(
      `有任务正在运行：${activeRunTask.title || "未命名"}。完成或停止后再切换。`,
      "info",
    );
    return {
      name: "run",
      params: { projectId, taskId: activeRunTask.id },
    };
  }
  const taskId = taskIdOf(to);
  if (!taskId) {
    return true;
  }
  const task = findTask(project, taskId);
  if (!task) {
    return notFound(to);
  }
  if (
    activeRunTask &&
    activeRunTask.id !== taskId &&
    ["scope", "plan", "run", "result", "task-details"].includes(String(to.name))
  ) {
    ui.showToast(
      `有任务正在运行：${activeRunTask.title || "未命名"}。完成或停止后再切换。`,
      "info",
    );
    return {
      name: "run",
      params: { projectId, taskId: activeRunTask.id },
    };
  }
  if (to.name === "scope" && task.stage !== "scope" && task.stage !== "fresh") {
    if (task.stage === "planned") {
      return { name: "plan", params: { projectId, taskId } };
    }
    if (task.stage === "running") {
      return { name: "run", params: { projectId, taskId } };
    }
    return { name: "result", params: { projectId, taskId } };
  }
  if (to.name === "plan") {
    const redirect = planRedirectFor(task);
    if (redirect) {
      return redirect;
    }
  }
  if (to.name === "run" && task.stage !== "running") {
    if (task.stage === "result") {
      return { name: "result", params: { projectId, taskId } };
    }
    return { name: "plan", params: { projectId, taskId } };
  }
  if (to.name === "result" && task.stage !== "result") {
    if (task.stage === "running") {
      return { name: "run", params: { projectId, taskId } };
    }
    if (task.stage === "planned") {
      return { name: "plan", params: { projectId, taskId } };
    }
    return { name: "scope", params: { projectId, taskId } };
  }
  return true;
});

router.afterEach((to) => {
  const ui = useUiStore();
  ui.closeSidebar();
  ui.closeEnv();
  if (typeof document !== "undefined") {
    const title = typeof to.meta.title === "string" ? to.meta.title : "";
    document.title = title ? `${title} · AI 测试` : "AI 测试";
  }
});

const CHUNK_LOAD_ERROR =
  /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i;

router.onError((error, to) => {
  if (!to || !CHUNK_LOAD_ERROR.test(String(error?.message ?? error))) {
    return;
  }
  // 开发服务器重启或发布后，旧页面拿不到懒加载 chunk：地址栏已变但主内容不切换，
  // 用户会以为按钮没反应。整页打开目标地址；同一地址 30 秒内只重载一次，避免循环。
  const target = router.resolve(to);
  const key = `aitest.chunk-reload.${target.href}`;
  try {
    const last = Number(sessionStorage.getItem(key) ?? 0);
    if (last && Date.now() - last < 30_000) {
      return;
    }
    sessionStorage.setItem(key, String(Date.now()));
  } catch {
    // 无法记录时仍然重载一次；失败会显示浏览器的加载错误。
  }
  window.location.assign(target.href);
});

export default router;
