import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "../app/api/native-paper-run/route";
import { loadNativePaperProjection } from "./native-paper";
import * as nativeReader from "./native-paper";
import { fetchNativePaperProjection, parseNativePaperResponse } from "./use-native-paper";

vi.mock("next/cache", () => ({ unstable_noStore: vi.fn() }));
const repoRoot = resolve(fileURLToPath(new URL("../../../../", import.meta.url)));
const fixture = resolve(repoRoot, "tests/fixtures/native_paper/native-demo");

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("native PAPER API and refresh contract", () => {
  it.each(["LIVE", "SHADOW", "TESTNET", "paper"])(
    "refuses %s with 403 and sanitized no-store error",
    async (mode) => {
      vi.stubEnv("TRADING_MODE", mode);
      vi.stubEnv("COCKPIT_NATIVE_PAPER_RUN_DIR", "/private/operator-path");
      const response = await GET();
      expect(response.status).toBe(403);
      expect(response.headers.get("cache-control")).toContain("no-store");
      const body: unknown = await response.json();
      expect(JSON.stringify(body)).not.toContain("operator-path");
      expect(() => parseNativePaperResponse(body)).toThrow(/contract/);
    },
  );

  it("returns the same projection contract as SSR without a directory field", async () => {
    vi.stubEnv("TRADING_MODE", "PAPER");
    vi.stubEnv("COCKPIT_NATIVE_PAPER_RUN_DIR", fixture);
    const response = await GET();
    const projection = parseNativePaperResponse(await response.json());
    const server = loadNativePaperProjection();
    expect(projection).toEqual({ ...server, observed_at: projection.observed_at });
    expect(projection.origin).toBe("demo");
    expect(projection.snapshot?.runId).toBe("native-demo");
    expect(JSON.stringify(projection)).not.toContain(fixture);
    expect(response.headers.get("cache-control")).toContain("no-store");
  });

  it("keeps an empty binding unbound and reports missing artifacts without path disclosure", async () => {
    vi.stubEnv("TRADING_MODE", "PAPER");
    vi.stubEnv("COCKPIT_NATIVE_PAPER_RUN_DIR", "");
    expect(parseNativePaperResponse(await (await GET()).json())).toMatchObject({
      origin: "unbound",
      state: "missing",
      snapshot: null,
    });
    vi.stubEnv("COCKPIT_NATIVE_PAPER_RUN_DIR", "/private/nonexistent-native-test-run");
    const missing = parseNativePaperResponse(await (await GET()).json());
    expect(missing).toMatchObject({ state: "missing", snapshot: null });
    expect(JSON.stringify(missing)).not.toContain("/private/");
  });

  it("uses no-store and refuses failed HTTP reads instead of treating stale values as current", async () => {
    const projection = loadNativePaperProjection({ TRADING_MODE: "PAPER" }, repoRoot);
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => projection });
    vi.stubGlobal("fetch", fetchMock);
    expect(await fetchNativePaperProjection()).toEqual(projection);
    expect(fetchMock).toHaveBeenCalledWith("/api/native-paper-run", { cache: "no-store" });
    fetchMock.mockResolvedValue({ ok: false });
    await expect(fetchNativePaperProjection()).rejects.toThrow(/not current/);
  });

  it("sanitizes unexpected reader exceptions and does not cache the failure", async () => {
    vi.stubEnv("TRADING_MODE", "PAPER");
    vi.spyOn(nativeReader, "loadNativePaperProjection").mockImplementation(() => {
      throw new Error("secret /private/path payload");
    });
    const response = await GET();
    expect(response.status).toBe(500);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(await response.json()).toEqual({
      ok: false,
      error: "Native PAPER snapshot could not be read safely.",
    });
  });

  it("rejects malformed and incompatible responses", () => {
    const projection = loadNativePaperProjection({ TRADING_MODE: "PAPER" }, repoRoot);
    for (const payload of [
      null,
      {},
      { ...projection, schema: "v2" },
      { ...projection, origin: "live" },
      { ...projection, snapshot: {} },
    ]) {
      expect(() => parseNativePaperResponse(payload)).toThrow(/contract/);
    }
  });

  it("rejects malformed history rows and unavailable states carrying economic values", () => {
    const projection = loadNativePaperProjection(
      { TRADING_MODE: "PAPER", COCKPIT_NATIVE_PAPER_RUN_DIR: fixture },
      repoRoot,
    );
    expect(projection.snapshot).not.toBeNull();
    const snapshot = projection.snapshot!;
    for (const [category, row] of [
      ["orders", { ...snapshot.orders[0], quantity: {}, side: "BUY" }],
      ["orders", { ...snapshot.orders[0], side: "MAYBE" }],
      ["fills", { ...snapshot.fills[0], price: null }],
      ["rejections", { ordinal: 1, reason: {}, detail: null }],
    ] as const) {
      expect(() =>
        parseNativePaperResponse({ ...projection, snapshot: { ...snapshot, [category]: [row] } }),
      ).toThrow(/contract/);
    }
    expect(() => parseNativePaperResponse({ ...projection, state: "error" })).toThrow(/contract/);
  });
});
