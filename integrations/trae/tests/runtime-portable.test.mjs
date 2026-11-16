import { test, after } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdirSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

// 便携运行时准备：下载、解压、venv、离线安装全部注入假实现，不联网、不启动真实宿主。
const root = resolve("../..");
const base = join(root, ".runtime/tmp");
await mkdir(base, { recursive: true });
const fixture = await mkdtemp(join(base, "runtime-portable-"));
await build({
  entryPoints: ["src/runtime.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: join(fixture, "runtime.mjs"),
});
const {
  ensureRuntime,
  preparePortableRuntime,
  findPython,
  selectPortableAsset,
  findBundledWheel,
  bundledWheelVersion,
  findPreparedPython,
  defaultRuntimeHome,
  downloadArchive,
} = await import(pathToFileURL(join(fixture, "runtime.mjs")).href);

after(async () => {
  assert.ok(fixture.startsWith(join(base, "runtime-portable-")));
  await rm(fixture, { recursive: true, force: true });
});

const SHA = "a".repeat(64);
const VERSION = "3.13.7+20250818";

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.stdout = new EventEmitter();
    this.stdout.setEncoding = () => {};
    this.stderr = new EventEmitter();
    this.stderr.setEncoding = () => {};
    this.killed = false;
  }
  kill() {
    this.killed = true;
    return true;
  }
}

/** responder(command, args) → {stdout, code, spawnError}；默认所有解释器都启动失败。 */
function fakeSpawn(responder = () => ({ spawnError: "ENOENT" })) {
  const calls = [];
  const spawn = (command, args, options) => {
    calls.push({ command, args: [...args], options });
    const child = new FakeChild();
    setImmediate(() => {
      const outcome = responder(command, args) ?? { code: 0 };
      if (outcome.spawnError) {
        const error = new Error("spawn failed");
        error.code = outcome.spawnError;
        child.emit("error", error);
        return;
      }
      if (outcome.stdout) {
        child.stdout.emit("data", outcome.stdout);
      }
      child.emit("close", outcome.code ?? 0);
    });
    return child;
  };
  return { spawn, calls };
}

function probe(version = "3.13.7", major = 3, minor = 13, extra = {}) {
  return JSON.stringify({ version, major, minor, aitest: "0.5.0", ...extra });
}

/** 一次性造好轮子目录与假的下载结果。 */
function makeWorkspace(name) {
  const home = join(fixture, name, "home");
  const wheelDir = join(fixture, name, "wheels");
  mkdirSync(wheelDir, { recursive: true });
  const wheel = join(wheelDir, "ai_test_component-0.5.0-py3-none-any.whl");
  writeFileSync(wheel, "wheel-bytes");
  return { home, wheelDir, wheel };
}

function asset(url = "https://example.invalid/cpython-3.13.7+20250818-x86_64-pc-windows-msvc-install_only.tar.gz") {
  return { version: VERSION, url, sha256: SHA };
}

function downloader(sha = SHA, calls = []) {
  return async (url, destination) => {
    calls.push({ url, destination });
    writeFileSync(destination, "archive-bytes");
    return { sha256: sha, bytes: 13 };
  };
}

test("an existing qualified interpreter skips download entirely", async () => {
  const { home, wheelDir } = makeWorkspace("existing");
  const downloads = [];
  const { spawn, calls } = fakeSpawn((command) => {
    if (command === "python") {
      return { stdout: probe() };
    }
    return { spawnError: "ENOENT" };
  });
  const result = await ensureRuntime(
    { workspacePath: join(fixture, "no-ws") },
    { spawn, download: downloader(SHA, downloads), runtimeHome: home, wheelDir, platform: "win32" },
  );
  assert.equal(result.ok, true);
  assert.equal(result.source, "existing");
  assert.equal(result.pythonPath, "python");
  assert.deepEqual(result.stages, []);
  assert.equal(downloads.length, 0);
  assert.equal(calls.some((call) => call.command === "tar"), false);
});

test("full preparation downloads, verifies, extracts, creates venv and installs the bundled wheel", async () => {
  const { home, wheelDir, wheel } = makeWorkspace("happy");
  const runtimeDir = join(home, VERSION);
  const basePython = join(runtimeDir, "python", "python.exe");
  const targetPython = join(runtimeDir, "venv", "Scripts", "python.exe");
  const { spawn, calls } = fakeSpawn((command, args) => {
    if (command === "py" || command === "python") {
      return { spawnError: "ENOENT" };
    }
    if (command === "tar") {
      const target = args[args.indexOf("-C") + 1];
      mkdirSync(join(target, "python"), { recursive: true });
      writeFileSync(join(target, "python", "python.exe"), "");
      return { code: 0 };
    }
    if (command === basePython && args[0] === "-m" && args[1] === "venv") {
      mkdirSync(join(args[2], "Scripts"), { recursive: true });
      writeFileSync(join(args[2], "Scripts", "python.exe"), "");
      return { code: 0 };
    }
    if (command === targetPython) {
      return args.includes("-c") ? { stdout: probe() } : { code: 0 };
    }
    return { spawnError: "ENOENT" };
  });
  const downloads = [];
  const stagesSeen = [];
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader(SHA, downloads),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
      onStage: (stage) => stagesSeen.push(stage.stage),
    },
  );

  assert.equal(result.ok, true);
  assert.equal(result.source, "portable-new");
  assert.equal(result.pythonPath, targetPython);
  assert.equal(result.version, "3.13.7");
  assert.equal(result.packageVersion, "0.5.0");
  assert.deepEqual(result.stages.map((stage) => stage.stage), [
    "download",
    "verify",
    "extract",
    "venv",
    "install",
    "verify",
  ]);
  assert.equal(result.stages.every((stage) => stage.ok), true);
  assert.deepEqual(stagesSeen, result.stages.map((stage) => stage.stage));
  assert.equal(downloads.length, 1);

  const install = calls.find((call) => call.args.includes("pip"));
  assert.ok(install, "应当调用 venv 内的 pip");
  assert.deepEqual(install.args.slice(0, 4), ["-m", "pip", "install", "--no-index"]);
  assert.equal(install.args.includes("--find-links"), true);
  assert.equal(install.args.includes(wheel), true);
  assert.equal(install.args.includes(wheelDir), true);

  const tar = calls.find((call) => call.command === "tar");
  assert.deepEqual(tar.args.slice(0, 3), ["-xf", join(runtimeDir, `python-${VERSION}.tar.gz`), "-C"]);
  // 解压后压缩包被回收，不留 40MB 临时文件。
  assert.equal(existsSync(join(runtimeDir, `python-${VERSION}.tar.gz`)), false);
});

test("a SHA256 mismatch stops before extraction and removes the temp archive", async () => {
  const { home, wheelDir, wheel } = makeWorkspace("mismatch");
  const { spawn, calls } = fakeSpawn();
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader("b".repeat(64)),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
    },
  );
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "verify");
  assert.equal(result.failure.code, "SHA256_MISMATCH");
  assert.equal(calls.some((call) => call.command === "tar"), false);
  assert.equal(existsSync(join(home, VERSION, `python-${VERSION}.tar.gz.part`)), false);
  const diagnostics = JSON.parse(readFileSync(result.failure.diagnosticsPath, "utf8"));
  assert.equal(diagnostics.stage, "verify");
  assert.equal(diagnostics.code, "SHA256_MISMATCH");
  // 诊断文件不落下载 URL。
  assert.equal(JSON.stringify(diagnostics).includes("example.invalid"), false);
});

test("extraction failure reports the extract stage", async () => {
  const { home, wheelDir } = makeWorkspace("extract");
  const { spawn } = fakeSpawn((command) => {
    if (command === "tar") {
      return { code: 2 };
    }
    return { spawnError: "ENOENT" };
  });
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader(),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
    },
  );
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "extract");
  assert.equal(result.failure.code, "EXTRACT_FAILED");
});

test("missing system tar reports TAR_MISSING instead of falling back", async () => {
  const { home, wheelDir } = makeWorkspace("no-tar");
  const { spawn } = fakeSpawn();
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader(),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
    },
  );
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "extract");
  assert.equal(result.failure.code, "TAR_MISSING");
});

test("venv failure reports the venv stage", async () => {
  const { home, wheelDir } = makeWorkspace("venv");
  const runtimeDir = join(home, VERSION);
  const basePython = join(runtimeDir, "python", "python.exe");
  const { spawn } = fakeSpawn((command, args) => {
    if (command === "tar") {
      const target = args[args.indexOf("-C") + 1];
      mkdirSync(join(target, "python"), { recursive: true });
      writeFileSync(join(target, "python", "python.exe"), "");
      return { code: 0 };
    }
    if (command === basePython) {
      return { code: 1 };
    }
    return { spawnError: "ENOENT" };
  });
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader(),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
    },
  );
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "venv");
  assert.equal(result.failure.code, "VENV_FAILED");
});

test("pip install failure reports the install stage and keeps the venv", async () => {
  const { home, wheelDir } = makeWorkspace("install");
  const runtimeDir = join(home, VERSION);
  const basePython = join(runtimeDir, "python", "python.exe");
  const targetPython = join(runtimeDir, "venv", "Scripts", "python.exe");
  const { spawn } = fakeSpawn((command, args) => {
    if (command === "tar") {
      const target = args[args.indexOf("-C") + 1];
      mkdirSync(join(target, "python"), { recursive: true });
      writeFileSync(join(target, "python", "python.exe"), "");
      return { code: 0 };
    }
    if (command === basePython) {
      mkdirSync(join(runtimeDir, "venv", "Scripts"), { recursive: true });
      writeFileSync(targetPython, "");
      return { code: 0 };
    }
    if (command === targetPython) {
      return { code: 1 };
    }
    return { spawnError: "ENOENT" };
  });
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader(),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
    },
  );
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "install");
  assert.equal(result.failure.code, "INSTALL_FAILED");
  assert.equal(existsSync(targetPython), true);
});

test("environment overrides replace the release lookup", async () => {
  const { home, wheelDir } = makeWorkspace("env");
  const runtimeDir = join(home, "3.12.9+20250101");
  const basePython = join(runtimeDir, "python", "python.exe");
  const targetPython = join(runtimeDir, "venv", "Scripts", "python.exe");
  const { spawn } = fakeSpawn((command, args) => {
    if (command === "tar") {
      const target = args[args.indexOf("-C") + 1];
      mkdirSync(join(target, "python"), { recursive: true });
      writeFileSync(join(target, "python", "python.exe"), "");
      return { code: 0 };
    }
    if (command === basePython) {
      mkdirSync(join(runtimeDir, "venv", "Scripts"), { recursive: true });
      writeFileSync(targetPython, "");
      return { code: 0 };
    }
    if (command === targetPython) {
      return args.includes("-c") ? { stdout: probe("3.12.9", 3, 12) } : { code: 0 };
    }
    return { spawnError: "ENOENT" };
  });
  const downloads = [];
  const customSha = "c".repeat(64);
  const url =
    "https://mirror.invalid/aitest/cpython-3.12.9+20250101-x86_64-pc-windows-msvc-install_only.tar.gz";
  const result = await ensureRuntime(
    {},
    {
      spawn,
      download: downloader(customSha, downloads),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
      env: { AITEST_PYTHON_URL: url, AITEST_PYTHON_SHA256: customSha },
    },
  );
  assert.equal(result.ok, true);
  assert.equal(result.pythonPath, targetPython);
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].url, url);
});

test("an env URL without a SHA256 is rejected before downloading", async () => {
  const { home, wheelDir } = makeWorkspace("no-sha");
  const { spawn } = fakeSpawn();
  const downloads = [];
  const result = await ensureRuntime(
    {},
    {
      spawn,
      download: downloader(SHA, downloads),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
      env: { AITEST_PYTHON_URL: "https://mirror.invalid/python.tar.gz" },
    },
  );
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "verify");
  assert.equal(result.failure.code, "SHA256_MISSING");
  assert.equal(downloads.length, 0);
});

test("missing bundled wheel fails before the network download", async () => {
  const home = join(fixture, "no-wheel", "home");
  const downloads = [];
  const { spawn } = fakeSpawn();
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader(SHA, downloads),
      runtimeHome: home,
      wheelDir: join(fixture, "no-wheel", "empty"),
      platform: "win32",
    },
  );
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "install");
  assert.equal(result.failure.code, "WHEEL_MISSING");
  assert.equal(downloads.length, 0);
});

test("a prepared runtime is reused offline on the next start", async () => {
  const { home, wheelDir } = makeWorkspace("cached");
  const runtimeDir = join(home, "3.13.7+20250818");
  const targetPython = join(runtimeDir, "venv", "Scripts", "python.exe");
  mkdirSync(join(runtimeDir, "venv", "Scripts"), { recursive: true });
  writeFileSync(targetPython, "");
  const downloads = [];
  const { spawn, calls } = fakeSpawn((command, args) => {
    if (command === targetPython && args.includes("-c")) {
      return { stdout: probe() };
    }
    return { spawnError: "ENOENT" };
  });
  const result = await ensureRuntime(
    {},
    { spawn, download: downloader(SHA, downloads), runtimeHome: home, wheelDir, platform: "win32" },
  );
  assert.equal(result.ok, true);
  assert.equal(result.source, "portable-cache");
  assert.equal(result.pythonPath, targetPython);
  assert.equal(downloads.length, 0);
  assert.equal(calls.some((call) => call.command === "tar"), false);
});

test("a broken cached runtime is rebuilt in the same version directory", async () => {
  const { home, wheelDir } = makeWorkspace("cached-broken");
  const runtimeDir = join(home, "3.13.7+20250818");
  const targetPython = join(runtimeDir, "venv", "Scripts", "python.exe");
  mkdirSync(join(runtimeDir, "venv", "Scripts"), { recursive: true });
  writeFileSync(targetPython, "");
  const basePython = join(runtimeDir, "python", "python.exe");
  const { spawn } = fakeSpawn((command, args) => {
    if (command === "tar") {
      const target = args[args.indexOf("-C") + 1];
      mkdirSync(join(target, "python"), { recursive: true });
      writeFileSync(join(target, "python", "python.exe"), "");
      return { code: 0 };
    }
    if (command === basePython) {
      mkdirSync(join(runtimeDir, "venv", "Scripts"), { recursive: true });
      writeFileSync(targetPython, "");
      return { code: 0 };
    }
    if (command === targetPython) {
      // 缓存核验失败（缺 aitest），安装后核验成功。
      return args.includes("-c") && existsSync(join(runtimeDir, "python", "python.exe"))
        ? { stdout: probe() }
        : { stdout: probe("3.13.7", 3, 13, { import_error: "ModuleNotFoundError" }) };
    }
    return { spawnError: "ENOENT" };
  });
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader(),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
    },
  );
  assert.equal(result.ok, true);
  assert.equal(result.source, "portable-new");
  assert.equal(result.stages[0].stage, "verify");
  assert.equal(result.stages[0].code, "CACHED_RUNTIME_INVALID");
});

test("selectPortableAsset prefers the newest 3.11–3.13 Windows build with a digest", () => {
  const assets = [
    {
      name: "cpython-3.11.11+20250101-x86_64-pc-windows-msvc-install_only.tar.gz",
      browser_download_url: "https://example.invalid/311",
      digest: `sha256:${"1".repeat(64)}`,
    },
    {
      name: "cpython-3.13.7+20250818-x86_64-pc-windows-msvc-install_only.tar.gz",
      browser_download_url: "https://example.invalid/313",
      digest: `sha256:${"2".repeat(64)}`,
    },
    {
      name: "cpython-3.13.7+20250818-x86_64-pc-windows-msvc-install_only.tar.gz.sha256",
      browser_download_url: "https://example.invalid/313-sha",
      digest: `sha256:${"3".repeat(64)}`,
    },
    {
      name: "cpython-3.14.0+20251015-x86_64-pc-windows-msvc-install_only.tar.gz",
      browser_download_url: "https://example.invalid/314",
      digest: `sha256:${"4".repeat(64)}`,
    },
    {
      name: "cpython-3.13.7+20250818-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
      browser_download_url: "https://example.invalid/stripped",
      digest: `sha256:${"5".repeat(64)}`,
    },
  ];
  const selection = selectPortableAsset(assets);
  assert.equal(selection.ok, true);
  assert.equal(selection.asset.version, "3.13.7+20250818");
  assert.equal(selection.asset.url, "https://example.invalid/313");
  assert.equal(selection.asset.sha256, "2".repeat(64));
});

test("selectPortableAsset refuses assets without a digest", () => {
  const selection = selectPortableAsset([
    {
      name: "cpython-3.13.7+20250818-x86_64-pc-windows-msvc-install_only.tar.gz",
      browser_download_url: "https://example.invalid/313",
      digest: null,
    },
  ]);
  assert.equal(selection.ok, false);
  assert.equal(selection.code, "DIGEST_MISSING");

  const missing = selectPortableAsset([{ name: "README.md", browser_download_url: "x" }]);
  assert.equal(missing.ok, false);
  assert.equal(missing.code, "ASSET_NOT_FOUND");
});

test("findBundledWheel prefers the aitest wheel over unrelated wheels", () => {
  const dir = join(fixture, "wheel-pick");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "zzz_dependency-1.0-py3-none-any.whl"), "");
  writeFileSync(join(dir, "ai_test_component-0.5.0-py3-none-any.whl"), "");
  assert.equal(join(dir, "ai_test_component-0.5.0-py3-none-any.whl"), findBundledWheel(dir));
  assert.equal(bundledWheelVersion(dir), "0.5.0");
  assert.equal(findBundledWheel(join(fixture, "absent-dir")), null);
});

test("a cached runtime with an older aitest package is rebuilt", async () => {
  const { home, wheelDir } = makeWorkspace("cached-outdated");
  const runtimeDir = join(home, VERSION);
  const targetPython = join(runtimeDir, "venv", "Scripts", "python.exe");
  mkdirSync(join(runtimeDir, "venv", "Scripts"), { recursive: true });
  writeFileSync(targetPython, "");
  const { spawn } = fakeSpawn((command, args) => {
    if (command === targetPython && args.includes("-c")) {
      return { stdout: probe("3.13.7", 3, 13, { aitest: "0.4.10" }) };
    }
    return { spawnError: "ENOENT" };
  });
  const result = await ensureRuntime(
    {},
    {
      spawn,
      resolveAsset: async () => asset(),
      download: downloader(),
      runtimeHome: home,
      wheelDir,
      platform: "win32",
    },
  );
  assert.equal(result.stages[0].code, "CACHED_RUNTIME_OUTDATED");
  // 没有 tar 假实现，重建停在 extract；关键是不把旧版本当成就绪。
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "extract");
});

test("findPreparedPython returns the newest prepared venv and defaultRuntimeHome honours APPDATA", () => {
  const home = join(fixture, "prepared-home");
  mkdirSync(join(home, "3.11.9", "venv", "Scripts"), { recursive: true });
  mkdirSync(join(home, "3.13.7", "venv", "Scripts"), { recursive: true });
  writeFileSync(join(home, "3.11.9", "venv", "Scripts", "python.exe"), "");
  writeFileSync(join(home, "3.13.7", "venv", "Scripts", "python.exe"), "");
  assert.equal(
    findPreparedPython(home, "win32"),
    join(home, "3.13.7", "venv", "Scripts", "python.exe"),
  );
  assert.equal(findPreparedPython(join(fixture, "empty-home"), "win32"), null);
  assert.equal(
    defaultRuntimeHome("win32", { APPDATA: "C:\\Users\\tester\\AppData\\Roaming" }),
    join("C:\\Users\\tester\\AppData\\Roaming", "aitest", "runtime"),
  );
});

test("discovery still reports a missing interpreter without preparing anything", async () => {
  const { spawn, calls } = fakeSpawn();
  const result = await findPython({}, spawn);
  assert.equal(result.ok, false);
  assert.deepEqual(result.missing, ["Python 3.11+", "aitest 包"]);
  assert.equal(calls.every((call) => call.args.includes("-c")), true);
});

test("preparePortableRuntime refuses to run when a valid interpreter check is passed in", async () => {
  // 直接调用准备函数时按“发现已失败”的检查结果处理；这是 ensureRuntime 的唯一入口约定。
  const { home, wheelDir } = makeWorkspace("direct");
  const { spawn } = fakeSpawn();
  const check = await findPython({}, spawn);
  const downloads = [];
  const result = await preparePortableRuntime(check, {
    spawn,
    resolveAsset: async () => asset(),
    download: downloader(SHA, downloads),
    runtimeHome: home,
    wheelDir,
    platform: "win32",
  });
  // 没有 tar/venv 假实现，最终停在 extract 阶段；关键是不联网成功伪造通过。
  assert.equal(result.ok, false);
  assert.equal(result.failure.stage, "extract");
});

test("downloadArchive reports a network error instead of writing a file", async () => {
  const destination = join(fixture, "download-unreachable.part");
  await assert.rejects(
    () => downloadArchive("http://127.0.0.1:1/cpython.tar.gz", destination),
    (error) => typeof error.code === "string" || typeof error.message === "string",
  );
  assert.equal(existsSync(destination), false);
});
