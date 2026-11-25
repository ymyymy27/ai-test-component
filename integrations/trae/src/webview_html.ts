/** Webview 面板 HTML 装配、CSP 注入与前端桥脚本模板。 */
import { readFileSync } from "node:fs";
import * as vscode from "vscode";

import { PANEL_TITLE } from "./ext_log";

export interface InitialPanelContext {
  readonly pythonPath: string;
  readonly workspacePath: string;
  readonly projectId: string;
  readonly projectRoot: string;
  readonly workspaceFolderCount: number;
  readonly themeKind: "dark" | "light";
  readonly capabilities?: Record<string, boolean>;
  readonly hostGit?: Record<string, unknown> | null;
}

export function safeJsonForHtml(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026");
}

/** 注入到 webview 的桥：只转发调用与状态，不解释业务结果。 */
export const BRIDGE_SCRIPT = `(function () {
  var api = acquireVsCodeApi();
  var pending = new Map();
  var seq = 0;
  var context = window.__AITEST_INITIAL_CONTEXT__ || null;
  var themeKind = (context && (context.themeKind === "dark" || context.themeKind === "light"))
    ? context.themeKind
    : "light";
  var contextListeners = new Set();
  var themeListeners = new Set();
  var dirtyListeners = new Set();
  function notifyContext() {
    contextListeners.forEach(function (cb) {
      try { cb(context); } catch (ignored) {}
    });
  }
  function notifyTheme() {
    themeListeners.forEach(function (cb) {
      try { cb(themeKind); } catch (ignored) {}
    });
  }
  function notifyDirty(payload) {
    dirtyListeners.forEach(function (cb) {
      try { cb(payload); } catch (ignored) {}
    });
  }
  window.addEventListener("message", function (event) {
    var message = event.data;
    if (!message) return;
    if (message.type === "context") {
      context = message;
      if (message.themeKind === "dark" || message.themeKind === "light") {
        themeKind = message.themeKind;
        notifyTheme();
      }
      notifyContext();
      return;
    }
    if (message.type === "workspaceDirty") {
      notifyDirty({
        projectRoot: typeof message.projectRoot === "string" ? message.projectRoot : "",
        changedPaths: Array.isArray(message.changedPaths) ? message.changedPaths : []
      });
      return;
    }
    if (message.type === "theme") {
      if (message.theme === "dark" || message.theme === "light") {
        themeKind = message.theme;
        if (context && typeof context === "object") {
          context = Object.assign({}, context, { themeKind: themeKind });
        }
        notifyTheme();
      }
      return;
    }
    if (message.type === "progress") {
      var active = pending.get(message.id);
      if (!active) return;
      if (typeof active.onProgress === "function") {
        try { active.onProgress(message); } catch (ignored) {}
      }
      return;
    }
    if (message.type !== "result" || !message.id) return;
    var entry = pending.get(message.id);
    if (!entry) return;
    pending.delete(message.id);
    if (message.ok) {
      entry.resolve({ ok: true, data: message.data === undefined ? null : message.data });
    } else {
      entry.resolve({
        ok: false,
        error: message.error || { code: "INTERNAL", message: "未知错误" }
      });
    }
  });
  window.aitestHost = {
    call: function (command, payload, options) {
      var id = "host-" + (++seq);
      var opts = options || {};
      return new Promise(function (resolve) {
        pending.set(id, {
          resolve: resolve,
          onProgress: typeof opts.onProgress === "function" ? opts.onProgress : null
        });
        api.postMessage({
          type: "call",
          id: id,
          command: command,
          payload: payload || {},
          options: { confirm: opts.confirm === true }
        });
      });
    },
    cancel: function (command, payload, options) {
      var opts = options || {};
      api.postMessage({
        type: "cancel",
        command: command,
        payload: payload || {},
        options: { confirm: opts.confirm === true }
      });
    },
    openPath: function (path) {
      var id = "open-" + (++seq);
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({ type: "openPath", id: id, path: path });
      });
    },
    pickDirectory: function () {
      var id = "pick-" + (++seq);
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({ type: "pickDirectory", id: id });
      });
    },
    openSource: function (path, line, column) {
      var id = "src-" + (++seq);
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({
          type: "openSource",
          id: id,
          path: path,
          line: typeof line === "number" ? line : 1,
          column: typeof column === "number" ? column : 1
        });
      });
    },
    publishDiagnostics: function (items) {
      var id = "diag-" + (++seq);
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({
          type: "publishDiagnostics",
          id: id,
          items: Array.isArray(items) ? items : []
        });
      });
    },
    clearDiagnostics: function () {
      var id = "diag-" + (++seq);
      return new Promise(function (resolve) {
        pending.set(id, { resolve: resolve });
        api.postMessage({ type: "clearDiagnostics", id: id });
      });
    },
    onWorkspaceDirty: function (cb) {
      if (typeof cb !== "function") return function () {};
      dirtyListeners.add(cb);
      return function () { dirtyListeners.delete(cb); };
    },
    getState: function () { return api.getState(); },
    setState: function (state) { api.setState(state); },
    getContext: function () { return context; },
    getTheme: function () { return themeKind; },
    onContext: function (cb) {
      if (typeof cb !== "function") return function () {};
      contextListeners.add(cb);
      if (context) {
        try { cb(context); } catch (ignored) {}
      }
      return function () { contextListeners.delete(cb); };
    },
    onTheme: function (cb) {
      if (typeof cb !== "function") return function () {};
      themeListeners.add(cb);
      try { cb(themeKind); } catch (ignored) {}
      return function () { themeListeners.delete(cb); };
    }
  };
  function reportPanelError(kind, detail) {
    try {
      var text = detail && detail.message ? detail.message : detail;
      api.postMessage({ type: "panel-error", kind: kind, message: String(text || "") });
    } catch (ignored) {}
  }
  window.addEventListener("error", function (event) {
    reportPanelError("error", event.error || event.message);
  });
  window.addEventListener("unhandledrejection", function (event) {
    reportPanelError("rejection", event.reason);
  });
  api.postMessage({ type: "ready" });
})();`;

/** 加载共享面板构建产物，并把脚本与样式内联进宿主 HTML。 */
export function renderPanelHtml(
  extensionUri: vscode.Uri,
  nonce: string,
  initialContext: InitialPanelContext,
): string {
  try {
    const panelRoot = vscode.Uri.joinPath(extensionUri, "media", "panel");
    const template = readFileSync(vscode.Uri.joinPath(panelRoot, "index.html").fsPath, "utf8");
    const theme = initialContext.themeKind;
    // 扩展构建产出单文件 panel.js / panel.css。Trae 的 webview 资源 URI 与
    // cspSource 可能不同源，内联可避免 CSS/脚本被 CSP 或 URI 改写拦掉。
    const script = readFileSync(vscode.Uri.joinPath(panelRoot, "panel.js").fsPath, "utf8")
      .replace(/<\/script/gi, "<\\/script")
      .split("<!--")
      .join("<\\u0021--");
    const style = readFileSync(vscode.Uri.joinPath(panelRoot, "panel.css").fsPath, "utf8");
    const csp = [
      "default-src 'none'",
      `script-src 'nonce-${nonce}'`,
      "style-src 'unsafe-inline'",
      "img-src data:",
      "font-src data:",
      "connect-src 'none'",
    ].join("; ");
    const html = template
      .replace(/<html\b([^>]*)>/i, (_match, attrs: string) =>
        /\bdata-theme=/.test(attrs)
          ? `<html${attrs.replace(/\bdata-theme="[^"]*"/i, `data-theme="${theme}"`)}>`
          : `<html${attrs} data-theme="${theme}">`,
      )
      .replace(/<script\b[^>]*\bsrc="[^"]*"[^>]*>\s*<\/script>/gi, "")
      .replace(/<link\b[^>]*\bhref="[^"]*\.css"[^>]*>/gi, "")
      .replace(
        /<head>/i,
        () => `<head>
    <meta http-equiv="Content-Security-Policy" content="${csp}">
    <style nonce="${nonce}">${style}</style>
    <script nonce="${nonce}">window.__AITEST_INITIAL_CONTEXT__=${safeJsonForHtml(initialContext)};${BRIDGE_SCRIPT}</script>`,
      )
      .replace(
        "</body>",
        () => `<script nonce="${nonce}">${script}</script>\n  </body>`,
      );
    return html;
  } catch {
    return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none';">
      <title>${PANEL_TITLE}</title></head><body>
      <p>面板资源缺失：media/panel/panel.js 未随扩展安装。请先运行 npm run build。</p></body></html>`;
  }
}

export function trustWarningHtml(): string {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none';">
    <title>${PANEL_TITLE}</title></head><body>
    <p>请先通过编辑器确认工作区信任，再打开 AI 测试面板。</p></body></html>`;
}
