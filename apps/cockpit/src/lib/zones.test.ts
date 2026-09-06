import { describe, expect, it } from "vitest";

import { COCKPIT_NAV_ZONES, cockpitNavOrder } from "./zones";

describe("cockpit IA nav", () => {
  it("orders Health → Markets → Research → PAPER → Risk", () => {
    expect(cockpitNavOrder()).toEqual(["health", "markets", "research", "paper", "risk"]);
    expect(COCKPIT_NAV_ZONES.map((zone) => zone.label)).toEqual([
      "Health",
      "Markets",
      "Research",
      "PAPER",
      "Risk",
    ]);
  });
});
