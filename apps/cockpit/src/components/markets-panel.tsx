"use client";

import { PublicCloseSparkline } from "./kpi-sparkline";
import { MidChart } from "./mid-chart";
import { formatGroupedNumber } from "../lib/display";
import {
  MARKET_QUOTE_UNAVAILABLE,
  marketsRowsFromBoundVenues,
  soakMarkRow,
  type MarketRow,
} from "../lib/markets";
import type { VenueCaptureQuery } from "../lib/paths";
import type { PaperPnl, VenueCaptureStripResponse } from "../lib/types";
import { usePublicBtcPerp } from "../lib/use-public-price";
import { useVenueCapturePoll } from "../lib/use-venue-capture";

function quoteDisplay(row: MarketRow): string {
  if (row.quoteKind === "public-mid" || row.quoteKind === "soak-mark") {
    return formatGroupedNumber(row.quote);
  }
  return row.quote;
}

function MarketQuote({ row }: { row: MarketRow }) {
  const unavailable = row.quote === MARKET_QUOTE_UNAVAILABLE || row.quoteKind === "unavailable";
  return (
    <span className={unavailable ? "tone-warn" : undefined}>
      {row.quoteKind === "public-mid" && row.quote !== MARKET_QUOTE_UNAVAILABLE ? (
        <span className="live-dot" aria-hidden="true" />
      ) : null}
      {quoteDisplay(row)}
    </span>
  );
}

export function MarketsPanel({
  query,
  initial,
  soakPnl,
}: {
  query: VenueCaptureQuery;
  initial: VenueCaptureStripResponse;
  soakPnl: PaperPnl | undefined;
}) {
  const mid = usePublicBtcPerp();
  const strip = useVenueCapturePoll(query, initial);
  const rows = strip.ok ? marketsRowsFromBoundVenues(strip.strip.venues, mid) : [];
  const soak = soakMarkRow(soakPnl);

  return (
    <section className="markets-panel" aria-label="MARKETS">
      <div className="markets-head">
        <h2>MARKETS</h2>
        <p className="panel-kicker">
          Bound HL BTC-PERP public mid + public candleSnapshot · public mid ≠ research truth ·
          sibling last/BBO fail closed · not Paper PnL
        </p>
      </div>
      {strip.ok ? (
        <div className="markets-scroll">
          <table className="markets-table">
            <thead>
              <tr>
                <th>Venue</th>
                <th>Product</th>
                <th className="num">Last / mid</th>
                <th>Source</th>
                <th>Capture</th>
                <th className="num">Age</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={`tone-${row.tone}`}>
                  <td className="venue-strip-chip">{row.venue}</td>
                  <td>{row.product}</td>
                  <td className="markets-quote num">
                    <MarketQuote row={row} />
                  </td>
                  <td>{row.quoteSource}</td>
                  <td>
                    {row.live ? <span className="live-dot" aria-hidden="true" /> : null}
                    {row.captureStatus ?? "n/a"}
                  </td>
                  <td className="num">{row.lastPartAge}</td>
                  <td className="venue-strip-run mono-id">{row.runId}</td>
                </tr>
              ))}
              {soak !== undefined ? (
                <tr className="markets-soak tone-warn">
                  <td className="venue-strip-chip">{soak.venue}</td>
                  <td>{soak.product}</td>
                  <td className="markets-quote num">{quoteDisplay(soak)}</td>
                  <td>{soak.quoteSource}</td>
                  <td>n/a</td>
                  <td className="num">n/a</td>
                  <td>n/a</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="error">{strip.error}</p>
      )}
      <PublicCloseSparkline />
      <MidChart />
    </section>
  );
}
