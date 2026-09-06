"use client";

import { useMemo, useState } from "react";

import { Badge } from "../ui/badge";
import { Card, CardBody, CardDisclosure, CardHeader } from "../ui/card";
import { DataTable, dataTableColumnHelper, type DataTableColumns } from "../ui/data-table";
import { KvList } from "../ui/kv";
import { Notice } from "../ui/notice";
import { ReadStatus } from "../ui/read-status";
import { Stat } from "../ui/stat";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import type { VenueCaptureQuery } from "../../lib/paths";
import { researchStubView } from "../../lib/research";
import {
  H1_LEADLAG_NOTE,
  PUBLIC_MID_NOT_RESEARCH,
  RESEARCH_P0_SOURCE,
  RESEARCH_RUN_BINDING_NOTE,
  RESEARCH_UNAVAILABLE,
  researchVerdictTone,
  type ResearchHealthRow,
  type ResearchIdentityRow,
  type ResearchP0View,
  type ResearchRegistryRow,
} from "../../lib/research-p0-view";
import { useResearchP0 } from "../../lib/use-research-p0";

const registryHelper = dataTableColumnHelper<ResearchRegistryRow>();
const healthHelper = dataTableColumnHelper<ResearchHealthRow>();
const identityHelper = dataTableColumnHelper<ResearchIdentityRow>();

function unavailable(value: string) {
  return value === RESEARCH_UNAVAILABLE || value === "n/a" ? (
    <span className="tone-muted">{value}</span>
  ) : (
    value
  );
}

const registryColumns: DataTableColumns<ResearchRegistryRow> = registryHelper.columns([
  registryHelper.accessor("venue", { header: "Venue" }),
  registryHelper.accessor("product", { header: "Product" }),
  registryHelper.accessor("runId", {
    header: "run_id",
    cell: ({ row }) => unavailable(row.original.runId),
  }),
  registryHelper.accessor("retained", {
    header: "Retained",
    cell: ({ row }) => unavailable(row.original.retained),
  }),
  registryHelper.accessor("state", {
    header: "State",
    cell: ({ row }) => unavailable(row.original.state),
  }),
  registryHelper.accessor("wallSpan", {
    header: "Wall span",
    cell: ({ row }) => unavailable(row.original.wallSpan),
  }),
  registryHelper.accessor("status", { header: "Capture" }),
  registryHelper.accessor("pathContract", { header: "path_contract" }),
]) as DataTableColumns<ResearchRegistryRow>;

const healthColumns: DataTableColumns<ResearchHealthRow> = healthHelper.columns([
  healthHelper.accessor("venue", { header: "Venue" }),
  healthHelper.accessor("gapsReconnects", {
    header: "Gaps / reconnects",
    cell: ({ row }) => unavailable(row.original.gapsReconnects),
  }),
  healthHelper.accessor("transportProfiles", {
    header: "transport_profiles",
    cell: ({ row }) => unavailable(row.original.transportProfiles),
  }),
  healthHelper.accessor("partCount", {
    header: "Parts",
    cell: ({ row }) => unavailable(row.original.partCount),
  }),
  healthHelper.accessor("bytes", {
    header: "Bytes",
    cell: ({ row }) => unavailable(row.original.bytes),
  }),
  healthHelper.accessor("lastPartMtime", {
    header: "Last part",
    cell: ({ row }) => unavailable(row.original.lastPartMtime),
  }),
]) as DataTableColumns<ResearchHealthRow>;

const identityColumns: DataTableColumns<ResearchIdentityRow> = identityHelper.columns([
  identityHelper.accessor("venue", { header: "Venue" }),
  identityHelper.accessor("product", { header: "Product" }),
  identityHelper.accessor("impulse", { header: "Impulse instrument", enableSorting: false }),
]) as DataTableColumns<ResearchIdentityRow>;

export function ResearchScreen({
  query,
  initialResearch,
}: {
  query: VenueCaptureQuery;
  initialResearch: ResearchP0View;
}) {
  const { token } = useCockpitRefresh();
  const researchPoll = useResearchP0(query, initialResearch, token);
  const view = researchPoll.data;
  const error = researchPoll.error ?? view.error;
  const [venueFilter, setVenueFilter] = useState<string>("all");
  const stub = researchStubView();

  const venues = useMemo(
    () => ["all", ...new Set(view.registry.map((row) => row.venue))],
    [view.registry],
  );
  const registryRows = useMemo(
    () =>
      venueFilter === "all"
        ? view.registry
        : view.registry.filter((row) => row.venue === venueFilter),
    [venueFilter, view.registry],
  );
  const healthRows = useMemo(
    () =>
      venueFilter === "all" ? view.health : view.health.filter((row) => row.venue === venueFilter),
    [venueFilter, view.health],
  );

  const sufficiency = view.sufficiency;
  const verdictTone = researchVerdictTone(sufficiency.verdict, sufficiency.runBinding);
  const attributed = sufficiency.runBinding === "matched";

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Research</h1>
          <p>
            An artifact and summary viewer for retained capture runs — not a live tape. There is no
            edge, no strategy PnL and no 72h claim mid-run. {PUBLIC_MID_NOT_RESEARCH}: the candle
            chart lives in Markets on purpose.
          </p>
        </div>
        <div className="page-head-actions">
          <ReadStatus
            state={researchPoll}
            sourceLabel="registry read"
            sourceIso={view.observedAt}
          />
          <label className="field">
            <span>Venue</span>
            <select
              value={venueFilter}
              onChange={(event) => {
                setVenueFilter(event.target.value);
              }}
            >
              {venues.map((venue) => (
                <option key={venue} value={venue}>
                  {venue === "all" ? "All venues" : venue}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {error === undefined ? null : (
        <Notice
          state="error"
          title={
            researchPoll.error === undefined
              ? "Research view could not be rebuilt"
              : "Research refresh failed — values below are from an earlier read"
          }
        >
          {error}
        </Notice>
      )}

      <div className="grid grid-sm-2 grid-lg-4">
        <Stat
          label="Sufficiency verdict"
          value={sufficiency.verdict}
          compact
          tone={verdictTone === "muted" ? "muted" : verdictTone}
          meta={
            attributed
              ? "Attributable to the bound run(s)."
              : "Not attributable to the current selection."
          }
        />
        <Stat
          label="Attribution"
          value={sufficiency.runBinding}
          compact
          tone={attributed ? "ok" : sufficiency.runBinding === "unavailable" ? "muted" : "warn"}
          meta={
            sufficiency.runRefs.length === 0
              ? "Summary declares no run_id."
              : `Summary: ${sufficiency.runRefs
                  .map(
                    (ref) =>
                      `${ref.venue === "any" ? "run" : ref.venue.toUpperCase()} ${ref.runId}`,
                  )
                  .join(" · ")}`
          }
        />
        <Stat
          label="Bound runs"
          value={String(view.boundRuns.length)}
          compact
          meta={
            view.boundRuns
              .map((ref) =>
                `${ref.venue.toUpperCase()} ${ref.product ?? ""} ${ref.runId}`.replace(/\s+/g, " "),
              )
              .join(" · ") || "No capture run is bound."
          }
        />
        <Stat
          label="Overlap vs 72h"
          value={view.overlap.elapsed}
          compact
          tone="muted"
          meta="Never claimed mid-run. The clock only reports what the claims already state."
        />
      </div>

      <Card>
        <CardHeader
          title="WP-Q1 sufficiency"
          description={RESEARCH_P0_SOURCE}
          actions={
            <Badge
              tone={attributed ? "ok" : sufficiency.runBinding === "unavailable" ? "muted" : "warn"}
            >
              {sufficiency.runBinding}
            </Badge>
          }
        />
        <CardBody>
          {attributed ? null : (
            <Notice
              state={sufficiency.verdict === RESEARCH_UNAVAILABLE ? "missing" : "stale"}
              title={
                sufficiency.verdict === RESEARCH_UNAVAILABLE
                  ? "No panel summary pointed"
                  : "Summary is not about these runs"
              }
            >
              {RESEARCH_RUN_BINDING_NOTE[sufficiency.runBinding]}
            </Notice>
          )}
          <KvList
            rows={[
              {
                label: "Verdict",
                value: sufficiency.verdict,
                tone: verdictTone === "muted" ? "unknown" : verdictTone,
              },
              {
                label: "Summary runs (per venue)",
                value:
                  sufficiency.runRefs
                    .map(
                      (ref) =>
                        `${ref.venue === "any" ? "run" : ref.venue.toUpperCase()}${ref.product === undefined ? "" : ` ${ref.product}`}: ${ref.runId}`,
                    )
                    .join(" · ") || RESEARCH_UNAVAILABLE,
                tone: sufficiency.runRefs.length === 0 ? "unknown" : "neutral",
              },
              {
                label: "Bound runs (per venue)",
                value:
                  view.boundRuns
                    .map((ref) => `${ref.venue.toUpperCase()} ${ref.product ?? ""}: ${ref.runId}`)
                    .join(" · ") || RESEARCH_UNAVAILABLE,
                tone: view.boundRuns.length === 0 ? "unknown" : "neutral",
              },
              {
                label: "Reasons",
                value: sufficiency.reasons.join(" · ") || RESEARCH_UNAVAILABLE,
                tone: "unknown",
              },
              { label: "HL gap fraction", value: sufficiency.hlGapFraction },
              { label: "BN gap fraction", value: sufficiency.bnGapFraction },
              { label: "Overlap buckets", value: sufficiency.overlapBuckets },
              { label: "panel_version", value: sufficiency.panelVersion },
              { label: "Source file", value: sufficiency.source },
            ]}
          />
        </CardBody>
        <CardDisclosure summary="Why a verdict can be shown but not counted">
          <p style={{ margin: 0 }}>
            A <span className="mono">panel-summary.json</span> is only a verdict about the runs it
            names. The cockpit copies every <span className="mono">run_id</span> the summary
            declares together with its venue (<span className="mono">hl_run_id</span>,{" "}
            <span className="mono">bn_run_id</span>, …) and compares each one with the run bound for
            that same venue. Every named venue must match: a summary about the current HL run and an
            earlier Binance run is <span className="mono">mismatched</span>, a summary naming a
            venue that is not bound is <span className="mono">partial</span>, and neither is ever
            rendered as a pass.
          </p>
        </CardDisclosure>
      </Card>

      <Card>
        <CardHeader
          title="Run registry"
          description="Copied capture claims. Sort by any column; filter by venue above."
        />
        <CardBody flush>
          <DataTable
            columns={registryColumns}
            data={registryRows}
            monoColumns={["runId", "pathContract"]}
            emptyLabel="No bound venue claims. The registry stays UNAVAILABLE rather than inventing rows."
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Capture health"
          description="Gaps, reconnects and transport profiles as recorded. Health JSON is written at stop, so a running capture legitimately shows UNAVAILABLE."
        />
        <CardBody flush>
          <DataTable
            columns={healthColumns}
            data={healthRows}
            numericColumns={["partCount", "bytes"]}
            emptyLabel="Capture health JSON is not pointed. Counts stay UNAVAILABLE."
          />
        </CardBody>
      </Card>

      <div className="grid grid-lg-2">
        <Card>
          <CardHeader title="Instrument identity" description={view.identityWarning} />
          <CardBody flush>
            <DataTable columns={identityColumns} data={view.identity} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Overlap clock"
            description="Elapsed versus the 72h target is deliberately never claimed while a run is in flight."
          />
          <CardBody>
            <KvList
              rows={[
                { label: "HL / BV / KR run", value: view.overlap.hlBvKrRun },
                { label: "BN run", value: view.overlap.bnRun },
                { label: "Overlap start", value: view.overlap.overlapStart },
                { label: "Note", value: view.overlap.note },
                { label: "Elapsed vs 72h", value: view.overlap.elapsed, tone: "unknown" },
                { label: "Badge", value: view.overlap.badge },
              ]}
            />
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-lg-2">
        <Card>
          <CardHeader title="Hypothesis / OOS" description={stub.reason} />
          <CardBody>
            <Notice state="missing" title="Designed, not yet produced">
              These fields exist so the shape of a future experiment is explicit. Nothing is
              generated until a real hypothesis document is written.
            </Notice>
            <KvList
              rows={[
                { label: "Status", value: stub.status, tone: "unknown" },
                { label: "Hypothesis", value: stub.hypothesis, tone: "unknown" },
                { label: "Universe", value: stub.universe, tone: "unknown" },
                { label: "Train / val / OOS", value: stub.trainValidationOos, tone: "unknown" },
              ]}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="H1 lead-lag" description={H1_LEADLAG_NOTE} />
          <CardBody>
            <KvList
              rows={[
                { label: "Status", value: RESEARCH_UNAVAILABLE, tone: "unknown" },
                { label: "promotion_decision", value: "forbidden", tone: "warn" },
                { label: "Edge", value: RESEARCH_UNAVAILABLE, tone: "unknown" },
              ]}
            />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
