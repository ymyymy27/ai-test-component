/** 标准可选宿主能力插头（HostCapabilities）。
 *
 * 将编辑器/宿主原生能力（Git 扩展、文件监听、文档符号、源码跳转、诊断波浪线、
 * SecretStorage、状态栏与输出面板）封装为“运行时特征探测 + 可选降级”的独立层：
 * - 宿主提供对应能力时直接复用，减少重复轮询和手写脚本；
 * - 宿主缺少某个 API（如精简编辑器、老版本宿主、CLI 无界面或无扩展测试）时平滑降级，
 *   不抛异常且核心测试工作流保持完整可用。
 */
import { existsSync, statSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

export interface CapabilityFlags {
  readonly git: boolean;
  readonly fileWatcher: boolean;
  readonly documentSymbols: boolean;
  readonly openSource: boolean;
  readonly diagnostics: boolean;
  readonly secretStorage: boolean;
  readonly statusBar: boolean;
  readonly outputChannel: boolean;
}

export interface HostGitSummary {
  readonly available: boolean;
  readonly branch: string;
  readonly head: string;
  readonly ahead: number;
  readonly behind: number;
  readonly dirtyCount: number;
}

export interface HostDocumentSymbol {
  readonly name: string;
  readonly detail: string;
  readonly kind: number;
  readonly startLine: number;
  readonly endLine: number;
}

export interface DiagnosticInput {
  readonly file: string;
  readonly line?: number;
  readonly endLine?: number;
  readonly message: string;
  readonly severity?: "error" | "warning" | "info";
  readonly code?: string;
}

export type ResolvedSourceResult =
  | { readonly ok: true; readonly path: string; readonly relPath: string }
  | { readonly ok: false; readonly code: string; readonly message: string; readonly nextStep: string };

export interface HostDisposable {
  dispose(): void;
}

export interface HostUriLike {
  readonly fsPath: string;
}

export interface HostSecretStorageLike {
  get(key: string): Thenable<string | undefined> | Promise<string | undefined>;
  store(key: string, value: string): Thenable<void> | Promise<void>;
  delete(key: string): Thenable<void> | Promise<void>;
}

export interface HostExtensionContextLike {
  readonly secrets?: HostSecretStorageLike;
}

interface HostGitRepoLike {
  readonly rootUri?: { readonly fsPath?: string };
  readonly state?: {
    readonly HEAD?: {
      readonly name?: string;
      readonly commit?: string;
      readonly ahead?: number;
      readonly behind?: number;
    };
    readonly workingTreeChanges?: readonly unknown[];
    readonly indexChanges?: readonly unknown[];
    readonly untrackedChanges?: readonly unknown[];
  };
}

interface HostGitApiLike {
  readonly repositories?: readonly HostGitRepoLike[];
}

interface HostDiagnosticCollectionLike extends HostDisposable {
  set(uri: unknown, diagnostics: readonly unknown[]): void;
  clear(): void;
}

interface HostStatusBarItemLike extends HostDisposable {
  text: string;
  tooltip?: string;
  command?: string;
  show(): void;
  hide(): void;
}

interface HostOutputChannelLike extends HostDisposable {
  appendLine(value: string): void;
}

export interface HostApiLike {
  readonly Uri: {
    file(path: string): HostUriLike;
  };
  readonly workspace?: {
    readonly openTextDocument?: (uri: unknown) => Thenable<unknown> | Promise<unknown>;
    readonly createFileSystemWatcher?: (globPattern: unknown) => {
      onDidChange(listener: (uri: HostUriLike) => void): HostDisposable;
      onDidCreate(listener: (uri: HostUriLike) => void): HostDisposable;
      onDidDelete(listener: (uri: HostUriLike) => void): HostDisposable;
      dispose(): void;
    };
  };
  readonly window?: {
    readonly showTextDocument?: (
      document: unknown,
      options?: { selection?: unknown; preserveFocus?: boolean },
    ) => Thenable<unknown> | Promise<unknown>;
    readonly createStatusBarItem?: (alignment?: number, priority?: number) => HostStatusBarItemLike;
    readonly createOutputChannel?: (name: string) => HostOutputChannelLike;
  };
  readonly commands?: {
    readonly executeCommand?: (command: string, ...rest: unknown[]) => Thenable<unknown> | Promise<unknown>;
  };
  readonly languages?: {
    readonly createDiagnosticCollection?: (name: string) => HostDiagnosticCollectionLike;
  };
  readonly extensions?: {
    readonly getExtension?: (extensionId: string) => {
      readonly isActive?: boolean;
      readonly exports?: {
        getAPI?(version: number): HostGitApiLike | undefined;
      };
      activate?(): Thenable<unknown> | Promise<unknown>;
    } | undefined;
  };
  readonly RelativePattern?: new (base: string, pattern: string) => unknown;
  readonly Range?: new (startLine: number, startCharacter: number, endLine: number, endCharacter: number) => unknown;
  readonly Diagnostic?: new (range: unknown, message: string, severity?: number) => {
    code?: string;
    source?: string;
  };
  readonly DiagnosticSeverity?: {
    readonly Error?: number;
    readonly Warning?: number;
    readonly Information?: number;
  };
  readonly StatusBarAlignment?: {
    readonly Left?: number;
    readonly Right?: number;
  };
}

const IGNORED_WATCH_SEGMENTS = new Set([
  ".git",
  "node_modules",
  "__pycache__",
  ".venv",
  "venv",
  ".runtime",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  "dist",
  "build",
]);

function normalizeComparePath(raw: string): string {
  const resolved = resolve(raw).replace(/[\\/]+$/, "");
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function isInsideRoot(root: string, target: string): { inside: boolean; relPath: string } {
  const normRoot = normalizeComparePath(root);
  const normTarget = normalizeComparePath(target);
  if (!normRoot || !normTarget) {
    return { inside: false, relPath: "" };
  }
  const relCompare = relative(normRoot, normTarget);
  if (!relCompare || relCompare === ".." || relCompare.startsWith("..\\") || relCompare.startsWith("../") || isAbsolute(relCompare)) {
    return { inside: normRoot === normTarget, relPath: "" };
  }
  const actualRel = relative(resolve(root), resolve(target)).replaceAll("\\", "/");
  return { inside: true, relPath: actualRel };
}

/** 将相对或绝对路径解析为已绑定项目根内的安全文件路径，越界一律拦截。 */
export function resolveSourceFile(
  projectRoot: string,
  rawPath: string,
  verifyExists = true,
): ResolvedSourceResult {
  const cleanRoot = projectRoot.trim();
  const cleanTarget = rawPath.trim();
  if (!cleanRoot) {
    return {
      ok: false,
      code: "PROJECT_UNBOUND",
      message: "当前未绑定项目根目录，无法定位源码",
      nextStep: "先在项目工作区绑定被测项目目录",
    };
  }
  if (!cleanTarget) {
    return {
      ok: false,
      code: "INVALID_REQUEST",
      message: "源码路径不能为空",
      nextStep: "提供相对项目根目录的源码文件路径",
    };
  }
  const candidate = isAbsolute(cleanTarget) ? resolve(cleanTarget) : resolve(cleanRoot, cleanTarget);
  const check = isInsideRoot(cleanRoot, candidate);
  if (!check.inside) {
    return {
      ok: false,
      code: "PERMISSION_DENIED",
      message: "禁止跳转到项目根目录之外的文件",
      nextStep: "仅打开当前已绑定项目内的源码文件",
    };
  }
  if (verifyExists) {
    try {
      if (!existsSync(candidate) || !statSync(candidate).isFile()) {
        return {
          ok: false,
          code: "NOT_FOUND",
          message: `源码文件不存在：${check.relPath || cleanTarget}`,
          nextStep: "刷新代码图后重试",
        };
      }
    } catch {
      return {
        ok: false,
        code: "NOT_FOUND",
        message: `无法访问源码文件：${check.relPath || cleanTarget}`,
        nextStep: "检查文件权限或刷新代码图",
      };
    }
  }
  return { ok: true, path: candidate, relPath: check.relPath };
}

function sanitizeGitText(value: unknown): string {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) {
    return "";
  }
  return raw.replace(/https?:\/\/[^@\s]+@/gi, "https://[REDACTED]@");
}

function shouldIgnoreChangedRel(relPosix: string): boolean {
  if (!relPosix) {
    return true;
  }
  return relPosix.split("/").some((segment) => IGNORED_WATCH_SEGMENTS.has(segment));
}

function toZeroBasedRange(host: HostApiLike, startLine1: number, startCol1: number, endLine1: number, endCol1: number): unknown {
  const sLine = Math.max(0, Math.floor(startLine1 - 1));
  const sCol = Math.max(0, Math.floor(startCol1 - 1));
  const eLine = Math.max(sLine, Math.floor(endLine1 - 1));
  const eCol = Math.max(sCol, Math.floor(endCol1 - 1));
  if (typeof host.Range === "function") {
    return new host.Range(sLine, sCol, eLine, eCol);
  }
  return {
    start: { line: sLine, character: sCol },
    end: { line: eLine, character: eCol },
  };
}

function extractLineNumber(rangeLike: unknown, key: "start" | "end", fallback: number): number {
  if (!rangeLike || typeof rangeLike !== "object") {
    return fallback;
  }
  const bound = (rangeLike as Record<string, unknown>)[key];
  if (bound && typeof bound === "object") {
    const line = (bound as Record<string, unknown>)["line"];
    if (typeof line === "number" && Number.isFinite(line)) {
      return Math.max(1, Math.floor(line) + 1);
    }
  }
  return fallback;
}

function flattenSymbols(raw: unknown, out: HostDocumentSymbol[]): void {
  if (!Array.isArray(raw)) {
    return;
  }
  for (const item of raw) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const rec = item as Record<string, unknown>;
    const name = typeof rec["name"] === "string" ? rec["name"] : "";
    const rangeSource = rec["range"] ?? (rec["location"] as Record<string, unknown> | undefined)?.["range"];
    const startLine = extractLineNumber(rangeSource, "start", 1);
    const endLine = extractLineNumber(rangeSource, "end", startLine);
    if (name) {
      out.push({
        name,
        detail: typeof rec["detail"] === "string" ? rec["detail"] : "",
        kind: typeof rec["kind"] === "number" ? rec["kind"] : 0,
        startLine,
        endLine,
      });
    }
    if (Array.isArray(rec["children"])) {
      flattenSymbols(rec["children"], out);
    }
  }
}

export interface HostCapabilitiesController extends HostDisposable {
  detectCapabilities(): CapabilityFlags;
  readGitSummary(projectRoot: string): Promise<HostGitSummary | null>;
  readDocumentSymbols(projectRoot: string, filePath: string): Promise<HostDocumentSymbol[]>;
  jumpToSource(
    projectRoot: string,
    filePath: string,
    line?: number,
    column?: number,
  ): Promise<
    | { readonly ok: true; readonly path: string; readonly line: number; readonly column: number }
    | { readonly ok: false; readonly code: string; readonly message: string; readonly nextStep: string }
  >;
  watchWorkspace(
    projectRoot: string,
    onDirty: (changedPaths: string[]) => void,
    debounceMs?: number,
  ): HostDisposable;
  publishDiagnostics(
    projectRoot: string,
    items: readonly DiagnosticInput[],
  ): { readonly count: number; readonly unsupported?: boolean };
  clearDiagnostics(): void;
  getSecret(key: string): Promise<string | undefined>;
  storeSecret(key: string, value: string): Promise<boolean>;
  deleteSecret(key: string): Promise<boolean>;
  updateStatusBar(text: string, tooltip?: string, command?: string): boolean;
  appendOutput(line: string): boolean;
}

/** 创建基于当前宿主环境的能力管理器；全部可选 API 均按特征探测并在缺失时降级。 */
export function createHostCapabilities(
  host: HostApiLike,
  context?: HostExtensionContextLike,
): HostCapabilitiesController {
  let activeWatcher: HostDisposable | null = null;
  let diagnosticCollection: HostDiagnosticCollectionLike | null = null;
  let statusBarItem: HostStatusBarItemLike | null = null;
  let outputChannel: HostOutputChannelLike | null = null;

  function detectCapabilities(): CapabilityFlags {
    const secrets = context?.secrets;
    return {
      git: typeof host.extensions?.getExtension === "function" && Boolean(host.extensions.getExtension("vscode.git")),
      fileWatcher: typeof host.workspace?.createFileSystemWatcher === "function",
      documentSymbols:
        typeof host.workspace?.openTextDocument === "function" &&
        typeof host.commands?.executeCommand === "function",
      openSource:
        typeof host.workspace?.openTextDocument === "function" &&
        typeof host.window?.showTextDocument === "function",
      diagnostics: typeof host.languages?.createDiagnosticCollection === "function",
      secretStorage: Boolean(
        secrets &&
          typeof secrets.get === "function" &&
          typeof secrets.store === "function" &&
          typeof secrets.delete === "function",
      ),
      statusBar: typeof host.window?.createStatusBarItem === "function",
      outputChannel: typeof host.window?.createOutputChannel === "function",
    };
  }

  async function readGitSummary(projectRoot: string): Promise<HostGitSummary | null> {
    if (!projectRoot.trim() || typeof host.extensions?.getExtension !== "function") {
      return null;
    }
    try {
      const ext = host.extensions.getExtension("vscode.git");
      if (!ext) {
        return null;
      }
      if (!ext.isActive && typeof ext.activate === "function") {
        await ext.activate();
      }
      const api = ext.exports?.getAPI?.(1);
      const repos = api?.repositories;
      if (!Array.isArray(repos) || repos.length === 0) {
        return null;
      }
      const normProject = normalizeComparePath(projectRoot);
      const matched = repos.find((repo) => {
        const repoRoot = repo.rootUri?.fsPath;
        if (!repoRoot) {
          return false;
        }
        const normRepo = normalizeComparePath(repoRoot);
        return normProject === normRepo || isInsideRoot(repoRoot, projectRoot).inside;
      });
      if (!matched?.state) {
        return null;
      }
      const st = matched.state;
      const head = st.HEAD;
      const dirtyCount =
        (st.workingTreeChanges?.length ?? 0) +
        (st.indexChanges?.length ?? 0) +
        (st.untrackedChanges?.length ?? 0);
      return {
        available: true,
        branch: sanitizeGitText(head?.name),
        head: sanitizeGitText(head?.commit),
        ahead: typeof head?.ahead === "number" ? head.ahead : 0,
        behind: typeof head?.behind === "number" ? head.behind : 0,
        dirtyCount,
      };
    } catch {
      return null;
    }
  }

  async function readDocumentSymbols(
    projectRoot: string,
    filePath: string,
  ): Promise<HostDocumentSymbol[]> {
    const checked = resolveSourceFile(projectRoot, filePath, true);
    if (!checked.ok) {
      return [];
    }
    const openDoc = host.workspace?.openTextDocument;
    const execCmd = host.commands?.executeCommand;
    if (typeof openDoc !== "function" || typeof execCmd !== "function") {
      return [];
    }
    try {
      const uri = host.Uri.file(checked.path);
      const doc = (await openDoc(uri)) as { uri?: unknown } | undefined;
      const raw = await execCmd("vscode.executeDocumentSymbolProvider", doc?.uri ?? uri);
      const symbols: HostDocumentSymbol[] = [];
      flattenSymbols(raw, symbols);
      return symbols;
    } catch {
      return [];
    }
  }

  async function jumpToSource(
    projectRoot: string,
    filePath: string,
    line = 1,
    column = 1,
  ) {
    const checked = resolveSourceFile(projectRoot, filePath, true);
    if (!checked.ok) {
      return checked;
    }
    const openDoc = host.workspace?.openTextDocument;
    const showDoc = host.window?.showTextDocument;
    if (typeof openDoc !== "function" || typeof showDoc !== "function") {
      return {
        ok: false as const,
        code: "HOST_UNSUPPORTED",
        message: "当前宿主未提供编辑器文档跳转接口",
        nextStep: "在资源管理器或编辑器文件树手动定位文件",
      };
    }
    const targetLine = Math.max(1, Math.floor(line || 1));
    const targetCol = Math.max(1, Math.floor(column || 1));
    try {
      const doc = await openDoc(host.Uri.file(checked.path));
      const selection = toZeroBasedRange(host, targetLine, targetCol, targetLine, targetCol);
      await showDoc(doc, { selection, preserveFocus: false });
      return {
        ok: true as const,
        path: checked.path,
        line: targetLine,
        column: targetCol,
      };
    } catch {
      return {
        ok: false as const,
        code: "HOST_CALL_FAILED",
        message: `打开源码文件失败：${checked.relPath}`,
        nextStep: "确认文件未被占用后重试",
      };
    }
  }

  function watchWorkspace(
    projectRoot: string,
    onDirty: (changedPaths: string[]) => void,
    debounceMs = 450,
  ): HostDisposable {
    activeWatcher?.dispose();
    activeWatcher = null;
    const createWatcher = host.workspace?.createFileSystemWatcher;
    if (!projectRoot.trim() || typeof createWatcher !== "function") {
      return { dispose() {} };
    }
    const pattern =
      typeof host.RelativePattern === "function"
        ? new host.RelativePattern(projectRoot, "**/*")
        : "**/*";
    const watcher = createWatcher(pattern);
    const pending = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const flush = (): void => {
      timer = null;
      if (disposed || pending.size === 0) {
        return;
      }
      const batch = [...pending];
      pending.clear();
      onDirty(batch);
    };

    const recordUri = (uri: HostUriLike): void => {
      if (disposed || !uri?.fsPath) {
        return;
      }
      const inside = isInsideRoot(projectRoot, uri.fsPath);
      if (!inside.inside || shouldIgnoreChangedRel(inside.relPath)) {
        return;
      }
      pending.add(inside.relPath);
      if (timer !== null) {
        clearTimeout(timer);
      }
      timer = setTimeout(flush, Math.max(10, debounceMs));
    };

    const subChange = watcher.onDidChange(recordUri);
    const subCreate = watcher.onDidCreate(recordUri);
    const subDelete = watcher.onDidDelete(recordUri);

    const handle: HostDisposable = {
      dispose(): void {
        if (disposed) {
          return;
        }
        disposed = true;
        if (timer !== null) {
          clearTimeout(timer);
          timer = null;
        }
        pending.clear();
        subChange.dispose();
        subCreate.dispose();
        subDelete.dispose();
        watcher.dispose();
        if (activeWatcher === handle) {
          activeWatcher = null;
        }
      },
    };
    activeWatcher = handle;
    return handle;
  }

  function publishDiagnostics(
    projectRoot: string,
    items: readonly DiagnosticInput[],
  ): { readonly count: number; readonly unsupported?: boolean } {
    const createDiag = host.languages?.createDiagnosticCollection;
    if (typeof createDiag !== "function") {
      return { count: 0, unsupported: true };
    }
    if (diagnosticCollection === null) {
      diagnosticCollection = createDiag("aitest");
    }
    diagnosticCollection.clear();
    if (!projectRoot.trim() || items.length === 0) {
      return { count: 0 };
    }
    const severityMap = {
      error: host.DiagnosticSeverity?.Error ?? 0,
      warning: host.DiagnosticSeverity?.Warning ?? 1,
      info: host.DiagnosticSeverity?.Information ?? 2,
    };
    const grouped = new Map<string, unknown[]>();
    let total = 0;
    for (const item of items) {
      const checked = resolveSourceFile(projectRoot, item.file, false);
      if (!checked.ok || !item.message.trim()) {
        continue;
      }
      const line = Math.max(1, Math.floor(item.line ?? 1));
      const endLine = Math.max(line, Math.floor(item.endLine ?? line));
      const range = toZeroBasedRange(host, line, 1, endLine, 1000);
      const sev = severityMap[item.severity ?? "error"];
      let diagObj: Record<string, unknown>;
      if (typeof host.Diagnostic === "function") {
        const instance = new host.Diagnostic(range, item.message, sev);
        instance.source = "AI 测试";
        if (item.code) {
          instance.code = item.code;
        }
        diagObj = instance as unknown as Record<string, unknown>;
      } else {
        diagObj = {
          range,
          message: item.message,
          severity: sev,
          source: "AI 测试",
          ...(item.code ? { code: item.code } : {}),
        };
      }
      const bucket = grouped.get(checked.path) ?? [];
      bucket.push(diagObj);
      grouped.set(checked.path, bucket);
      total += 1;
    }
    for (const [filePath, list] of grouped) {
      diagnosticCollection.set(host.Uri.file(filePath), list);
    }
    return { count: total };
  }

  function clearDiagnostics(): void {
    diagnosticCollection?.clear();
  }

  async function getSecret(key: string): Promise<string | undefined> {
    if (!context?.secrets || typeof context.secrets.get !== "function" || !key.trim()) {
      return undefined;
    }
    try {
      return await context.secrets.get(key);
    } catch {
      return undefined;
    }
  }

  async function storeSecret(key: string, value: string): Promise<boolean> {
    if (!context?.secrets || typeof context.secrets.store !== "function" || !key.trim()) {
      return false;
    }
    try {
      await context.secrets.store(key, value);
      return true;
    } catch {
      return false;
    }
  }

  async function deleteSecret(key: string): Promise<boolean> {
    if (!context?.secrets || typeof context.secrets.delete !== "function" || !key.trim()) {
      return false;
    }
    try {
      await context.secrets.delete(key);
      return true;
    } catch {
      return false;
    }
  }

  function updateStatusBar(text: string, tooltip = "", command = "aitest.openPanel"): boolean {
    const createBar = host.window?.createStatusBarItem;
    if (typeof createBar !== "function") {
      return false;
    }
    if (statusBarItem === null) {
      statusBarItem = createBar(host.StatusBarAlignment?.Left ?? 1, 100);
    }
    statusBarItem.text = text;
    if (tooltip) {
      statusBarItem.tooltip = tooltip;
    }
    if (command) {
      statusBarItem.command = command;
    }
    statusBarItem.show();
    return true;
  }

  function appendOutput(line: string): boolean {
    const createOut = host.window?.createOutputChannel;
    if (typeof createOut !== "function") {
      return false;
    }
    if (outputChannel === null) {
      outputChannel = createOut("AI 测试 (aitest)");
    }
    outputChannel.appendLine(line);
    return true;
  }

  function dispose(): void {
    activeWatcher?.dispose();
    activeWatcher = null;
    diagnosticCollection?.clear();
    diagnosticCollection?.dispose();
    diagnosticCollection = null;
    statusBarItem?.dispose();
    statusBarItem = null;
    outputChannel?.dispose();
    outputChannel = null;
  }

  return {
    detectCapabilities,
    readGitSummary,
    readDocumentSymbols,
    jumpToSource,
    watchWorkspace,
    publishDiagnostics,
    clearDiagnostics,
    getSecret,
    storeSecret,
    deleteSecret,
    updateStatusBar,
    appendOutput,
    dispose,
  };
}
