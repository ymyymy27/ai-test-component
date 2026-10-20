/** Candidate editor host adapter for the single shared AI test surface.
 *
 * The sidebar consumes host-projected facts. Detail pages are opened as editor
 * tabs beside it. No webview click is promoted to a write/confirmation. */
import * as vscode from "vscode";
import { randomBytes, randomUUID } from "node:crypto";

import { dispatchReadOnly, ensureCore, type DispatchOutcome, type DispatchRequest } from "./core-bridge";

const PANEL_VIEW_TYPE = "aitest.panel";
const DISPATCH_TIMEOUT_MS = 30_000;
const DETAIL_VIEWS = new Set(["ai-records", "driver", "environment", "issue", "records", "regression", "result", "running", "plan"]);
const detailPanels = new Map<string, vscode.WebviewPanel>();

interface PanelTarget { readonly webview: vscode.Webview; }
interface HostSettings {
  readonly python: string;
  readonly workspaceRoot: string;
  readonly projectId: string;
  readonly demoMode: boolean;
}

function readSettings(): HostSettings {
  const configuration = vscode.workspace.getConfiguration("aitest");
  return {
    python: configuration.get<string>("pythonPath", "python"),
    workspaceRoot: configuration.get<string>("workspaceRoot", ""),
    projectId: configuration.get<string>("projectId", ""),
    demoMode: configuration.get<boolean>("demoMode", false),
  };
}

class PanelViewProvider implements vscode.WebviewViewProvider {
  public constructor(private readonly context: vscode.ExtensionContext) {}

  public resolveWebviewView(view: vscode.WebviewView): void {
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "dist")],
    };
    view.webview.html = panelHtml(this.context, view.webview, "<ai-test-panel></ai-test-panel>", false);
    view.webview.onDidReceiveMessage((message: unknown) => {
      void handleWebviewMessage(view, message, this.context);
    });
  }
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(PANEL_VIEW_TYPE, new PanelViewProvider(context), {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.commands.registerCommand("aitest.openPanel", async () => {
      if (!vscode.workspace.isTrusted) {
        await vscode.window.showWarningMessage("请先通过编辑器确认工作区信任。");
        return;
      }
      await vscode.commands.executeCommand(`${PANEL_VIEW_TYPE}.focus`);
    }),
    vscode.commands.registerCommand("aitest.openReferencePreview", async () => {
      if (!vscode.workspace.isTrusted) {
        await vscode.window.showWarningMessage("请先通过编辑器确认工作区信任。");
        return;
      }
      openReferencePreview(context);
    }),
  );
}

function panelHtml(
  context: vscode.ExtensionContext,
  webview: vscode.Webview,
  body: string,
  demo: boolean,
): string {
  const script = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "dist", "panel.js"));
  const nonceValue = randomBytes(24).toString("hex");
  const demoScript = demo
    ? `<script nonce="${nonceValue}">window.__AITEST_DEMO__=true;</script>`
    : "";
  return `<!doctype html><html lang="zh-CN"><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonceValue}'; style-src 'unsafe-inline';">
    <title>AI 测试</title></head><body style="margin:0;padding:0;background:#191b20">
    ${body}${demoScript}<script nonce="${nonceValue}" src="${script}"></script>
    </body></html>`;
}

function openReferencePreview(context: vscode.ExtensionContext): void {
  const panel = vscode.window.createWebviewPanel(
    "aitest.reference",
    "AI 测试 · 参考图预览",
    vscode.ViewColumn.Active,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "dist")],
    },
  );
  panel.webview.html = panelHtml(context, panel.webview, "<aitest-mockup></aitest-mockup>", true);
  context.subscriptions.push(panel);
}

function openDetailPanel(context: vscode.ExtensionContext, detail: string): void {
  if (!DETAIL_VIEWS.has(detail)) return;
  const existing = detailPanels.get(detail);
  if (existing) { existing.reveal(); return; }
  const titles: Readonly<Record<string, string>> = {
    "ai-records": "AI 生成记录", driver: "驱动与运行修订", environment: "环境与来源",
    issue: "问题详情", records: "报告查询", regression: "变更与回归", result: "报告 · 修订3",
    running: "测试 · 工单状态修改", plan: "测试计划",
  };
  const panel = vscode.window.createWebviewPanel(
    `aitest.detail.${detail}`,
    titles[detail] ?? "AI 测试详情",
    vscode.ViewColumn.Beside,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "dist")],
    },
  );
  const demo = readSettings().demoMode;
  panel.webview.html = panelHtml(
    context,
    panel.webview,
    `<ai-test-detail view="${detail}"></ai-test-detail>`,
    demo,
  );
  panel.onDidDispose(() => detailPanels.delete(detail));
  detailPanels.set(detail, panel);
  context.subscriptions.push(panel);
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function toDispatchRequest(raw: Record<string, unknown>): DispatchRequest | null {
  const action = textOrNull(raw["action"]);
  if (action === null) return null;
  const parameters = raw["parameters"];
  if (parameters !== undefined && (typeof parameters !== "object" || parameters === null || Array.isArray(parameters))) {
    return null;
  }
  return {
    action,
    parameters: (parameters as Record<string, unknown> | undefined) ?? {},
    projectId: textOrNull(raw["projectId"]),
  };
}

function post(target: PanelTarget, payload: Record<string, unknown>): void {
  void target.webview.postMessage(payload);
}

function postHostInfo(target: PanelTarget): HostSettings {
  const settings = readSettings();
  post(target, {
    kind: "hostInfo",
    projectId: settings.projectId,
    workspaceRoot: settings.workspaceRoot,
    demoAvailable: settings.demoMode,
  });
  return settings;
}

async function handleWebviewMessage(
  target: PanelTarget,
  message: unknown,
  context: vscode.ExtensionContext,
): Promise<void> {
  if (typeof message !== "object" || message === null) return;
  const raw = message as Record<string, unknown>;
  if (raw["kind"] === "openDetail") {
    const detail = textOrNull(raw["detail"]);
    if (detail !== null) openDetailPanel(context, detail);
    return;
  }
  const settings = postHostInfo(target);
  if (raw["kind"] === "ensureCore") {
    const ensured = await ensureCore(settings.python, settings.workspaceRoot);
    post(target, {
      kind: "outcome", action: "ensure-core", status: ensured.status,
      code: ensured.code, message: ensured.message, response: ensured.response,
    });
    return;
  }
  if (raw["kind"] !== "dispatch") return;
  const request = toDispatchRequest(raw);
  if (request === null) {
    post(target, {
      kind: "outcome", action: null, status: "refused", code: "INVALID_REQUEST",
      message: "派发请求缺少动作名，或参数不是 JSON 对象",
    });
    return;
  }
  let outcome: DispatchOutcome;
  try {
    outcome = await dispatchReadOnly(
      settings.python, settings.workspaceRoot, request, randomUUID(), DISPATCH_TIMEOUT_MS,
    );
  } catch (error) {
    outcome = {
      status: "refused", code: "ENTRY_ACTION_NOT_ALLOWED",
      message: error instanceof Error ? error.message : "该动作不能由面板发起",
    };
  }
  post(target, { kind: "outcome", action: request.action, ...outcome });
}

export function deactivate(): void {
  // Closing an editor entry never cancels a business run already managed by core.
}