import { expect, test, type Page } from "@playwright/test";

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

test.describe("PAPER cockpit chrome", () => {
  test("shows PAPER fail-closed chrome and no LIVE affordances on every workspace", async ({
    page,
  }) => {
    await stubPublicMarketRoutes(page);

    for (const path of ["/", "/markets", "/research", "/paper", "/system"]) {
      await page.goto(path);

      // Topbar PAPER pill is the one status landmark; brand and footer repeat the mode.
      await expect(page.getByRole("status")).toHaveText(/paper/i);
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
