"use client";

import { useState } from "react";

import { StoredMarketData } from "./stored-market-data";
import { MidChart } from "../mid-chart";
import { Badge } from "../ui/badge";
import { OriginBadge } from "../ui/origin-badge";
import { Card, CardBody, CardDisclosure, CardHeader } from "../ui/card";
import { DataTable, dataTableColumnHelper, type DataTableColumns } from "../ui/data-table";
import { Notice } from "../ui/notice";
import { PanelBoundary } from "../ui/panel-boundary";
import { ReadStatus } from "../ui/read-status";
import { Stat } from "../ui/stat";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { describeOriginSummary, stripOrigin } from "../../lib/data-origin";
import { captureChipDataState, dataStateTone } from "../../lib/data-state";
import { formatGroupedNumber } from "../../lib/display";
import { updatedAgoLabel } from "../../lib/poll-state";
import { localClockLabel } from "../../lib/time-display";
import { useNow } from "../../lib/use-now";
import {
  MARKET_QUOTE_UNAVAILABLE,
  applyStoredTapeQuote,
  marketsRowsFromBoundVenues,
  soakMarkRow,
  type MarketQuoteState,
  type MarketRow,
} from "../../lib/markets";
import type { MarketTapeResponse } from "../../lib/market-tape-types";
import type { VenueCaptureQuery } from "../../lib/paths";
import {
  PUBLIC_CANDLE_INTERVALS,
  PUBLIC_CANDLE_INTERVAL,
  type PublicCandleInterval,
} from "../../lib/public-candles";
import type { PaperPnl, VenueCaptureStripResponse } from "../../lib/types";
import { useMarketTape } from "../../lib/use-market-tape";
import { usePublicBtcPerp } from "../../lib/use-public-price";
import { useVenueCapturePoll } from "../../lib/use-venue-capture";
import { newestPartMtime } from "../../lib/venue-capture-poll";

const helper = dataTableColumnHelper<MarketRow>();

const QUOTE_STATE_LABEL: Record<MarketQuoteState, string> = {
  ok: "FRESH",
  stale: "STALE",
  unavailable: "UNAVAILABLE",
};

function quoteStateTone(state: MarketQuoteState): "ok" | "warn" | "down" {
  switch (state) {
    case "ok":
      return "ok";
    case "stale":
      return "warn";
    case "unavailable":
      return "down";
    default: {
      const exhaustive: never = state;
      throw new Error(`Unhandled quote state: ${String(exhaustive)}`);
    }
  }
}

function Detail({
  children,
  title,
  nowrap = false,
}: {
  children: string;
  title?: string;
  nowrap?: boolean;
}) {
  return (
    <span className={nowrap ? "quote-detail quote-detail-nowrap" : "quote-detail"} title={title}>
      {children}
    </span>
  );
}

function QuoteWhen({ row }: { row: MarketRow }) {
  const nowIso = useNow();
  const clock = localClockLabel(row.quoteAt);
  const ago =
    row.quoteAt === undefined
      ? `age ${row.quoteAge}`
      : updatedAgoLabel(row.quoteAt, nowIso, `updated ${row.quoteAge} ago`);
  return (
    <>
      <span className="quote-secondary" title={row.quoteAt === undefined ? undefined : clock}>
        {clock}
      </span>
      <Detail nowrap>{ago}</Detail>
      <Detail nowrap>{`part ${row.lastPartAge}`}</Detail>
    </>
  );
}

function QuoteCell({ value, reason }: { value: string; reason: string | undefined }) {
  if (value === MARKET_QUOTE_UNAVAILABLE) {
    return (
      <span className="quote-primary tone-warn" title={reason}>
        UNAVAILABLE
      </span>
    );
  }
  if (value === "—") {
    return <span className="quote-primary tone-muted">…</span>;
  }
  return <span className="quote-primary">{formatGroupedNumber(value)}</span>;
}

const columns: DataTableColumns<MarketRow> = helper.columns([
  helper.accessor("venue", { header: "Venue" }),
  helper.accessor("instrument", {
    header: "Instrument",
    cell: ({ row }) => (
      <>
        <span className="mono quote-secondary">{row.original.instrument}</span>
        {row.original.instrument === row.original.product ? null : (
          <Detail nowrap>{`contract ${row.original.product}`}</Detail>
        )}
      </>
    ),
  }),
  helper.accessor("last", {
    header: "Last",
    cell: ({ row }) => <QuoteCell value={row.original.last} reason={row.original.quoteReason} />,
  }),
  helper.accessor("mid", {
    header: "Mid",
    cell: ({ row }) => <QuoteCell value={row.original.mid} reason={row.original.quoteReason} />,
  }),
  helper.accessor("quoteAge", {
    header: "When",
    cell: ({ row }) => <QuoteWhen row={row.original} />,
  }),
  helper.accessor("quoteState", {
    header: "Status",
    cell: ({ row }) => (
      <>
        <span className="row" style={{ gap: "0.3rem" }}>
          <Badge
            tone={quoteStateTone(row.original.quoteState)}
            dot
            live={row.original.quoteState === "ok" && row.original.live}
            title={row.original.quoteReason}
          >
            {QUOTE_STATE_LABEL[row.original.quoteState]}
          </Badge>
          {row.original.captureStatus === undefined ? null : (
            <Badge
              tone={dataStateTone(captureChipDataState(row.original.captureStatus))}
              title={`Capture health chip for run ${row.original.runId}; the quote chip on the left is about the stored tick itself.`}
            >
              {`capture ${row.original.captureStatus}`}
            </Badge>
          )}
        </span>
        {row.original.quoteReason === undefined ? null : (
          <Detail>{row.original.quoteReason}</Detail>
        )}
        <Detail title={row.original.quoteSource}>{row.original.quoteSource}</Detail>
      </>
    ),
  }),
]) as DataTableColumns<MarketRow>;

export function MarketsScreen({
  query,
  initialStrip,
  initialTape,
  soakPnl,
}: {
  query: VenueCaptureQuery;
  initialStrip: VenueCaptureStripResponse;
  initialTape: MarketTapeResponse;
  soakPnl: PaperPnl | undefined;
}) {
  const { token } = useCockpitRefresh();
  const [interval, setInterval] = useState<PublicCandleInterval>(PUBLIC_CANDLE_INTERVAL);
  const stripPoll = useVenueCapturePoll(query, initialStrip, token);
  const tapePoll = useMarketTape(query, initialTape, token);
  const strip = stripPoll.data;
  const mid = usePublicBtcPerp(token);
  const origin = stripOrigin(strip);

  const freshMaxS = strip.ok ? strip.strip.fresh_max_s : 180;
  const rows = strip.ok
    ? applyStoredTapeQuote(marketsRowsFromBoundVenues(strip.strip.venues), tapePoll.data, freshMaxS)
    : [];
  const soak = soakMarkRow(soakPnl);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Markets</h1>
          <p>
            Venue last and mid come from the stored capture of each bound run (HL, Binance, Bitvavo,
            Bitvavo Standard, Kraken). A missing trade or BBO stays UNAVAILABLE. The chart above
            that table is separate public Hyperliquid context and is not a venue mid. Neither is
            research truth or PAPER PnL.
          </p>
        </div>
        <div className="page-head-actions">
          <OriginBadge
            origin={origin.origin}
            detail={describeOriginSummary(origin)}
            live={!stripPoll.degraded}
          />
          <ReadStatus
            state={stripPoll}
            sourceLabel="newest part"
            sourceIso={newestPartMtime(strip)}
          />
        </div>
      </div>

      <div className="grid grid-sm-2 grid-lg-4">
        <Stat
          label="Public HL context"
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
              ? `${mid.price.source} · not a capture mid`
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
          value={soak === undefined ? "—" : formatGroupedNumber(soak.mid)}
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
          <PanelBoundary title="Public candle chart unavailable">
            <MidChart interval={interval} refreshToken={token} />
          </PanelBoundary>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Bound venues · last / mid"
          description="One row per bound capture contract, including Hyperliquid. Last is the newest stored trade. Mid is the stored best bid/offer midpoint. Green is inside the freshness bound, amber is stale, red means no usable tick. A down or gapped venue stays UNAVAILABLE and does not clear the others. Prices are never invented."
        />
        <CardBody flush>
          {strip.ok ? (
            <DataTable
              columns={columns}
              data={rows}
              numericColumns={["last", "mid", "quoteAge"]}
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
              <span className="mono">{formatGroupedNumber(soak.mid)}</span> — {soak.quoteSource}. It
              is a copied soak mark for {soak.product}, deliberately not blended with live public
              quotes.
            </p>
          </CardDisclosure>
        )}
      </Card>

      <PanelBoundary title="Stored market data failed to render">
        <StoredMarketData tapePoll={tapePoll} strip={strip} />
      </PanelBoundary>
    </div>
  );
}
