import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CockpitRefreshProvider } from "../providers/cockpit-refresh";
import { loadNativePaperProjection } from "../../lib/native-paper";
import { NativePaperScreen } from "./native-paper-screen";

const repoRoot = resolve(fileURLToPath(new URL("../../../../../", import.meta.url)));
const fixture = resolve(repoRoot, "tests/fixtures/native_paper/native-demo");

describe("native PAPER panel", () => {
  it("renders exact economics, provenance and explicit snapshot limitations", () => {
    const projection = loadNativePaperProjection(
      { TRADING_MODE: "PAPER", COCKPIT_NATIVE_PAPER_RUN_DIR: fixture },
      repoRoot,
    );
    expect(projection.snapshot).not.toBeNull();
    const html = renderToStaticMarkup(
      <CockpitRefreshProvider>
        <NativePaperScreen projection={projection} />
      </CockpitRefreshProvider>,
    );
    for (const label of [
      "DEMO FIXTURE",
      "not venue-reconciled",
      "funding is not settled",
      "Liveness is unverified",
      "Realized gross PnL",
      "not free margin",
      "Ledger history",
      projection.snapshot!.sourceCommit,
    ])
      expect(html).toContain(label);
    expect(html).not.toContain("LIVE CAPTURE");
    expect(html).not.toContain(fixture);
  });

  it("preserves unavailable nulls and hides economics when the read fails closed", () => {
    const projection = loadNativePaperProjection(
      { TRADING_MODE: "PAPER", COCKPIT_NATIVE_PAPER_RUN_DIR: fixture },
      repoRoot,
    );
    if (projection.snapshot === null) throw new Error("fixture missing");
    projection.snapshot = {
      ...projection.snapshot,
      markPrice: null,
      unrealizedUsdc: null,
      equityUsdc: null,
    };
    const render = () =>
      renderToStaticMarkup(
        <CockpitRefreshProvider>
          <NativePaperScreen projection={projection} />
        </CockpitRefreshProvider>,
      );
    expect(render().match(/UNAVAILABLE/g)?.length).toBe(3);
    projection.snapshot = null;
    projection.state = "error";
    const html = render();
    expect(html).toContain("Native PAPER values unavailable");
    expect(html).not.toContain("Cash ledger (USDC)");
    expect(html).not.toContain("COURSE-1");
  });
});
