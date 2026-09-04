import { KvTable } from "../components/kv-table";
import { LiveBtcPrice } from "../components/live-btc-price";
import { MetricTile } from "../components/metric-tile";
import { PaperBlotter } from "../components/paper-blotter";
import { formatGroupedNumber, healthTone, signedTone, uniqueStrings, yesNo } from "../lib/display";
import { loadPaperRunSnapshot } from "../lib/paper-run";
import type { PaperRunSnapshot } from "../lib/types";

export const dynamic = "force-dynamic";

function sourceLabel(source: PaperRunSnapshot["source"]): string {
  if (source === "default-fixture") {
    return "default-fixture";
  }
  if (source === "path-contract") {
    return "path-contract";
  }
  return "paper-run-dir";
}

function Masthead({
  snapshot,
  snapshotError,
}: {
  snapshot: PaperRunSnapshot | undefined;
  snapshotError: string | undefined;
}) {
  return (
    <header className="masthead">
      <div className="masthead-row">
        <div className="brand">
          <span className="brand-mark">HLQ</span>
          <div className="brand-copy">
            <strong>Cockpit</strong>
            <span>BTC-PERP · COURSE-1</span>
          </div>
        </div>
        <div className="paper-badge" role="status">
          <span className="paper-badge-mode">PAPER TRADING</span>
          <span className="paper-badge-warn">NO REAL CAPITAL</span>
        </div>
        <p className="masthead-flags">
          <span>SIGNING OFF</span>
          <span>NO KEYS</span>
          <span>NO LIVE</span>
        </p>
      </div>
      <dl className="identity">
        <div>
          <dt>Mode</dt>
          <dd className="tone-paper">PAPER</dd>
        </div>
        <div>
          <dt>Instrument</dt>
          <dd>{snapshot?.position.instrument_id ?? "BTC-USD-PERP.HYPERLIQUID"}</dd>
        </div>
        <div>
          <dt>Run</dt>
          <dd>{snapshot?.runId ?? "unavailable"}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>{snapshot ? sourceLabel(snapshot.source) : "fail-closed"}</dd>
        </div>
        <div>
          <dt>Signing</dt>
          <dd>off</dd>
        </div>
        {snapshotError !== undefined ? (
          <div>
            <dt>JSON</dt>
            <dd className="tone-warn">missing</dd>
          </div>
        ) : null}
      </dl>
    </header>
  );
}

function SnapshotDesk({ snapshot }: { snapshot: PaperRunSnapshot }) {
  const pnlTone = signedTone(snapshot.pnl.net_pnl_usdc_assumed);
  const statusTone = healthTone(snapshot.health.status);
  const soakSeconds =
    snapshot.claim.seconds === undefined ? "n/a" : `${String(snapshot.claim.seconds)}s`;

  return (
    <>
      <section className="metrics" aria-label="PAPER run snapshot">
        <LiveBtcPrice />
        <MetricTile
          label="Paper position"
          value={`${formatGroupedNumber(snapshot.position.final_position_btc)} BTC`}
          meta={`${snapshot.position.instrument_id} · sandbox PAPER`}
          note={`Venue authoritative ${yesNo(snapshot.position.venue_authoritative)} · not account truth`}
          tone="neutral"
        />
        <MetricTile
          label="Assumed overlay PnL"
          value={`${formatGroupedNumber(snapshot.pnl.net_pnl_usdc_assumed)} USDC`}
          meta="Copied from paper-pnl.json · not venue PnL"
          note={`Funding ${formatGroupedNumber(snapshot.pnl.funding_payment_usdc)} · live mid not applied`}
          tone={pnlTone === "unknown" ? "neutral" : pnlTone}
        />
        <MetricTile
          label="Capture health"
          value={snapshot.health.status}
          meta={`${snapshot.health.feed} · bounded soak`}
          note={`24/7 claim ${yesNo(snapshot.health.twenty_four_seven)} · credentialless ${yesNo(snapshot.health.credentialless)}`}
          tone={statusTone}
        />
      </section>

      <div className="board">
        <section className="panel">
          <div className="panel-head">
            <h2>Position / identity</h2>
            <p className="panel-kicker">Reconstructable PAPER position, not D22-B venue truth</p>
          </div>
          <KvTable
            rows={[
              { label: "Run id", value: snapshot.runId },
              { label: "Claim state", value: snapshot.claim.state ?? "n/a" },
              { label: "Soak", value: soakSeconds },
              { label: "Feed", value: snapshot.claim.feed ?? snapshot.health.feed },
              { label: "Final position", value: `${snapshot.position.final_position_btc} BTC` },
              {
                label: "Business qty",
                value: snapshot.position.business_final_position_quantity,
              },
              {
                label: "Venue authoritative",
                value: yesNo(snapshot.position.venue_authoritative),
              },
              {
                label: "D22-B recon",
                value: yesNo(snapshot.claim.d22b_venue_authoritative_reconciliation === true),
              },
              { label: "Intents", value: String(snapshot.orders.order_count) },
              { label: "Fills", value: String(snapshot.fills.fill_count) },
              {
                label: "Venue orders submitted",
                value: yesNo(snapshot.orders.venue_orders_submitted),
              },
            ]}
          />
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Assumed overlay ledger</h2>
            <p className="panel-kicker">D01 overlay economics copied from paper-pnl.json</p>
          </div>
          <KvTable
            rows={[
              {
                label: "Net PnL (assumed)",
                value: `${formatGroupedNumber(snapshot.pnl.net_pnl_usdc_assumed)} USDC`,
                tone: pnlTone === "unknown" ? "neutral" : pnlTone,
              },
              {
                label: "Starting cash",
                value: `${formatGroupedNumber(snapshot.pnl.starting_cash_usdc_assumed)} USDC`,
              },
              {
                label: "Ending cash",
                value: `${formatGroupedNumber(snapshot.pnl.ending_cash_usdc_assumed)} USDC`,
              },
              {
                label: "Ending equity",
                value: `${formatGroupedNumber(snapshot.pnl.ending_equity_usdc_assumed)} USDC`,
              },
              { label: "Fee", value: `${snapshot.pnl.fee_cost_usdc} USDC` },
              { label: "Half-spread", value: `${snapshot.pnl.half_spread_cost_usdc} USDC` },
              { label: "Slippage", value: `${snapshot.pnl.slippage_cost_usdc} USDC` },
              { label: "Funding", value: `${snapshot.pnl.funding_payment_usdc} USDC` },
              { label: "Soak mark", value: formatGroupedNumber(snapshot.pnl.mark_price) },
              {
                label: "Assumed / venue PnL",
                value: `${yesNo(snapshot.pnl.assumed)} / ${yesNo(snapshot.pnl.venue_pnl)}`,
              },
            ]}
          />
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Capture health</h2>
            <p className="panel-kicker">Bounded soak summary, not a 24/7 heartbeat</p>
          </div>
          <KvTable
            rows={[
              { label: "Status", value: snapshot.health.status, tone: statusTone },
              { label: "Trades", value: String(snapshot.health.trade_count) },
              { label: "BBO", value: String(snapshot.health.bbo_count) },
              { label: "Adapter rejected", value: String(snapshot.health.adapter_rejected_count) },
              { label: "Risk rejections", value: String(snapshot.health.risk_rejections) },
              {
                label: "Subscriptions",
                value: snapshot.health.subscriptions_acknowledged.join(", ") || "none",
              },
              {
                label: "Credentialless",
                value: yesNo(snapshot.health.credentialless),
                tone: snapshot.health.credentialless ? "ok" : "warn",
              },
              {
                label: "24/7 claim",
                value: yesNo(snapshot.health.twenty_four_seven),
              },
            ]}
          />
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Fail-closed bounds</h2>
            <p className="panel-kicker">This screen refuses LIVE, signing, and invented numbers</p>
          </div>
          <KvTable
            rows={[
              { label: "Trading mode", value: "PAPER", tone: "paper" },
              { label: "Browser signing", value: "never" },
              { label: "Keys in browser", value: "never" },
              { label: "Live mid used for PnL", value: "no" },
              { label: "Schema", value: snapshot.position.schema },
              { label: "Path contract", value: snapshot.position.path_contract },
              { label: "Source", value: sourceLabel(snapshot.source) },
            ]}
          />
          <p className="source">
            Source {snapshot.source}: <code>{snapshot.runDir}</code>
          </p>
        </section>
      </div>

      <PaperBlotter orders={snapshot.orders} fills={snapshot.fills} />

      <section className="limitations-panel">
        <h2>Limitations</h2>
        <ul className="limitations">
          {uniqueStrings([
            ...snapshot.position.limitations,
            ...snapshot.pnl.limitations,
            ...snapshot.health.limitations,
            ...snapshot.orders.limitations,
            ...snapshot.fills.limitations,
          ]).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
    </>
  );
}

export default function FirstPaperScreen() {
  let snapshotError: string | undefined;
  let snapshot: PaperRunSnapshot | undefined;
  try {
    snapshot = loadPaperRunSnapshot();
  } catch (error: unknown) {
    snapshotError = error instanceof Error ? error.message : "PAPER run data is unavailable.";
  }

  return (
    <div className="terminal">
      <div className="watermark" aria-hidden="true">
        PAPER
      </div>
      <Masthead snapshot={snapshot} snapshotError={snapshotError} />
      <main>
        {snapshotError !== undefined || snapshot === undefined ? (
          <>
            <section className="metrics" aria-label="Public market data">
              <LiveBtcPrice />
              <article className="metric">
                <h2>Paper position</h2>
                <p className="metric-value tone-warn">UNAVAILABLE</p>
                <p className="metric-note error">
                  {snapshotError ?? "PAPER run data is unavailable."}
                </p>
              </article>
              <article className="metric">
                <h2>Assumed overlay PnL</h2>
                <p className="metric-value tone-warn">UNAVAILABLE</p>
                <p className="metric-note">
                  Numbers are not invented when reconstructable JSON is missing or not PAPER.
                </p>
              </article>
              <article className="metric">
                <h2>Capture health</h2>
                <p className="metric-value tone-warn">UNAVAILABLE</p>
                <p className="metric-note">Fail closed. Zeros are not fabricated.</p>
              </article>
            </section>
            <section className="panel">
              <div className="panel-head">
                <h2>PAPER run</h2>
                <p className="panel-kicker">Missing or non-PAPER JSON fails the desk</p>
              </div>
              <p className="error">{snapshotError ?? "PAPER run data is unavailable."}</p>
            </section>
          </>
        ) : (
          <SnapshotDesk snapshot={snapshot} />
        )}
      </main>
      <footer className="statusbar">
        <span>PAPER ONLY</span>
        <span>NO WALLET SIGNING</span>
        <span>NO LIVE CAPITAL</span>
        <span>PUBLIC MID != PAPER PNL</span>
      </footer>
    </div>
  );
}
