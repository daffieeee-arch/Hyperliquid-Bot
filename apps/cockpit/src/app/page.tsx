import { LiveBtcPrice } from "../components/live-btc-price";
import { loadPaperRunSnapshot } from "../lib/paper-run";
import type { PaperRunSnapshot } from "../lib/types";

export const dynamic = "force-dynamic";

export default function FirstPaperScreen() {
  let snapshotError: string | undefined;
  let snapshot: PaperRunSnapshot | undefined;
  try {
    snapshot = loadPaperRunSnapshot();
  } catch (error: unknown) {
    snapshotError = error instanceof Error ? error.message : "PAPER run data is unavailable.";
  }

  return (
    <main>
      <section className="banner">
        <strong>PAPER TRADING — NO REAL CAPITAL</strong>
        <p>
          Fail-closed PAPER only. The browser never signs orders and never holds keys. This is a
          bounded reconstructable run view, not a 24/7 runtime.
        </p>
      </section>

      <div className="grid">
        <LiveBtcPrice />

        {snapshotError !== undefined || snapshot === undefined ? (
          <section className="panel">
            <h2>PAPER run</h2>
            <p className="error">{snapshotError ?? "PAPER run data is unavailable."}</p>
            <p className="note">
              Numbers are not invented when reconstructable JSON is missing or not PAPER.
            </p>
          </section>
        ) : (
          <>
            <section className="panel">
              <h2>Paper position</h2>
              <p className="value">{snapshot.position.final_position_btc} BTC</p>
              <p className="meta">{snapshot.position.instrument_id}</p>
              <p className="note">
                Sandbox PAPER position from <code>paper-position.json</code>. Not venue account
                truth. Venue authoritative: {snapshot.position.venue_authoritative ? "yes" : "no"}.
              </p>
              <p className="note">
                run_id <code>{snapshot.runId}</code> · {snapshot.orders.order_count} PAPER intents ·{" "}
                {snapshot.fills.fill_count} PAPER fills · venue orders submitted:{" "}
                {snapshot.orders.venue_orders_submitted ? "yes" : "no"}
              </p>
            </section>

            <section className="panel">
              <h2>Assumed overlay PnL</h2>
              <p className="value">{snapshot.pnl.net_pnl_usdc_assumed} USDC</p>
              <p className="meta">
                Assumed D01 overlay · not venue PnL · funding {snapshot.pnl.funding_payment_usdc}
              </p>
              <p className="note">
                starting {snapshot.pnl.starting_cash_usdc_assumed} · ending cash{" "}
                {snapshot.pnl.ending_cash_usdc_assumed} · ending equity{" "}
                {snapshot.pnl.ending_equity_usdc_assumed}
              </p>
              <p className="note">
                fee {snapshot.pnl.fee_cost_usdc} · half-spread {snapshot.pnl.half_spread_cost_usdc}{" "}
                · slippage {snapshot.pnl.slippage_cost_usdc} · soak mark {snapshot.pnl.mark_price}
              </p>
              <p className="note">
                Copied from <code>paper-pnl.json</code>. The live public mid is not used to compute
                this number.
              </p>
            </section>

            <section className="panel">
              <h2>Capture health</h2>
              <p className="value">{snapshot.health.status}</p>
              <p className="meta">{snapshot.health.feed}</p>
              <p className="note">
                trades {snapshot.health.trade_count} · bbo {snapshot.health.bbo_count} · adapter
                rejected {snapshot.health.adapter_rejected_count} · risk rejections{" "}
                {snapshot.health.risk_rejections}
              </p>
              <p className="note">
                subscriptions: {snapshot.health.subscriptions_acknowledged.join(", ")} ·
                credentialless: {snapshot.health.credentialless ? "yes" : "no"} · 24/7 claim:{" "}
                {snapshot.health.twenty_four_seven ? "yes" : "no"}
              </p>
              <p className="note">
                Bounded soak summary from <code>capture-health.json</code>, not a 24/7 heartbeat.
              </p>
            </section>
          </>
        )}
      </div>

      {snapshot !== undefined ? (
        <>
          <ul className="limitations">
            {[
              ...snapshot.position.limitations,
              ...snapshot.pnl.limitations,
              ...snapshot.health.limitations,
            ].map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="source">
            Source {snapshot.source}: <code>{snapshot.runDir}</code>
          </p>
        </>
      ) : null}
    </main>
  );
}
