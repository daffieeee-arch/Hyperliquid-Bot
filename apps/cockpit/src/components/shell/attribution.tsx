/**
 * Third-party attribution.
 *
 * Lightweight Charts is Apache-2.0 and its license requires naming TradingView
 * as the product creator with a link to tradingview.com. The in-chart
 * attribution logo is disabled so the dense candle panel stays readable, which
 * per the library docs makes this visible notice the thing that satisfies the
 * requirement — it must stay reachable on a public page.
 */
export function Attribution() {
  return (
    <details className="spacer">
      <summary>Attribution</summary>
      <div className="app-footer-list" style={{ display: "block", maxWidth: "70ch" }}>
        <p style={{ margin: "0 0 0.3rem" }}>
          Charts by TradingView Lightweight Charts™ — Copyright © 2025{" "}
          <a
            href="https://www.tradingview.com/"
            target="_blank"
            rel="noreferrer noopener"
            style={{ color: "var(--paper)" }}
          >
            TradingView, Inc.
          </a>{" "}
          (Apache-2.0).
        </p>
        <p style={{ margin: 0 }}>
          Also uses shadcn/ui, Radix UI, TanStack Table and recharts (MIT), lucide (ISC) and
          class-variance-authority (Apache-2.0). See{" "}
          <span className="mono">apps/cockpit/NOTICE</span>.
        </p>
      </div>
    </details>
  );
}
