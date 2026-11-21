import { describe, expect, it } from "vitest";

import { createProjectRecord } from "@/domain/factory";
import {
  isAbsolutePath,
  normalizeRoot,
  parseLineList,
  validateProjectConfig,
  validateProjectForm,
} from "@/domain/validation";

describe("project validation", () => {
  it("accepts Windows and POSIX absolute paths", () => {
    expect(isAbsolutePath("D:\\work\\service")).toBe(true);
    expect(isAbsolutePath("D:/work/service")).toBe(true);
    expect(isAbsolutePath("\\\\server\\share\\service")).toBe(true);
    expect(isAbsolutePath("/home/me/service")).toBe(true);
    expect(isAbsolutePath("work/service")).toBe(false);
    expect(isAbsolutePath("")).toBe(false);
  });

  it("rejects duplicate and overlapping project roots", () => {
    const project = createProjectRecord({ name: "服务", root: "D:\\work\\service" });
    expect(
      validateProjectForm({ root: "D:\\work\\service", name: "重复" }, [project]).errors.root,
    ).toContain("已经登记");
    expect(
      validateProjectForm({ root: "D:\\work\\service\\sub", name: "子目录" }, [project]).errors
        .root,
    ).toContain("重叠");
    expect(
      validateProjectForm({ root: "D:\\work", name: "父目录" }, [project]).errors.root,
    ).toContain("重叠");
    expect(validateProjectForm({ root: "D:\\work\\other", name: "其他" }, [project]).ok).toBe(true);
  });

  it("uses the directory name when project name is blank and enforces 40 chars", () => {
    const auto = validateProjectForm({ root: "D:\\work\\order-service", name: "" }, []);
    expect(auto.name).toBe("order-service");
    const tooLong = validateProjectForm({ root: "D:\\work\\service", name: "x".repeat(41) }, []);
    expect(tooLong.ok).toBe(false);
    expect(tooLong.errors.name).toContain("40");
  });

  it("rejects unknown config sections and credential-shaped keys", () => {
    expect(validateProjectConfig({ unknown: {} }).ok).toBe(false);
    expect(validateProjectConfig({ l1: { build: "pnpm build" } }).ok).toBe(true);
    expect(validateProjectConfig({ services: { password: "x" } }).ok).toBe(false);
    expect(validateProjectConfig({ model: { apiKey: "x" } }).ok).toBe(false);
  });

  it("rejects non-positive evidence limits and invalid service numbers", () => {
    expect(validateProjectConfig({ evidence: { singleFileMb: 0 } }).ok).toBe(false);
    expect(validateProjectConfig({ evidence: { mediaMb: -1 } }).ok).toBe(false);
    expect(validateProjectConfig({ evidence: { singleFileMb: 20 } }).ok).toBe(true);
    expect(validateProjectConfig({ services: { port: 70000 } }).ok).toBe(false);
    expect(validateProjectConfig({ services: { port: 8080 } }).ok).toBe(true);
    expect(validateProjectConfig({ services: { timeoutSeconds: 0 } }).ok).toBe(false);
    expect(validateProjectConfig({ services: { timeoutSeconds: 30 } }).ok).toBe(true);
  });

  it("rejects wrong types for service URLs and L1 commands", () => {
    expect(validateProjectConfig({ services: { healthUrl: 123 } }).ok).toBe(false);
    expect(validateProjectConfig({ services: { healthUrl: "not-a-url" } }).ok).toBe(false);
    expect(
      validateProjectConfig({ services: { healthUrl: "http://127.0.0.1:8000/health" } }).ok,
    ).toBe(true);
    expect(validateProjectConfig({ l1: { build: ["npm", "run", "build"] } }).ok).toBe(false);
    expect(validateProjectConfig({ l1: { build: "npm run build" } }).ok).toBe(true);
    expect(validateProjectConfig({ l1: { entry: null } }).ok).toBe(true);
  });

  it("parses one-item-per-line lists", () => {
    expect(parseLineList("a\n b \n\nc")).toEqual(["a", "b", "c"]);
  });

  it("normalizes separators and dot segments before comparing project roots", () => {
    expect(normalizeRoot("D:\\Work\\Service\\")).toBe("d:/work/service");
    expect(normalizeRoot("D:/work/./service/")).toBe("d:/work/service");
    expect(normalizeRoot("D:/work/service/sub/..")).toBe("d:/work/service");
    expect(normalizeRoot("/home/me/../me/service/")).toBe("/home/me/service");
    expect(normalizeRoot("\\\\server\\share\\service\\")).toBe("//server/share/service");
    const project = createProjectRecord({ name: "服务", root: "D:\\work\\service" });
    expect(
      validateProjectForm({ root: "d:/work/service/./", name: "绕过" }, [project]).ok,
    ).toBe(false);
  });

  it("does not reject legitimate keys that merely contain credential words", () => {
    expect(validateProjectConfig({ data: { tokenizer: "simple" } }).ok).toBe(true);
    expect(validateProjectConfig({ runners: { monkey: "yes" } }).ok).toBe(true);
    expect(validateProjectConfig({ model: { fields: { key: "id" } } }).ok).toBe(true);
    expect(validateProjectConfig({ model: { access_token: "secret" } }).ok).toBe(false);
  });
});
