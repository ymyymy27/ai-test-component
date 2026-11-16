import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 构建 aitest wheel 与它的依赖 wheel，一起放进 media/runtime，供首次使用时离线安装。
//
// - wheel 优先用 `uv build --wheel`，其次 `python -m build --wheel`；都失败即报错退出，
//   不生成“没有 wheel 的 VSIX”。
// - 依赖按 pyproject.toml 的 [project].dependencies，为运行时可能选中的
//   CPython 3.11/3.12/3.13 (win_amd64) 各下一份二进制 wheel。
//   `pip install --no-index --find-links media/runtime` 因此能完整解析依赖。
// - 离线构建可设 AITEST_RUNTIME_SKIP_DEPS=1 跳过依赖（生成的 VSIX 无法离线安装，
//   脚本会明确警告，不静默）。
const scriptDir = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(scriptDir, "..");
const repoRoot = resolve(extensionRoot, "..", "..");
const distDir = join(repoRoot, "dist");
const outDir = join(extensionRoot, "media", "runtime");
const RUNTIME_TARGETS = ["3.11", "3.12", "3.13"];

const buildPython =
  (process.env.AITEST_BUILD_PYTHON ?? "").trim() || (process.platform === "win32" ? "python" : "python3");

function run(command, args, options = {}) {
  // 不经过 shell：依赖约束里含 >= / <，在 cmd 下会被当成重定向。
  return spawnSync(command, args, {
    cwd: repoRoot,
    stdio: "inherit",
    ...options,
  });
}

function wheelCandidates(directory) {
  try {
    return readdirSync(directory)
      .filter((name) => name.toLowerCase().endsWith(".whl"))
      .map((name) => join(directory, name));
  } catch {
    return [];
  }
}

function projectDependencies() {
  const text = readFileSync(join(repoRoot, "pyproject.toml"), "utf8");
  const section = /^\[project\]\s*$([\s\S]*?)^\[/m.exec(text);
  const block = section ? /^dependencies\s*=\s*\[([\s\S]*?)\]/m.exec(section[1]) : null;
  if (!block) {
    return [];
  }
  return [...block[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]);
}

function copyWheels(from, into) {
  let copied = 0;
  for (const path of wheelCandidates(from)) {
    const name = path.split(/[\\/]/).pop();
    copyFileSync(path, join(into, name));
    copied += 1;
  }
  return copied;
}

mkdirSync(distDir, { recursive: true });

const uv = run("uv", ["build", "--wheel", "--out-dir", distDir]);
let built = uv.status === 0 && wheelCandidates(distDir).length > 0;
if (!built) {
  console.warn("uv build 不可用，改用 python -m build --wheel。");
  const fallback = run(buildPython, ["-m", "build", "--wheel", "--outdir", distDir]);
  if (fallback.error || fallback.status !== 0) {
    console.error(
      "无法构建 aitest wheel：uv build --wheel 与 python -m build --wheel 都失败。\n" +
        "请先安装 uv（推荐）或 `python -m pip install build` 后重试；" +
        "VSIX 必须包含 media/runtime/*.whl 才能离线准备运行时。",
    );
    process.exit(1);
  }
}

const wheels = wheelCandidates(distDir)
  .map((path) => ({ path, mtime: statSync(path).mtimeMs }))
  .sort((left, right) => right.mtime - left.mtime);
if (wheels.length === 0) {
  console.error(`未在 ${distDir} 找到 wheel，构建失败。`);
  process.exit(1);
}
const source = wheels[0].path;
const name = source.split(/[\\/]/).pop() ?? "";
if (!/^ai[_-]?test/i.test(name)) {
  console.warn(`注意：wheel 文件名 ${name} 不以 aitest 开头；运行时按 *.whl 发现，不影响安装。`);
}

// 只保留最新一份 aitest wheel，避免 VSIX 里堆多个版本。
rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });
copyFileSync(source, join(outDir, name));
console.log(`已放入 media/runtime/${name}`);

// 依赖 wheel：与 aitest wheel 同目录，离线安装时作为 --find-links。
const requirements = projectDependencies();
const skipDeps = (process.env.AITEST_RUNTIME_SKIP_DEPS ?? "").trim() === "1";
if (skipDeps) {
  console.warn(
    "AITEST_RUNTIME_SKIP_DEPS=1：未打包依赖 wheel。" +
      "生成的 VSIX 在干净机器上会在 install 阶段失败（pip --no-index 缺 pydantic/portalocker）。",
  );
} else if (requirements.length === 0) {
  console.error("无法从 pyproject.toml 的 [project].dependencies 读取依赖，拒绝打包不完整的运行时。");
  process.exit(1);
} else {
  for (const target of RUNTIME_TARGETS) {
    const staging = mkdtempSync(join(tmpdir(), `aitest-runtime-deps-${target}-`));
    try {
      const downloaded = run(buildPython, [
        "-m",
        "pip",
        "download",
        "--only-binary=:all:",
        "--platform",
        "win_amd64",
        "--python-version",
        target,
        "--implementation",
        "cp",
        "--abi",
        `cp${target.replace(".", "")}`,
        "--disable-pip-version-check",
        "--dest",
        staging,
        ...requirements,
      ]);
      if (downloaded.error || downloaded.status !== 0) {
        console.error(
          `无法为 Python ${target} 下载依赖 wheel（需要网络与可用的 pip）。\n` +
            "请检查网络/代理后重试；确需离线构建时设 AITEST_RUNTIME_SKIP_DEPS=1，" +
            "但要接受生成的 VSIX 无法在干净机器上离线安装。",
        );
        process.exit(1);
      }
      const copied = copyWheels(staging, outDir);
      console.log(`已放入 Python ${target} 的依赖 wheel ${copied} 个`);
    } finally {
      rmSync(staging, { recursive: true, force: true });
    }
  }
}

const total = wheelCandidates(outDir).length;
console.log(`media/runtime 就绪：${total} 个 wheel（含 aitest 自身）。`);
