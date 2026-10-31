import { afterEach, describe, expect, it, vi } from "vitest";

import { NativePaperScreen } from "../../components/paper/native-paper-screen";
import { PaperScreen } from "../../components/paper/paper-screen";
import { loadCockpitPageData } from "../../lib/server-load";
import PaperPage from "./page";

vi.mock("../../lib/server-load", () => ({
  loadCockpitPageData: vi.fn(() => ({ paper: {}, identities: {} })),
}));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("PAPER page source selection", () => {
  it("retains the existing COURSE-1 path when native binding is absent", async () => {
    vi.stubEnv("TRADING_MODE", "PAPER");
    vi.stubEnv("COCKPIT_NATIVE_PAPER_RUN_DIR", undefined);
    const page = await PaperPage({ searchParams: Promise.resolve({}) });
    expect(page.type).toBe(PaperScreen);
    expect(loadCockpitPageData).toHaveBeenCalledOnce();
  });

  it.each(["", "/nonexistent-native-run"])(
    "selects native for explicit binding %j without COURSE-1 fallback",
    async (binding) => {
      vi.stubEnv("TRADING_MODE", "PAPER");
      vi.stubEnv("COCKPIT_NATIVE_PAPER_RUN_DIR", binding);
      const page = await PaperPage({ searchParams: Promise.resolve({}) });
      expect(page.type).toBe(NativePaperScreen);
      expect(page.props).toHaveProperty("projection.snapshot", null);
      expect(loadCockpitPageData).not.toHaveBeenCalled();
    },
  );

  it("refuses non-PAPER before either reader runs", async () => {
    vi.stubEnv("TRADING_MODE", "TESTNET");
    vi.stubEnv("COCKPIT_NATIVE_PAPER_RUN_DIR", "");
    await expect(PaperPage({ searchParams: Promise.resolve({}) })).rejects.toThrow();
    expect(loadCockpitPageData).not.toHaveBeenCalled();
  });
});
