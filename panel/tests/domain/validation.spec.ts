import { describe, expect, it } from "vitest";

import { createProjectRecord } from "@/domain/factory";
import {
  isAbsolutePath,
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

  it("parses one-item-per-line lists", () => {
    expect(parseLineList("a\n b \n\nc")).toEqual(["a", "b", "c"]);
  });
});
