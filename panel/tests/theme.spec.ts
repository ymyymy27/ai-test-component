import { beforeEach, describe, expect, it } from "vitest";

import { resolvedTheme, setThemeMode, themeMode, toggleTheme } from "@/state/theme";

describe("主题切换", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.dataset.theme = "";
  });

  it("浅色模式写到 html 与 localStorage", () => {
    setThemeMode("light");
    expect(themeMode.value).toBe("light");
    expect(resolvedTheme.value).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("aitest.theme")).toBe("light");
  });

  it("从浅色切换到深色", () => {
    setThemeMode("light");
    toggleTheme();
    expect(themeMode.value).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("aitest.theme")).toBe("dark");
  });

  it("跟随系统时保存 system 模式", () => {
    setThemeMode("system");
    expect(themeMode.value).toBe("system");
    expect(localStorage.getItem("aitest.theme")).toBe("system");
  });
});
