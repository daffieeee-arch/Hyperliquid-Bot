"use client";

import { Badge } from "../ui/badge";
import { Card, CardBody, CardDisclosure, CardHeader } from "../ui/card";
import { DataTable, dataTableColumnHelper, type DataTableColumns } from "../ui/data-table";
import { KvList, type KvRow } from "../ui/kv";
import { Notice } from "../ui/notice";
import { OriginBadge } from "../ui/origin-badge";
import { ReadStatus } from "../ui/read-status";
import { captureChipDataState, dataStateMeta, dataStateTone } from "../../lib/data-state";
import { formatGroupedNumber, presentCopiedText } from "../../lib/display";
import {
  MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S,
  type MarketTapeResponse,
  type TapeTrade,
} from "../../lib/market-tape-types";
import {
  instrumentRows,
  newestTapeEvent,
  runIdDisagrees,
  venueTapeSummaries,
  type InstrumentRow,
  type VenueTapeSummary,
} from "../../lib/market-tape-rows";
import type { PollState } from "../../lib/poll-state";
import { localClockLabel } from "../../lib/time-display";
import type { VenueCaptureStripResponse } from "../../lib/types";

const helper = dataTableColumnHelper<InstrumentRow>();

const KIND_LABEL: Record<InstrumentRow["kind"], string> = {
  perpetual: "PERP",
  spot: "SPOT",
  unknown: "?",
};

function sideTone(side: InstrumentRow["lastSide"]): "up" | "down" | "muted" {
  switch (side) {
    case "buy":
      return "up";
    case "sell":
      return "down";
    case "unknown":
    case "—":
      return "muted";
    default: {
      const exhaustive: never = side;
      throw new Error(`Unhandled trade side: ${String(exhaustive)}`);
    }
  }
}

function Detail({ children }: { children: string }) {
  return (
    <span className="eyebrow" style={{ display: "block", marginTop: "0.15rem" }}>
      {children}
    </span>
  );
}

const columns: DataTableColumns<InstrumentRow> = helper.columns([
  helper.accessor("product", {
    header: "Instrument",
    cell: ({ row }) => (
      <span className="row" style={{ gap: "0.4rem" }}>
        <span className="mono">{row.original.product}</span>
        <Badge tone={row.original.kind === "perpetual" ? "info" : "muted"}>
          {KIND_LABEL[row.original.kind]}
        </Badge>
      </span>
    ),
  }),
  helper.accessor("quote", { header: "Quote" }),
  helper.accessor("lastPrice", {
    header: "Last trade",
    cell: ({ row }) => (
      <>
        <span className={`quote-primary tone-${sideTone(row.original.lastSide)}`}>
          {formatGroupedNumber(row.original.lastPrice)}
        </span>
        {row.original.lastPrice === "—" ? null : (
          <Detail>{`${row.original.lastSide} ${row.original.lastSize} · ${row.original.lastTradeAt}`}</Detail>
        )}
      </>
    ),
  }),
  helper.accessor("bid", {
    header: "Bid / ask",
    cell: ({ row }) => (
      <>
        {row.original.bid === "—" ? (
          <span className="tone-muted">—</span>
        ) : (
          <span className="quote-secondary mono">
            {formatGroupedNumber(row.original.bid)} / {formatGroupedNumber(row.original.ask)}
          </span>
        )}
        {row.original.bid === "—" ? null : (
          <Detail>{`spread ${row.original.spread} · ${row.original.spreadBps} bps`}</Detail>
        )}
      </>
    ),
  }),
  helper.accessor("trades", { header: "Trades" }),
  helper.accessor("lastEventAge", { header: "Data age" }),
]) as DataTableColumns<InstrumentRow>;

function RecentTrades({ trades, quote }: { trades: TapeTrade[]; quote: string }) {
  if (trades.length === 0) {
    return <p style={{ margin: 0 }}>No trade decoded from the processed parts yet.</p>;
  }
  return (
    <table className="dt">
      <thead>
        <tr>
          <th>Time</th>
          <th>Side</th>
          <th className="num">Price ({quote})</th>
          <th className="num">Size</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((trade, index) => (
          <tr key={`${trade.at}-${String(index)}`}>
            <td className="mono" title={trade.at}>
              {localClockLabel(trade.at)}
            </td>
            <td className={`tone-${sideTone(trade.side)}`}>{trade.side}</td>
            <td className="num mono">{formatGroupedNumber(trade.price)}</td>
            <td className="num mono">{trade.size}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function VenueTapeCard({ summary, degraded }: { summary: VenueTapeSummary; degraded: boolean }) {
  const { tape, chip } = summary;
  const observedAt = tape.observedAt;
  const rows = instrumentRows(tape, observedAt);
  const disagrees = runIdDisagrees(summary);
  const stateMeta = dataStateMeta(summary.dataState);

  const kv: KvRow[] = [
    {
      label: "Run",
      value: <span className="mono cell-id">{presentCopiedText(tape.runId)}</span>,
      detail: `bound via ${tape.bindingSource}`,
      title: tape.runId,
    },
    {
      label: "Published",
      value: `${summary.published} · ${summary.volume}`,
      detail: summary.processed,
    },
    {
      label: "Newest part",
      value: summary.newestPart,
      detail: tape.parts.newestPartName,
      title: tape.parts.newestPartMtimeUtc,
    },
    {
      label: "Last data",
      value: `${summary.lastData} · ${summary.lastDataAge} ago`,
      tone:
        dataStateTone(summary.dataState) === "muted" ? "unknown" : dataStateTone(summary.dataState),
      detail: "newest receive time inside the processed parts",
      title: tape.lastEventUtc,
    },
    {
      label: "Publication lag",
      value: summary.publicationLag,
      tone: summary.publicationLagExceeded ? "warn" : undefined,
      detail: `writer publishes after ≤${String(MARKET_TAPE_EXPECTED_PUBLICATION_LAG_S)}s or 5 000 records`,
    },
  ];

  return (
    <Card style={{ opacity: degraded ? 0.78 : undefined }}>
      <CardHeader
        title={`${tape.chip} · ${tape.series}`}
        description={`${tape.venue} ${tape.contractProduct} — stored parts only, read by the backend.`}
        actions={
          <>
            <OriginBadge origin={summary.origin} live={chip?.live === true && !degraded} />
            {chip === undefined ? null : (
              <Badge
                tone={dataStateTone(captureChipDataState(chip.status))}
                dot
                live={chip.live && !degraded}
                title={chip.status_detail}
              >
                {chip.status}
              </Badge>
            )}
          </>
        }
      />
      <CardBody>
        {tape.status === "ok" ? (
          <div className="grid grid-lg-facts">
            <KvList rows={kv} />
            <div className="stack-sm">
              {disagrees ? (
                <Notice state="error" title="Run ids disagree">
                  Capture health is bound to <span className="mono">{chip?.run_id}</span> but stored
                  data was read from <span className="mono">{tape.runId}</span>. Re-check the run
                  picker before trusting either.
                </Notice>
              ) : null}
              {summary.dataState === "stale" ? (
                <Notice state="stale" title={stateMeta.label}>
                  Newest stored event is {summary.lastDataAge} old while the capture reports live.
                  Either the writer has not rotated a part yet or the feed is quiet.
                </Notice>
              ) : null}
              {rows.length === 0 ? (
                <Notice state="pending" title="No instrument decoded yet">
                  {tape.note}
                </Notice>
              ) : (
                <DataTable
                  columns={columns}
                  data={rows}
                  numericColumns={["lastPrice", "bid", "trades", "lastEventAge"]}
                  emptyLabel="No instrument decoded."
                  nowrap
                />
              )}
            </div>
          </div>
        ) : (
          <Notice
            state={tape.status === "missing" ? "missing" : "error"}
            title={tape.status === "missing" ? "No stored data bound" : "Stored data unreadable"}
          >
            {tape.error}
          </Notice>
        )}
      </CardBody>
      {tape.status === "ok" && tape.instruments.some((item) => item.recentTrades.length > 0) ? (
        <CardDisclosure summary="Recent trades per instrument">
          <div className="stack-sm">
            {tape.instruments.map((instrument) => (
              <div key={instrument.product}>
                <div className="eyebrow" style={{ marginBottom: "0.3rem" }}>
                  {instrument.product} · {KIND_LABEL[instrument.kind]} · channels{" "}
                  {instrument.channelsSeen.join(", ") || "none"}
                </div>
                <RecentTrades trades={instrument.recentTrades} quote={instrument.quote} />
              </div>
            ))}
          </div>
        </CardDisclosure>
      ) : (
        <CardDisclosure summary="Why values may lag the collector">
          <p style={{ margin: 0 }}>{tape.note}</p>
        </CardDisclosure>
      )}
    </Card>
  );
}

/**
 * Stored market data per venue: what the collectors actually wrote, decoded
 * from published Parquet parts. Nothing here is fetched from a venue API.
 */
export function StoredMarketData({
  tapePoll,
  strip,
}: {
  tapePoll: PollState<MarketTapeResponse>;
  strip: VenueCaptureStripResponse;
}) {
  const tape = tapePoll.data;
  const summaries = venueTapeSummaries(tape, strip);

  return (
    <section className="stack">
      <div className="page-head" style={{ marginTop: "0.25rem" }}>
        <div>
          <h2 style={{ margin: 0 }}>Stored market data</h2>
          <p>
            Last trade, best bid/offer, spread and recent trades decoded from the published{" "}
            <span className="mono">raw/part-*.parquet</span> files of each bound run. Spot,
            perpetual and quote currency stay separate rows. Only parts published since the previous
            read are parsed.
          </p>
        </div>
        <div className="page-head-actions">
          <ReadStatus state={tapePoll} sourceLabel="last event" sourceIso={newestTapeEvent(tape)} />
          {tape.ok ? (
            <Badge
              tone="muted"
              title="Runs held in the backend cache and parts parsed by the most recent read"
            >
              cache {String(tape.tape.cache.runsCached)} runs · parsed{" "}
              {String(tape.tape.cache.partsParsedThisCall)}
            </Badge>
          ) : null}
        </div>
      </div>

      {tape.ok ? (
        <div className="stack">
          {summaries.map((summary) => (
            <VenueTapeCard key={summary.tape.id} summary={summary} degraded={tapePoll.degraded} />
          ))}
        </div>
      ) : (
        <Notice state="error" title="Stored market data unavailable">
          {tape.error}
        </Notice>
      )}
    </section>
  );
}
