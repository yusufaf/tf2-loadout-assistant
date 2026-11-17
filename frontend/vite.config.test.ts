import { describe, expect, it } from "vitest";
import config from "./vite.config.ts";

describe("vite build config", () => {
  it("never inlines imported assets as data: URIs, which the CSP blocks", () => {
    expect(config.build?.assetsInlineLimit).toBe(0);
  });
});
