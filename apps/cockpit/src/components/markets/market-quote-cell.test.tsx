import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MarketQuoteCell } from "./markets-screen";

describe("per-field market quote presentation", () => {
  it("renders a stale source with its own age and warning alongside the exact stored value", () => {
    const html = renderToStaticMarkup(
      <MarketQuoteCell
        value="81180.10"
        read={{
          at: "2026-09-06T00:15:59.000Z",
          age: "10h 0m",
          state: "stale",
          reason: "BBO is 10h old",
        }}
      />,
    );
    expect(html).toContain("81,180.10");
    expect(html).toContain("STALE");
    expect(html).toContain("updated 10h 0m ago");
    expect(html).toContain("tone-warn");
    expect(html).not.toContain("FRESH");
  });

  it("keeps a missing snapshot unavailable with its reason", () => {
    const html = renderToStaticMarkup(
      <MarketQuoteCell
        value="UNAVAILABLE"
        read={{
          at: undefined,
          age: "n/a",
          state: "unavailable",
          reason: "no L2 snapshot in the bounded stored tape",
        }}
      />,
    );
    expect(html).toContain("UNAVAILABLE");
    expect(html).toContain("no L2 snapshot");
    expect(html).not.toContain("FRESH");
  });
});
