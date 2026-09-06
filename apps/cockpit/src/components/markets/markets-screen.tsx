"use client";

import { useState } from "react";

import { MidChart } from "../mid-chart";
import { Badge } from "../ui/badge";
import { Card, CardBody, CardDisclosure, CardHeader } from "../ui/card";
import { DataTable, dataTableColumnHelper, type DataTableColumns } from "../ui/data-table";
import { Notice } from "../ui/notice";
import { ReadStatus } from "../ui/read-status";
import { Stat } from "../ui/stat";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { captureChipDataState, dataStateTone } from "../../lib/data-state";
import { formatGroupedNumber } from "../../lib/display";
import {
  MARKET_QUOTE_UNAVAILABLE,
  marketsRowsFromBoundVenues,
  soakMarkRow,
  type MarketRow,
} from "../../lib/markets";
import type { VenueCaptureQuery } from "../../lib/paths";
import {
  PUBLIC_CANDLE_INTERVALS,
  PUBLIC_CANDLE_INTERVAL,
  type PublicCandleInterval,
} from "../../lib/public-candles";
import type { PaperPnl, VenueCaptureStripResponse } from "../../lib/types";
import { usePublicBtcPerp } from "../../lib/use-public-price";
import { useVenueCapturePoll } from "../../lib/use-venue-capture";
import { newestPartMtime } from "../../lib/venue-capture-poll";

const helper = dataTableColumnHelper<MarketRow>();

const columns: DataTableColumns<MarketRow> = helper.columns([
  helper.accessor("venue", { header: "Venue" }),
  helper.accessor("product", { header: "Instrument" }),
  helper.accessor("quote", {
    header: "Last / mid",
    cell: ({ row }) =>
      row.original.quote === MARKET_QUOTE_UNAVAILABLE ? (
        <span className="tone-muted">UNAVAILABLE</span>
      ) : (
        formatGroupedNumber(row.original.quote)
      ),
  }),
  helper.accessor("quoteSource", { header: "Source", enableSorting: false }),
  helper.accessor("captureStatus", {
    header: "Capture",
    cell: ({ row }) =>
      row.original.captureStatus === undefined ? (
        <span className="tone-muted">n/a</span>
      ) : (
        <Badge
          tone={dataStateTone(captureChipDataState(row.original.captureStatus))}
          dot
          live={row.original.live}
        >
          {row.original.captureStatus}
        </Badge>
      ),
  }),
  helper.accessor("lastPartAge", { header: "Age" }),
  helper.accessor("runId", { header: "Run" }),
]) as DataTableColumns<MarketRow>;

export function MarketsScreen({
  query,
  initialStrip,
  soakPnl,
}: {
  query: VenueCaptureQuery;
  initialStrip: VenueCaptureStripResponse;
  soakPnl: PaperPnl | undefined;
}) {
  const { token } = useCockpitRefresh();
  const [interval, setInterval] = useState<PublicCandleInterval>(PUBLIC_CANDLE_INTERVAL);
  const stripPoll = useVenueCapturePoll(query, initialStrip, token);
  const strip = stripPoll.data;
  const mid = usePublicBtcPerp(token);

  const rows = strip.ok ? marketsRowsFromBoundVenues(strip.strip.venues, mid) : [];
  const soak = soakMarkRow(soakPnl);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Markets</h1>
          <p>
            Public Hyperliquid market context from the credentialless{" "}
            <span className="mono">/info</span> route. Sibling venue last/BBO are not in the cockpit
            APIs and stay UNAVAILABLE rather than being invented. Public mid is context, never
            research truth and never PAPER PnL.
          </p>
        </div>
        <div className="page-head-actions">
          <ReadStatus
            state={stripPoll}
            sourceLabel="newest part"
            sourceIso={newestPartMtime(strip)}
          />
        </div>
      </div>

      <div className="grid grid-sm-2 grid-lg-4">
        <Stat
          label="BTC-PERP mid"
          value={
            mid.status === "ready"
              ? formatGroupedNumber(mid.price.mid)
              : mid.status === "loading"
                ? "…"
                : "—"
          }
          tone={mid.status === "ready" ? "neutral" : "muted"}
          meta={
            mid.status === "ready"
              ? `${mid.price.source} · unsigned`
              : mid.status === "error"
                ? mid.message
                : "Fetching public /info allMids…"
          }
        />
        <Stat
          label="Candle interval"
          value={interval}
          compact
          meta="Public candleSnapshot. Changing the interval refits once; refreshes keep your zoom."
        />
        <Stat
          label="Bound venues"
          value={strip.ok ? String(strip.strip.venues.length) : "—"}
          compact
          meta={
            strip.ok
              ? `${String(strip.strip.venues.filter((venue) => venue.live).length)} live · fresh ≤ ${String(strip.strip.fresh_max_s)}s`
              : strip.error
          }
        />
        <Stat
          label="Soak mark"
          value={soak === undefined ? "—" : formatGroupedNumber(soak.quote)}
          compact
          tone="warn"
          meta={
            soak === undefined
              ? "No COURSE-1 paper-pnl.json bound."
              : "paper-pnl.json mark_price · soak mark, not a live last"
          }
        />
      </div>

      <Card>
        <CardHeader
          title="HL BTC-PERP candles"
          description="Public candleSnapshot. Scroll to pan, wheel or pinch to zoom — a refresh restores the range you were looking at."
          actions={
            <div className="seg" role="group" aria-label="Candle interval">
              {PUBLIC_CANDLE_INTERVALS.map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={interval === option}
                  onClick={() => {
                    setInterval(option);
                  }}
                >
                  {option}
                </button>
              ))}
            </div>
          }
        />
        <CardBody>
          <MidChart interval={interval} refreshToken={token} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Bound instruments"
          description="Sort any column. Quotes only appear where a public cockpit API actually provides them."
        />
        <CardBody flush>
          {strip.ok ? (
            <DataTable
              columns={columns}
              data={rows}
              numericColumns={["quote", "lastPartAge"]}
              monoColumns={["runId"]}
              emptyLabel="No bound venues."
            />
          ) : (
            <div style={{ padding: "0.85rem" }}>
              <Notice state="error" title="Capture strip unavailable">
                {strip.error}
              </Notice>
            </div>
          )}
        </CardBody>
        {soak === undefined ? null : (
          <CardDisclosure summary="COURSE-1 soak mark (kept out of the market table)">
            <p style={{ margin: 0 }}>
              <span className="mono">{formatGroupedNumber(soak.quote)}</span> — {soak.quoteSource}.
              It is a copied soak mark for {soak.product}, deliberately not blended with live public
              quotes.
            </p>
          </CardDisclosure>
        )}
      </Card>
    </div>
  );
}
