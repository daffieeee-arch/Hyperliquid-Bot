import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RESULT_UNAVAILABLE, parseHypothesisResultObject } from "../../lib/hypothesis-result-model";
import { listHypothesisResults } from "../../lib/hypothesis-results";
import { HypothesisResultsView } from "./hypothesis-results-view";

const repoRoot = resolve(fileURLToPath(new URL("../../../../../", import.meta.url)));
const fixtureRoot = join(repoRoot, "tests", "fixtures", "hypothesis_results");

function renderList(
  list: ReturnType<typeof listHypothesisResults>,
  nowIso: string | undefined,
): string {
  return renderToStaticMarkup(
    <HypothesisResultsView
      list={list}
      nowIso={nowIso}
      selectedId={undefined}
      onSelect={() => undefined}
    />,
  );
}

describe("hypothesis results view", () => {
  it("renders the fixture table, Amsterdam clock, and forbidden promotion", () => {
    const list = listHypothesisResults(
      { TRADING_MODE: "PAPER", COCKPIT_HYPOTHESIS_RESULTS: fixtureRoot },
      "2026-10-06T08:20:00.000Z",
    );
    const html = renderList(list, "2026-10-06T08:15:45.000Z");
    expect(html).toContain("Promotion is forbidden unless H1 passes");
    expect(html).toContain("does not promote a hypothesis");
    expect(html).toContain("WP1 H-1");
    expect(html).toContain("interesting_but_fragile");
    expect(html).toContain("0 / 12");
    expect(html).toContain("1.4");
    expect(html).toContain(RESULT_UNAVAILABLE);
    expect(html).toContain("2026-10-06 10:15:00 CEST");
    expect(html).toContain("updated 45s ago");
    expect(html).toContain("2026-04-01 02:00:00 CEST");
    expect(html).toContain("2026-04-01 – 2026-05-01");
    expect(html).toContain("2026-01-01 01:00:00 CET");
    expect(html).toContain("20260401t000000z-wp1-h1");
    expect(html).toContain("BTC-PERP");
    expect(html).toContain("hypothesis-wp1-btc-perp-v1");
    expect(html).toContain("run_id");
    expect(html).toContain("path_contract");
    expect(html).toContain("notice-gate");
    expect(html).toContain("table-scroll-sticky-first");
    expect(html).toContain("dt-nowrap");
    expect(html).toContain("badge-muted");
    expect(html).not.toContain("badge-down");
    expect(html).not.toContain("notice-stale");
    expect(html).toContain("taker-flow x vol");
    expect(html).toContain("cost-killed");
    expect(html).toContain("place an order");
    expect(html).not.toContain("Place order");
    expect(html).not.toContain("Submit order");
    expect(html.toLowerCase()).not.toContain("promote to");
  });

  it("shows a partial artifact as UNAVAILABLE instead of zero", () => {
    const list = listHypothesisResults({
      TRADING_MODE: "PAPER",
      COCKPIT_HYPOTHESIS_RESULTS: fixtureRoot,
    });
    const html = renderList(list, undefined);
    expect(html).toContain("WP2 H-2");
    expect(html).toContain("0.2");
    expect(html).not.toContain(">0 / 4<");
    expect(html).toContain(`UNAVAILABLE / 4`);
    const partial = renderToStaticMarkup(
      <HypothesisResultsView
        list={list}
        nowIso={undefined}
        selectedId="wp2-partial"
        onSelect={() => undefined}
      />,
    );
    expect(partial).toContain("run_id");
    expect(partial).toContain("product");
    expect(partial).toContain("path_contract");
    expect(partial).toContain("Best net bps/trade");
    expect(partial.indexOf("Best net bps/trade")).toBeLessThan(
      partial.indexOf("Best gross bps/trade"),
    );
  });

  it("renders a malformed file without throwing and escapes report HTML", () => {
    const root = mkdtempSync(join(tmpdir(), "hypothesis-view-"));
    mkdirSync(join(root, "bad"));
    writeFileSync(join(root, "bad", "result.json"), "{not json", "utf8");
    const hostile = parseHypothesisResultObject("hostile", "hostile/result.json", {
      schema_version: "hypothesis-result-v1",
      trading_mode: "PAPER",
      work_package: "WP4",
      hypothesis_id: "H-4",
      passes_h1: false,
      promotion_decision: "promote",
      label: "interesting_but_fragile",
    });
    hostile.reportMarkdown = "Hello <script>alert(1)</script>";
    const broken = listHypothesisResults({
      TRADING_MODE: "PAPER",
      COCKPIT_HYPOTHESIS_RESULTS: root,
    });
    const html = renderToStaticMarkup(
      <HypothesisResultsView
        list={{ ...broken, items: [...broken.items, hostile] }}
        nowIso="2026-10-06T08:15:00.000Z"
        selectedId="hostile"
        onSelect={() => undefined}
      />,
    );
    expect(html).toContain("not valid JSON");
    expect(html).toContain(RESULT_UNAVAILABLE);
    expect(html).toContain("Recorded promotion_decision is promote");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });
});
