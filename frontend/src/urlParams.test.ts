import { describe, expect, it } from "vitest";
import { withoutParam } from "./urlParams";

const at = (search: string, pathname = "/", hash = "") => ({ pathname, search, hash });

describe("withoutParam", () => {
  it("strips only the named param", () => {
    expect(withoutParam(at("?auth=failed&build=abc"), "auth")).toBe("/?build=abc");
  });

  it('drops the "?" when the last param goes', () => {
    expect(withoutParam(at("?build=abc"), "build")).toBe("/");
  });

  it("keeps a base64url build value byte-for-byte", () => {
    expect(withoutParam(at("?build=eyJ-a_b&auth=failed"), "auth")).toBe("/?build=eyJ-a_b");
  });

  it("preserves pathname and hash", () => {
    expect(withoutParam(at("?build=a", "/x", "#h"), "build")).toBe("/x#h");
  });

  it("leaves the URL alone when the param is absent", () => {
    expect(withoutParam(at("?a=1"), "b")).toBe("/?a=1");
  });
});
