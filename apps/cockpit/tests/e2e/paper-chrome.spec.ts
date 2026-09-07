import { expect, test, type Page } from "@playwright/test";

async function stubPublicMarketRoutes(page: Page): Promise<void> {
  await page.route("**/api/public-btc-perp", async (route) => {
    await route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ error: "fixture-only public mid is unavailable" }),
    });
  });
  await page.route("**/api/public-btc-perp-candles", async (route) => {
    await route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ error: "fixture-only public candles are unavailable" }),
    });
  });
}

test.describe("PAPER cockpit chrome", () => {
  test("shows PAPER fail-closed chrome and no LIVE affordances", async ({ page }) => {
    await stubPublicMarketRoutes(page);
    await page.goto("/");

    await expect(page.getByRole("status")).toContainText("PAPER TRADING");
    await expect(page.getByRole("status")).toContainText("NO REAL CAPITAL");
    await expect(page.getByText("NO LIVE", { exact: true })).toBeVisible();
    await expect(page.getByText("SIGNING OFF", { exact: true })).toBeVisible();
    await expect(page.locator(".watermark")).toHaveText("PAPER");
    await expect(page.getByRole("button", { name: /live|promote|withdraw|sign/i })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("Enable LIVE");
    await expect(page.locator("body")).not.toContainText("Go LIVE");
  });

  test("keeps research truth, assumed_pnl, and #65 reject labels on the fixture desk", async ({
    page,
  }) => {
    await stubPublicMarketRoutes(page);
    await page.goto("/");

    await expect(page.getByText("public mid ≠ research truth")).toHaveCount(3);
    await expect(page.getByRole("region", { name: "RESEARCH", exact: true })).toContainText(
      "MARKETS only · not research truth",
    );
    await expect(page.getByRole("region", { name: "Results" })).toContainText("assumed_pnl");
    await expect(page.getByRole("region", { name: "Results" })).toContainText("-0.0127 USDC");
    await expect(page.getByRole("region", { name: "Results" })).toContainText(
      "not venue-reconciled",
    );
    await expect(page.getByRole("region", { name: "Results" })).toContainText("blocked");
    await expect(page.getByRole("region", { name: "Intent tape" })).toContainText("REJECT");
    await expect(page.getByRole("region", { name: "Intent tape" })).toContainText(
      "#65/paper_risk · risk_based_size",
    );
  });
});
