import { describe, expect, it } from "vitest";

import { compareItemNo, toHostConfig } from "@/host/map";
import type { ProjectConfig } from "@/domain/types";

describe("host config mapping", () => {
  it("writes the CLI snake_case keys instead of panel camelCase keys", () => {
    const config: ProjectConfig = {
      services: { healthUrl: "http://127.0.0.1:8000/health", baseUrl: "http://127.0.0.1:8000" },
      evidence: { singleFileMb: 10, mediaMb: 200 },
      link: { logPath: "a.log, b.log" },
      data: { cleanupCommand: "npm run clean" },
    };
    const dto = toHostConfig(config);
    expect(dto.services).toEqual({
      health: "http://127.0.0.1:8000/health",
      base_url: "http://127.0.0.1:8000",
    });
    expect(dto.evidence).toEqual({
      file_cap_mb: 10,
      media_budget_mb: 200,
      video: false,
    });
    expect(dto.link).toEqual({ log_paths: ["a.log", "b.log"] });
    expect(dto.data).toEqual({ cleanup: "npm run clean" });
    expect(JSON.stringify(dto)).not.toContain("healthUrl");
    expect(JSON.stringify(dto)).not.toContain("singleFileMb");
  });

  it("sorts acceptance items by their numeric number", () => {
    expect(["04", "02", "10", "03"].sort(compareItemNo)).toEqual(["02", "03", "04", "10"]);
  });
});
