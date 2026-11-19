import {
  createRouter,
  createWebHashHistory,
  createWebHistory,
  type RouteLocationNormalized,
  type RouterHistory,
} from "vue-router";

import { findTask, runningTask } from "@/domain/derive";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

function projectIdOf(to: RouteLocationNormalized): string {
  return String(to.params.projectId ?? "");
}

function taskIdOf(to: RouteLocationNormalized): string {
  return String(to.params.taskId ?? "");
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
      meta: { title: "AI 测试" },
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
          meta: { title: "新建任务" },
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
          meta: { title: "历史" },
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
      return { el: to.hash, top: 16 };
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
  const project = store.projects.find((entry) => entry.id === projectId);
  if (!project) {
    return { name: "not-found" };
  }
  if (store.currentProjectId !== projectId) {
    await store.setCurrentProject(projectId);
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
    return { name: "not-found" };
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
    if (task.stage === "scope" || task.stage === "fresh") {
      return { name: "scope", params: { projectId, taskId } };
    }
    if (task.stage === "running") {
      return { name: "run", params: { projectId, taskId } };
    }
    if (task.stage === "result") {
      return { name: "result", params: { projectId, taskId } };
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

export default router;
