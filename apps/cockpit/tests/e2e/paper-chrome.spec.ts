import { expect, test, type Page } from "@playwright/test";

import type { MarketTapeResponse } from "../../src/lib/market-tape-types";
import type { VenueCaptureStripResponse } from "../../src/lib/types";

/**
 * The public Hyperliquid routes are stubbed so the regress never depends on the
 * network: the cockpit must keep its PAPER chrome and labels even when the
 * public mid is unavailable.
 */
async function stubPublicMarketRoutes(page: Page): Promise<void> {
  await page.route("**/api/public-btc-perp", async (route) => {
    await route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ error: "fixture-only public mid is unavailable" }),
    });
  });
  await page.route("**/api/public-btc-perp-candles**", async (route) => {
    await route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ error: "fixture-only public candles are unavailable" }),
    });
  });
}

async function storedQuoteFixtures(page: Page, bboAgeMs = 36_000_000) {
  const response = await page.request.get("/api/venue-capture-health");
  const strip = (await response.json()) as VenueCaptureStripResponse;
  if (!strip.ok) throw new Error("fixture capture strip unavailable");
  const hl = strip.strip.venues.find((venue) => venue.id === "hl");
  if (hl === undefined) throw new Error("fixture HL chip unavailable");
  const observed = new Date().toISOString();
  const fresh = new Date(Date.now() - 1_000).toISOString();
  const old = new Date(Date.now() - bboAgeMs).toISOString();
  const run = "browser-quote-provenance";
  strip.strip.venues = [{ ...hl, run_id: run }];
  const tape: MarketTapeResponse = {
    ok: true,
    tape: {
      observed_at: observed,
      cache: { runsCached: 1, partsParsedThisCall: 0 },
      venues: [
        {
          id: "hl",
          chip: "HL",
          series: "DATA-1A",
          venue: "hyperliquid",
          contractProduct: "BTC-PERP",
          runId: run,
          bindingSource: "query",
          status: "ok",
          error: undefined,
          observedAt: observed,
          parts: {
            published: 1,
            processed: 1,
            skippedOnColdStart: 0,
            bytes: 1,
            newestPartName: undefined,
            newestPartMtimeUtc: undefined,
          },
          lastEventUtc: fresh,
          lastEventAgeS: 1,
          publicationLagS: 0,
          note: "Synthetic browser fixture only",
          instruments: [
            {
              product: "BTC-PERP",
              kind: "perpetual",
              base: "BTC",
              quote: "USDC",
              tradeCount: 1,
              bboCount: 1,
              contextCount: 0,
              lastTrade: { at: fresh, price: "81180.10", size: "1", side: "buy" },
              lastBbo: {
                at: old,
                bid: "81179.90",
                ask: "81180.10",
                bidSize: "1",
                askSize: "1",
                spread: "0.20",
                spreadBps: "0.02",
              },
              lastContext: undefined,
              recentTrades: [],
              lastEventUtc: fresh,
              channelsSeen: ["trades", "bbo"],
            },
          ],
        },
      ],
    },
  };
  return { strip, tape };
}

test.describe("PAPER cockpit chrome", () => {
  test("keeps Last/Mid freshness separate and clears prices during a run-selection race", async ({
    page,
  }) => {
    await stubPublicMarketRoutes(page);
    const { strip, tape } = await storedQuoteFixtures(page);
    await page.route("**/api/venue-capture-health**", async (route) => {
      await route.fulfill({ json: strip });
    });
    await page.route("**/api/market-tape**", async (route) => {
      await route.fulfill({ json: tape });
    });
    await page.goto("/markets");
    const row = page
      .locator("section.card")
      .filter({
        has: page.getByRole("heading", { name: "Bound venues · last / mid", exact: true }),
      })
      .locator("tbody tr");
    await expect(row).toHaveCount(1);
    await expect(row.locator("td").nth(2)).toContainText("FRESH");
    await expect(row.locator("td").nth(3)).toContainText("STALE");
    await expect(row.locator("td").nth(3)).toContainText("81,180.00");
    await expect(row.locator("td").nth(5)).toContainText("STALE");

    tape.tape.venues[0]!.runId = "other-selected-run";
    await page.getByRole("button", { name: "Refresh now", exact: true }).click();
    await expect(row.locator("td").nth(2)).toHaveText("UNAVAILABLE");
    await expect(row.locator("td").nth(3)).toHaveText("UNAVAILABLE");
    await expect(row).toContainText("run does not match");
  });

  test("fails closed on retained read errors and ages quotes while new reads stay in flight", async ({
    page,
  }) => {
    await stubPublicMarketRoutes(page);
    await page.clock.install({ time: new Date() });
    const { strip, tape } = await storedQuoteFixtures(page, 1_000);
    let failure: "none" | "strip" | "tape" = "none";
    let holdReads = false;
    let releaseReads = () => {};
    let successfulTapeReads = 0;
    const held = new Promise<void>((resolve) => {
      releaseReads = resolve;
    });
    await page.route("**/api/venue-capture-health**", async (route) => {
      if (holdReads) await held;
      await route.fulfill(
        failure === "strip"
          ? { status: 500, json: { ok: false, error: "offline strip refresh" } }
          : { json: strip },
      );
    });
    await page.route("**/api/market-tape**", async (route) => {
      if (holdReads) await held;
      if (failure === "tape") {
        await route.fulfill({ status: 500, json: { ok: false, error: "offline tape refresh" } });
      } else {
        successfulTapeReads += 1;
        await route.fulfill({ json: tape });
      }
    });
    await page.goto("/markets");
    const row = page
      .locator("section.card")
      .filter({
        has: page.getByRole("heading", { name: "Bound venues · last / mid", exact: true }),
      })
      .locator("tbody tr");
    await expect(row.locator("td").nth(2)).toContainText("FRESH");
    await expect(row.locator("td").nth(3)).toContainText("FRESH");
    for (const endpoint of ["tape", "strip"] as const) {
      failure = endpoint;
      await page.getByRole("button", { name: "Refresh now", exact: true }).click();
      await expect(row.locator("td").nth(2)).toHaveText("UNAVAILABLE");
      await expect(row.locator("td").nth(3)).toHaveText("UNAVAILABLE");
      await expect(row).toContainText(
        `${endpoint === "strip" ? "capture strip" : "market tape"} refresh failed`,
      );
      failure = "none";
      await page.getByRole("button", { name: "Refresh now", exact: true }).click();
      await expect(row.locator("td").nth(2)).toContainText("FRESH");
      await expect(row.locator("td").nth(3)).toContainText("FRESH");
    }
    holdReads = true;
    const readsBeforeExpiry = successfulTapeReads;
    await page.clock.fastForward(182_000);
    await expect(row.locator("td").nth(2)).toContainText("STALE");
    await expect(row.locator("td").nth(3)).toContainText("STALE");
    await expect(row.locator("td").nth(5)).toContainText("STALE");
    expect(successfulTapeReads).toBe(readsBeforeExpiry);
    const recoveredAt = await page.evaluate(() => new Date().toISOString());
    tape.tape.observed_at = recoveredAt;
    const instrument = tape.tape.venues[0]!.instruments[0]!;
    instrument.lastTrade!.at = recoveredAt;
    instrument.lastBbo!.at = recoveredAt;
    holdReads = false;
    releaseReads();
    await expect(row.locator("td").nth(2)).toContainText("FRESH");
    await expect(row.locator("td").nth(3)).toContainText("FRESH");
  });

  test("shows PAPER fail-closed chrome and no LIVE affordances on every workspace", async ({
    page,
  }) => {
    await stubPublicMarketRoutes(page);

    for (const path of ["/", "/markets", "/research", "/paper", "/system"]) {
      await page.goto(path);

      // Topbar PAPER pill (distinct from refresh feedback, which is also role=status).
      await expect(page.locator(".paper-pill")).toHaveText(/paper/i);
      await expect(page.getByRole("banner")).toContainText(/paper/i);
      await expect(page.getByRole("contentinfo")).toContainText("PAPER ONLY");
      await expect(page.locator(".brand-copy").first()).toContainText("PAPER");

      // Nothing on the page offers to sign, promote, withdraw or go LIVE.
      await expect(
        page.getByRole("button", { name: /live|promote|withdraw|sign|start|stop/i }),
      ).toHaveCount(0);
      await expect(page.getByRole("link", { name: /live|promote|withdraw|sign/i })).toHaveCount(0);
      await expect(page.locator("body")).not.toContainText("Enable LIVE");
      await expect(page.locator("body")).not.toContainText("Go LIVE");
    }

    // The fail-closed constraints stay reachable from the footer disclosure.
    await page.getByRole("contentinfo").getByText("Fail-closed constraints").click();
    const footer = page.getByRole("contentinfo");
    await expect(footer).toContainText("NO WALLET SIGNING");
    await expect(footer).toContainText("NO LIVE CAPITAL");
    await expect(footer).toContainText("NO CAPTURE START/STOP");
    await expect(footer).toContainText("PUBLIC MID != RESEARCH");
  });

  test("keeps public mid out of research truth: chart lives in Markets only", async ({ page }) => {
    await stubPublicMarketRoutes(page);

    await page.goto("/markets");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Markets");
    await expect(
      page.getByRole("img", { name: /public hyperliquid btc-perp .* candles/i }),
    ).toHaveCount(1);
    await expect(page.locator("body")).toContainText(/neither is research truth or paper pnl/i);

    await page.goto("/research");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Research");
    await expect(page.getByRole("img", { name: /candles/i })).toHaveCount(0);
    await expect(page.locator("body")).toContainText("public mid ≠ research truth");
    await expect(page.locator("body")).not.toContainText("Go LIVE");
  });

  test("keeps assumed_pnl labels and the #65 gate reference on the fixture PAPER desk", async ({
    page,
  }) => {
    await stubPublicMarketRoutes(page);
    await page.goto("/paper");

    const results = page.getByRole("region", { name: "Results" });
    await expect(results).toContainText("assumed_pnl");
    await expect(results).toContainText("-0.0127 USDC");
    await expect(results).toContainText("not venue-reconciled");
    await expect(results).toContainText("blocked");

    // The tape shows exactly what the fixture recorded: two ACCEPTs, no invented REJECT row.
    const tape = page.getByRole("region", { name: "Intent tape" });
    await expect(tape.locator("tbody tr")).toHaveCount(2);
    await expect(tape.locator("tbody tr", { hasText: "REJECT" })).toHaveCount(0);
    await expect(tape).toContainText("recorded no paper_risk reject");

    // The documented #65 / D01 gate stays visible, but as a separate reference card.
    const examples = page.getByRole("region", { name: "How a reject reads" });
    await expect(examples).toContainText("Documented #65 / D01 catalog");
    await expect(examples).toContainText("risk_based_size");
    await expect(examples).toContainText("never mixed into the tape");
    await expect(examples.getByText("example", { exact: true }).first()).toBeVisible();
  });
});
