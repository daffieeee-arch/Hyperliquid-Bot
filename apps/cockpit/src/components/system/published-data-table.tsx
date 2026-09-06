"use client";

import { Badge } from "../ui/badge";
import { DataTable, dataTableColumnHelper, type DataTableColumns } from "../ui/data-table";
import { Notice } from "../ui/notice";
import { OriginBadge } from "../ui/origin-badge";
import { captureChipDataState, dataStateTone } from "../../lib/data-state";
import { presentCopiedText } from "../../lib/display";
import {
  runIdDisagrees,
  venueTapeSummaries,
  type VenueTapeSummary,
} from "../../lib/market-tape-rows";
import type { MarketTapeResponse } from "../../lib/market-tape-types";
import type { VenueCaptureStripResponse } from "../../lib/types";

type PublishedRow = {
  key: string;
  venue: string;
  summary: VenueTapeSummary;
  runId: string;
  parts: number;
  volume: string;
  newestPart: string;
  lastData: string;
  lastDataAge: string;
  publicationLag: string;
};

const helper = dataTableColumnHelper<PublishedRow>();

const columns: DataTableColumns<PublishedRow> = helper.columns([
  helper.accessor("venue", { header: "Venue" }),
  helper.accessor("summary", {
    header: "Origin",
    enableSorting: false,
    cell: ({ row }) => (
      <span className="row" style={{ gap: "0.3rem" }}>
        <OriginBadge origin={row.original.summary.origin} live={row.original.summary.chip?.live} />
        {row.original.summary.chip === undefined ? null : (
          <Badge
            tone={dataStateTone(captureChipDataState(row.original.summary.chip.status))}
            dot
            live={row.original.summary.chip.live}
            title={row.original.summary.chip.status_detail}
          >
            {row.original.summary.chip.status}
          </Badge>
        )}
      </span>
    ),
  }),
  helper.accessor("runId", {
    header: "Run",
    cell: ({ row }) => (
      <span
        className={runIdDisagrees(row.original.summary) ? "tone-down" : undefined}
        title={
          runIdDisagrees(row.original.summary)
            ? `Capture strip is bound to ${row.original.summary.chip?.run_id ?? "n/a"}`
            : row.original.runId
        }
      >
        {row.original.runId}
      </span>
    ),
  }),
  helper.accessor("parts", { header: "Parts" }),
  helper.accessor("volume", { header: "Volume" }),
  helper.accessor("newestPart", { header: "Newest part" }),
  helper.accessor("lastData", { header: "Last data" }),
  helper.accessor("lastDataAge", {
    header: "Age",
    cell: ({ row }) => (
      <span className={`tone-${dataStateTone(row.original.summary.dataState)}`}>
        {row.original.lastDataAge}
      </span>
    ),
  }),
  helper.accessor("publicationLag", {
    header: "Publish lag",
    cell: ({ row }) => (
      <span className={row.original.summary.publicationLagExceeded ? "tone-warn" : undefined}>
        {row.original.publicationLag}
      </span>
    ),
  }),
]) as DataTableColumns<PublishedRow>;

/**
 * Per-venue publication facts: how many parts exist, how large they are, when
 * the newest was published and how far behind the events it was. Missing runs
 * appear with dashes, never zeros.
 */
export function PublishedDataTable({
  tape,
  strip,
}: {
  tape: MarketTapeResponse;
  strip: VenueCaptureStripResponse;
}) {
  if (!tape.ok) {
    return (
      <div style={{ padding: "0.85rem" }}>
        <Notice state="error" title="Published data unavailable">
          {tape.error}
        </Notice>
      </div>
    );
  }
  const rows: PublishedRow[] = venueTapeSummaries(tape, strip).map((summary) => ({
    key: summary.tape.id,
    venue: `${summary.tape.chip} · ${summary.tape.series}`,
    summary,
    runId: presentCopiedText(summary.tape.runId),
    parts: summary.tape.parts.published,
    volume: summary.tape.status === "ok" ? summary.volume : "—",
    newestPart: summary.tape.status === "ok" ? summary.newestPart : "—",
    lastData: summary.tape.status === "ok" ? summary.lastData : "—",
    lastDataAge: summary.tape.status === "ok" ? summary.lastDataAge : "—",
    publicationLag: summary.tape.status === "ok" ? summary.publicationLag : "—",
  }));
  return (
    <DataTable
      columns={columns}
      data={rows}
      numericColumns={["parts", "volume", "lastDataAge", "publicationLag"]}
      monoColumns={["runId", "newestPart", "lastData"]}
      emptyLabel="No venue bound."
      nowrap
    />
  );
}
