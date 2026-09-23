"use client";

import { useMemo, useState } from "react";

import { PublishedDataTable } from "./published-data-table";
import { RunPicker } from "../run-picker";
import { VenueStrip } from "../venue-strip";
import { Badge } from "../ui/badge";
import { Card, CardBody, CardDisclosure, CardHeader } from "../ui/card";
import { DataTable, dataTableColumnHelper, type DataTableColumns } from "../ui/data-table";
import { KvList } from "../ui/kv";
import { Notice } from "../ui/notice";
import { OriginBadge } from "../ui/origin-badge";
import { ReadStatus } from "../ui/read-status";
import { Stat } from "../ui/stat";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { describeOriginSummary, stripOrigin } from "../../lib/data-origin";
import {
  captureChipDataState,
  dataStateMeta,
  dataStateTone,
  worstDataState,
} from "../../lib/data-state";
import { deskPhaseAProgressLine } from "../../lib/desk";
import {
  data1aCaptureHealthPresentation,
  presentCopiedNumber,
  presentData1ADuration,
  presentGapReconnectClusters,
} from "../../lib/display";
import { newestTapeEvent } from "../../lib/market-tape-rows";
import type { MarketTapeResponse } from "../../lib/market-tape-types";
import type { VenueCaptureQuery } from "../../lib/paths";
import { PAPER_HARD_LIMIT_GATES } from "../../lib/paper-risk-gates";
import type { PaperRiskGate } from "../../lib/paper-risk-gates";
import { withLiveStripRisk, type RiskField, type RiskView } from "../../lib/risk";
import { withLiveStripSecondRow, type SecondRowView } from "../../lib/second-row";
import { updatedAgoLabel } from "../../lib/poll-state";
import { localClockLabel } from "../../lib/time-display";
import { useNow } from "../../lib/use-now";
import type { Data1ACaptureResponse, VenueCaptureStripResponse } from "../../lib/types";
import { useData1ACapturePoll } from "../../lib/use-data1a-capture";
import { useMarketTape } from "../../lib/use-market-tape";
import { useVenueCapturePoll } from "../../lib/use-venue-capture";
import { newestPartMtime } from "../../lib/venue-capture-poll";

const gateHelper = dataTableColumnHelper<PaperRiskGate>();
const riskHelper = dataTableColumnHelper<RiskField>();

const gateColumns: DataTableColumns<PaperRiskGate> = gateHelper.columns([
  gateHelper.accessor("id", { header: "Gate" }),
  gateHelper.accessor("label", { header: "Label" }),
  gateHelper.accessor("defaultCap", { header: "Documented cap" }),
  gateHelper.accessor("kind", {
    header: "Kind",
    cell: ({ row }) => (
      <Badge tone={row.original.kind === "halt" ? "down" : "outline"}>{row.original.kind}</Badge>
    ),
  }),
  gateHelper.accessor("onBreach", { header: "On breach", enableSorting: false }),
]) as DataTableColumns<PaperRiskGate>;

const riskColumns: DataTableColumns<RiskField> = riskHelper.columns([
  riskHelper.accessor("label", { header: "Field" }),
  riskHelper.accessor("value", {
    header: "Value",
    cell: ({ row }) =>
      row.original.kind === "unavailable" ? (
        <span className="tone-muted">{row.original.value}</span>
      ) : (
        <span className={row.original.tone === "warn" ? "tone-warn" : undefined}>
          {row.original.value}
        </span>
      ),
  }),
  riskHelper.accessor("kind", {
    header: "Kind",
    cell: ({ row }) => (
      <Badge
        tone={
          row.original.kind === "unavailable"
            ? "muted"
            : row.original.kind === "assumed"
              ? "warn"
              : "outline"
        }
      >
        {row.original.kind}
      </Badge>
    ),
  }),
  riskHelper.accessor("source", { header: "Source", enableSorting: false }),
]) as DataTableColumns<RiskField>;

export function SystemScreen({
  query,
  initialStrip,
  initialData1A,
  initialTape,
  secondRow,
  risk,
}: {
  query: VenueCaptureQuery;
  initialStrip: VenueCaptureStripResponse;
  initialData1A: Data1ACaptureResponse;
  initialTape: MarketTapeResponse;
  secondRow: SecondRowView;
  risk: RiskView;
}) {
  const { token, paused, setPaused, intervalMs } = useCockpitRefresh();
  const nowIso = useNow();
  const stripPoll = useVenueCapturePoll(query, initialStrip, token);
  const data1aPoll = useData1ACapturePoll(query.data1a_run_id, initialData1A, token);
  const tapePoll = useMarketTape(query, initialTape, token);
  const strip = stripPoll.data;
  const data1a = data1aPoll.data;
  const tape = tapePoll.data;
  const origin = stripOrigin(strip);
  const [riskFilter, setRiskFilter] = useState<"all" | "copied" | "unavailable">("all");

  const liveRisk = useMemo(() => withLiveStripRisk(risk, strip), [risk, strip]);
  const liveSecondRow = useMemo(() => withLiveStripSecondRow(secondRow, strip), [secondRow, strip]);
  const phaseALine = useMemo(() => deskPhaseAProgressLine(strip), [strip]);

  const riskRows = useMemo(() => {
    const all = [...liveRisk.copied, ...liveRisk.unavailable];
    if (riskFilter === "copied") {
      return all.filter((field) => field.kind !== "unavailable");
    }
    if (riskFilter === "unavailable") {
      return all.filter((field) => field.kind === "unavailable");
    }
    return all;
  }, [liveRisk.copied, liveRisk.unavailable, riskFilter]);

  const presentation = data1a.ok ? data1aCaptureHealthPresentation(data1a.snapshot) : undefined;
  const worstState = strip.ok
    ? worstDataState(strip.strip.venues.map((venue) => captureChipDataState(venue.status)))
    : "error";
  const lastPartIso = data1a.ok ? data1a.snapshot.parts.last_part_mtime_utc : undefined;
  const lastPartClock = localClockLabel(lastPartIso);
  const lastPartAgo = updatedAgoLabel(lastPartIso, nowIso, "");
  const lastPartAmsterdam =
    lastPartClock === "—"
      ? "n/a"
      : lastPartAgo === ""
        ? lastPartClock
        : `${lastPartClock} · ${lastPartAgo}`;
  const durationLine = data1a.ok ? presentData1ADuration(data1a.snapshot) : undefined;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>System &amp; risk</h1>
          <p>
            Read-only capture health, run binding and the documented{" "}
            <span className="mono">paper_risk</span> gates. Starting or stopping a collector from
            the browser is vetoed by contract, so this screen only reports.
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
          <label className="row" style={{ gap: "0.35rem" }}>
            <input
              type="checkbox"
              checked={paused}
              onChange={(event) => {
                setPaused(event.target.checked);
              }}
            />
            <span className="eyebrow">Pause {String(intervalMs / 1000)}s auto-refresh</span>
          </label>
        </div>
      </div>

      {stripPoll.error === undefined &&
      data1aPoll.error === undefined &&
      tapePoll.error === undefined ? null : (
        <Notice state="error" title="A refresh failed — values below are from an earlier read">
          {stripPoll.error ?? data1aPoll.error ?? tapePoll.error}
        </Notice>
      )}

      <div className="grid grid-sm-2 grid-lg-3">
        <Stat
          label="Phase A capture"
          value={
            strip.ok
              ? `${String(strip.strip.venues.filter((venue) => venue.live).length)}/${String(strip.strip.venues.length)}`
              : "—"
          }
          tone={dataStateTone(worstState)}
          compact
          meta={phaseALine}
        />
        <Stat
          label="Capture freshness"
          value={liveRisk.captureLine === "UNAVAILABLE" ? "—" : "live strip"}
          tone={stripPoll.degraded ? "warn" : "ok"}
          compact
          meta={liveSecondRow.capture.glanceLine}
        />
        <Stat
          label="DATA-1A duration"
          value={durationLine ?? "—"}
          compact
          meta={data1a.ok ? `last part ${lastPartAmsterdam}` : "Capture snapshot unavailable."}
        />
      </div>

      <Card>
        <CardHeader
          title="Bound capture runs"
          description="Auto-binding prefers the freshest live retain. Pick an explicit run to pin every workspace to it."
          actions={<ReadStatus state={stripPoll} compact />}
        />
        <CardBody>
          <VenueStrip strip={strip} degraded={stripPoll.degraded} />
          {strip.ok ? (
            <RunPicker query={query} catalog={strip.strip.catalog} venues={strip.strip.venues} />
          ) : null}
        </CardBody>
        <CardDisclosure summary="Run provenance">
          {strip.ok ? (
            <KvList
              rows={[
                ...strip.strip.provenance.rows.map((row) => ({
                  label: `${row.chip} ${row.series}`,
                  value: row.run_id,
                  detail: `binding: ${row.binding_source}${
                    row.started_at_utc === undefined
                      ? ""
                      : ` · started ${localClockLabel(row.started_at_utc)}`
                  }`,
                })),
                {
                  label: "Overlap",
                  value: strip.strip.provenance.overlap_note,
                  tone: "unknown" as const,
                },
              ]}
            />
          ) : (
            <p style={{ margin: 0 }}>{strip.error}</p>
          )}
        </CardDisclosure>
      </Card>

      <Card>
        <CardHeader
          title="Published data per venue"
          description="What each bound run has actually published: part count, volume, newest part and how far the newest part lagged its own events."
          actions={
            <ReadStatus
              state={tapePoll}
              sourceLabel="last event"
              sourceIso={newestTapeEvent(tape)}
            />
          }
        />
        <CardBody flush>
          <PublishedDataTable tape={tape} strip={strip} />
        </CardBody>
        <div className="card-foot">
          Publish lag is the writer holding data in memory before rotating a part (bound ≤60s or 5
          000 records). Age is measured from the newest event inside the published parts, so it
          includes that lag.
        </div>
      </Card>

      <div className="grid grid-sm-2 grid-lg-4">
        <Stat
          label="DATA-1A state"
          value={presentation?.tileLabel ?? "—"}
          icon={<ReadStatus state={data1aPoll} compact />}
          compact
          tone={
            presentation === undefined
              ? "muted"
              : presentation.tone === "ok"
                ? "ok"
                : presentation.tone === "down"
                  ? "down"
                  : "warn"
          }
          meta={presentation?.note ?? (data1a.ok ? "" : data1a.error)}
        />
        <Stat
          label="Parquet parts"
          value={data1a.ok ? presentCopiedNumber(data1a.snapshot.parts.count) : "—"}
          compact
          meta={data1a.ok ? `last ${lastPartAmsterdam}` : "Capture snapshot unavailable."}
        />
        <Stat
          label="Gaps · reconnects"
          value={
            data1a.ok
              ? presentGapReconnectClusters(
                  data1a.snapshot.health?.gaps,
                  data1a.snapshot.health?.reconnects,
                  data1a.snapshot.health?.reconnect_clusters,
                )
              : "—"
          }
          compact
          meta="Raw attempts and optional 5s wall-clock clusters are distinct. Written at stop."
        />
        <Stat
          label="Claim state"
          value={data1a.ok ? data1a.snapshot.claim.state : "—"}
          compact
          meta={data1a.ok ? `retained: ${data1a.snapshot.claim.retained ? "yes" : "no"}` : ""}
        />
      </div>

      <div className="grid grid-lg-2">
        <Card>
          <CardHeader title="D01 / paper_risk bind" description={liveSecondRow.bind.source} />
          <CardBody>
            <KvList
              rows={[
                { label: "Bound", value: liveSecondRow.bind.bound },
                { label: "Strategy class", value: liveSecondRow.bind.strategyClass },
                { label: "Same D01 smoke risk", value: liveSecondRow.bind.sameD01SmokeRisk },
                { label: "paper_risk catalog", value: liveSecondRow.bind.paperRiskCatalog },
              ]}
            />
            <p className="card-desc">{liveSecondRow.bind.note}</p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Binance usdm_public" description={liveSecondRow.binance.note} />
          <CardBody>
            <KvList
              rows={[
                { label: "Profile", value: liveSecondRow.binance.profile },
                { label: "Channel", value: liveSecondRow.binance.channel },
                { label: "run_id", value: liveSecondRow.binance.runId },
                { label: "Status", value: liveSecondRow.binance.status },
                { label: "Last part age", value: liveSecondRow.binance.lastPartAge },
                {
                  label: "Gaps · reconnects",
                  value: liveSecondRow.binance.gapsReconnects,
                },
                { label: "Binding", value: liveSecondRow.binance.binding },
              ]}
            />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Capture states explained"
          description="The cockpit keeps these four apart on purpose."
        />
        <CardBody flush>
          <div className="attention">
            {(["ok", "pending", "stale", "missing", "error"] as const).map((state) => {
              const meta = dataStateMeta(state);
              return (
                <div key={state} className="attention-row">
                  <Badge tone={meta.tone}>{meta.label}</Badge>
                  <span className="attention-copy">
                    <span>{meta.meaning}</span>
                  </span>
                  <span className="attention-side">
                    <span className="eyebrow">
                      {strip.ok
                        ? String(
                            strip.strip.venues.filter(
                              (venue) => captureChipDataState(venue.status) === state,
                            ).length,
                          )
                        : "—"}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        </CardBody>
        <div className="card-foot">
          {strip.ok ? (
            <>
              Worst state across {String(strip.strip.venues.length)} bound venues:{" "}
              <span className={`tone-${dataStateTone(worstState)}`}>
                {dataStateMeta(worstState).label}
              </span>{" "}
              — {dataStateMeta(worstState).meaning}
            </>
          ) : (
            "Capture strip unavailable, so no state can be reported."
          )}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Reconstructable risk overlay"
          description={`${phaseALine} · assumed overlay is never venue-reconciled. Leverage, margin, liquidation and VaR stay UNAVAILABLE.`}
          actions={
            <div className="seg" role="group" aria-label="Filter risk fields">
              {(["all", "copied", "unavailable"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={riskFilter === option}
                  onClick={() => {
                    setRiskFilter(option);
                  }}
                >
                  {option}
                </button>
              ))}
            </div>
          }
        />
        <CardBody flush>
          {liveRisk.error === undefined ? null : (
            <div style={{ padding: "0.85rem 0.85rem 0" }}>
              <Notice state="missing" title="PAPER overlay unavailable">
                {liveRisk.error}
              </Notice>
            </div>
          )}
          <DataTable
            columns={riskColumns}
            data={riskRows}
            emptyLabel="No risk fields in this filter."
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Documented paper_risk gates (#65 / D01)"
          description="The catalog the paperbroker enforces. Per-run halt state is not in COURSE-1 JSON and therefore stays UNAVAILABLE."
        />
        <CardBody flush>
          <DataTable
            columns={gateColumns}
            data={[...PAPER_HARD_LIMIT_GATES]}
            monoColumns={["id"]}
          />
        </CardBody>
        <div className="card-foot">
          Reduce-only after halt: halt gates block new entries, a reduce-only flatten is still
          allowed.
        </div>
      </Card>
    </div>
  );
}
