import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchInventory, signOut } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("signOut", () => {
  it("rejects when the request never reaches the API", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(signOut()).rejects.toThrow();
  });

  it("rejects on a non-2xx, since the session cookie wasn't cleared", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 502 })));
    await expect(signOut()).rejects.toThrow();
  });

  it("resolves once the API has cleared the session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(signOut()).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("/auth/logout", { method: "POST" });
  });
});

describe("fetchInventory", () => {
  it("asks the server to bypass its cache on refresh", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", defindexes: [], fetched_at: 1 }))
    );
    vi.stubGlobal("fetch", fetchMock);
    await fetchInventory(true);
    expect(fetchMock).toHaveBeenCalledWith("/me/inventory?refresh=1");
  });
});
