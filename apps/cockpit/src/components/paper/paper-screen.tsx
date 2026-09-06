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
import { formatGroupedNumber, yesNo } from "../../lib/display";
import type { SeparateIdentityCards } from "../../lib/identity-cards";
import type { IntentFillRow } from "../../lib/intent-fill";
import type { PaperBotView, PaperRunLifecycle } from "../../lib/paper-bot";
import { usePaperBot } from "../../lib/use-paper-bot";

type OutcomeFilter = "all" | "ACCEPT" | "REJECT" | "UNAVAILABLE";

const helper = dataTableColumnHelper<IntentFillRow>();

function outcomeTone(outcome: IntentFillRow["outcome"]): "ok" | "down" | "warn" {
  switch (outcome) {
    case "ACCEPT":
      return "ok";
    case "REJECT":
      return "down";
    case "UNAVAILABLE":
      return "warn";
    default: {
      const exhaustive: never = outcome;
      throw new Error(`Unhandled tape outcome: ${String(exhaustive)}`);
    }
  }
}

const columns: DataTableColumns<IntentFillRow> = helper.columns([
  helper.accessor("outcome", {
    header: "Outcome",
    cell: ({ row }) => (
      <Badge tone={outcomeTone(row.original.outcome)}>{row.original.outcome}</Badge>
    ),
  }),
  helper.accessor("gateCode", {
    header: "Gate",
    cell: ({ row }) =>
      row.original.outcome === "REJECT" ? (
        <span className="tone-down mono">#65/{row.original.gateCode}</span>
      ) : (
        <span className="tone-muted">{row.original.gateCode}</span>
      ),
  }),
  helper.accessor("clientOrderId", { header: "Intent" }),
  helper.accessor("side", {
    header: "Side",
    cell: ({ row }) => (
      <span className={row.original.side === "BUY" ? "tone-ok" : "tone-down"}>
        {row.original.side}
      </span>
    ),
  }),
  helper.accessor("quantity", {
    header: "Qty",
    cell: ({ row }) => formatGroupedNumber(row.original.quantity),
  }),
  helper.accessor("intentReason", {
    header: "Why",
    enableSorting: false,
    cell: ({ row }) =>
      row.original.outcome === "REJECT" ? row.original.gateReason : row.original.intentReason,
  }),
  helper.accessor("reduceOnly", {
    header: "RO",
    cell: ({ row }) => yesNo(row.original.reduceOnly),
  }),
  helper.accessor("fillPrice", {
    header: "Fill price",
    cell: ({ row }) =>
      row.original.matched ? (
        formatGroupedNumber(row.original.fillPrice)
      ) : (
        <span className="tone-muted">UNAVAILABLE</span>
      ),
  }),
  helper.accessor("positionAfter", {
    header: "Pos after",
    cell: ({ row }) =>
      row.original.matched ? (
        formatGroupedNumber(row.original.positionAfter)
      ) : (
        <span className="tone-muted">UNAVAILABLE</span>
      ),
  }),
]) as DataTableColumns<IntentFillRow>;

function lifecycleTone(state: PaperRunLifecycle["state"]): "info" | "paper" | "muted" {
  switch (state) {
    case "historical":
      return "info";
    case "in-flight":
      return "paper";
    case "unavailable":
      return "muted";
    default: {
      const exhaustive: never = state;
      throw new Error(`Unhandled paper lifecycle: ${String(exhaustive)}`);
    }
  }
}

export function PaperScreen({
  view: initialView,
  identities,
}: {
  view: PaperBotView;
  identities: SeparateIdentityCards;
}) {
  const { token } = useCockpitRefresh();
  const paperPoll = usePaperBot(initialView, token);
  const view = paperPoll.data;
  const [filter, setFilter] = useState<OutcomeFilter>("all");
  const rows = useMemo(
    () => (filter === "all" ? view.tape : view.tape.filter((row) => row.outcome === filter)),
    [filter, view.tape],
  );

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>PAPER desk</h1>
          <p>
            COURSE-1 soak / paperbroker only. The DATA retain identity stays a separate card and is
            never blended into these numbers. Results are an assumed overlay: not venue-reconciled,
            D22-B blocked.
          </p>
        </div>
        <div className="page-head-actions">
          <Badge tone={lifecycleTone(view.lifecycle.state)} title={view.lifecycle.note}>
            {view.lifecycle.label}
          </Badge>
          <ReadStatus state={paperPoll} />
        </div>
      </div>

      {paperPoll.error === undefined ? null : (
        <Notice state="error" title="PAPER refresh failed — values below are from an earlier read">
          {paperPoll.error}
        </Notice>
      )}

      {view.error === undefined ? null : (
        <Notice state="missing" title="PAPER run artifacts unavailable">
          {view.error}
        </Notice>
      )}

      {view.lifecycle.state === "historical" ? (
        <Notice state="pending" title="Historical snapshot of a finished PAPER run">
          {view.lifecycle.note} Claim state{" "}
          <span className="mono">{view.lifecycle.claimState}</span>, health status{" "}
          <span className="mono">{view.lifecycle.healthStatus}</span>. The values are re-read on
          every refresh but will not change.
        </Notice>
      ) : view.lifecycle.artifactSource === "default-fixture" ? (
        <Notice state="pending" title="Repository fixture (demo artifacts)">
          {view.lifecycle.note}
        </Notice>
      ) : null}

      <div className="grid grid-sm-2 grid-lg-4">
        <Stat
          label="Last decision"
          value={view.why.outcome}
          compact
          tone={
            view.why.outcome === "ACCEPT" ? "ok" : view.why.outcome === "REJECT" ? "down" : "warn"
          }
          meta={view.why.reason}
        />
        <Stat
          label="assumed_pnl"
          value={view.results.assumedPnl}
          compact
          tone="warn"
          meta={`exact ${view.results.assumedPnlExact} · ${view.results.venueReconciled}`}
        />
        <Stat
          label="Position"
          value={`${view.results.side}`}
          compact
          meta={`${view.results.size} · entry ${view.results.entry} · mark ${view.results.mark}`}
        />
        <Stat
          label="Intents / rejects"
          value={`${String(view.tape.length)} / ${String(view.tapeRejects.count)}`}
          compact
          tone={view.tapeRejects.count > 0 ? "down" : "muted"}
          meta={view.tapeRejects.note}
        />
      </div>

      <div className="grid grid-lg-3">
        <Card>
          <CardHeader
            title="What ran"
            description={`PAPER · ${view.what.kind} · not DATA retain`}
          />
          <CardBody>
            <KvList
              rows={[
                { label: "Mode", value: view.what.mode, tone: "paper" },
                { label: "Kind", value: view.what.kind, tone: "paper" },
                {
                  label: "Lifecycle",
                  value: view.lifecycle.label,
                  detail: `${view.lifecycle.healthStatus} · ${view.lifecycle.artifactSource}`,
                },
                { label: "run_id", value: view.what.runId },
                { label: "Soak claim path", value: view.what.claimPath },
                { label: "path_contract", value: view.what.pathContract },
                { label: "Instrument", value: view.what.instrument },
                { label: "Strategy", value: view.what.strategyClass },
                { label: "Venue orders", value: view.what.venueOrdersSubmitted },
              ]}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Why the last decision" description={view.why.source} />
          <CardBody>
            <KvList
              rows={[
                {
                  label: "Outcome",
                  value: view.why.outcome,
                  tone:
                    view.why.outcome === "ACCEPT"
                      ? "ok"
                      : view.why.outcome === "REJECT"
                        ? "down"
                        : "warn",
                },
                {
                  label: "Gate",
                  value: view.why.gateName,
                  tone: view.why.outcome === "REJECT" ? "down" : "unknown",
                },
                { label: "Reason", value: view.why.reason },
                { label: "Intent", value: view.why.intentId },
              ]}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Results" description={view.results.assumedPnlLabel} />
          <CardBody>
            <KvList
              rows={[
                { label: "Side", value: view.results.side },
                { label: "Size", value: view.results.size },
                { label: "Entry", value: formatGroupedNumber(view.results.entry) },
                { label: "Mark", value: formatGroupedNumber(view.results.mark) },
                {
                  label: "assumed_pnl",
                  value: view.results.assumedPnl,
                  tone: "warn",
                  title: `${view.results.assumedPnlExact} · ${view.results.assumedPnlLabel}`,
                  detail: `exact ${view.results.assumedPnlExact}`,
                },
                { label: "Ending equity", value: view.results.endingEquity, tone: "warn" },
                { label: "Label", value: view.results.venueReconciled, tone: "warn" },
                { label: "D22-B", value: view.results.d22b, tone: "warn" },
              ]}
            />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Intent tape"
          description={`Last ${String(view.tapeLimit)} intents and fills copied from orders.json and fills.json. Only rows this run actually recorded appear here.`}
          actions={
            <div className="seg" role="group" aria-label="Filter tape by outcome">
              {(["all", "ACCEPT", "REJECT", "UNAVAILABLE"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={filter === option}
                  onClick={() => {
                    setFilter(option);
                  }}
                >
                  {option === "all" ? "All" : option}
                </button>
              ))}
            </div>
          }
        />
        <CardBody flush>
          <DataTable
            columns={columns}
            data={rows}
            numericColumns={["quantity", "fillPrice", "positionAfter"]}
            monoColumns={["clientOrderId"]}
            emptyLabel={
              view.tape.length === 0
                ? "No PAPER intents in this reconstructable COURSE-1 soak."
                : `No ${filter} rows in the last ${String(view.tapeLimit)} intents.`
            }
          />
        </CardBody>
        <CardFooterNote note={view.tapeRejects.note} rejects={view.tapeRejects.count} />
      </Card>

      <div className="grid grid-lg-2">
        <Card>
          <CardHeader
            title="Preflight caps"
            description={`${view.preflight.documentedSource} · ${view.preflight.source}`}
          />
          <CardBody>
            <KvList
              rows={[
                { label: "Assumed equity", value: view.preflight.equity },
                { label: "Risk / trade", value: view.preflight.riskPerTrade },
                { label: "Max leverage", value: view.preflight.maxLeverage },
                { label: "Max gross", value: view.preflight.maxGross },
                { label: "Max net", value: view.preflight.maxNet },
                { label: "Max positions", value: view.preflight.maxPositions },
                { label: "Copied notional", value: view.preflight.maxEntryNotionalUsdc },
                { label: "Copied assumed loss", value: view.preflight.maxAssumedLossUsdc },
                { label: "Same D01 smoke risk", value: view.preflight.sameD01SmokeRisk },
              ]}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="How a reject reads"
            description="Documented #65 / D01 catalog. Reference only — these rows are never mixed into the tape above."
          />
          <CardBody flush>
            <div className="attention">
              {view.gateExamples.map((example) => (
                <div key={example.gateCode} className="attention-row">
                  <Badge tone="outline">example</Badge>
                  <span className="attention-copy">
                    <strong className="mono">{example.gateCode}</strong>
                    <span>{example.reason}</span>
                  </span>
                  <span className="attention-side">
                    <span className="eyebrow">{example.onBreach}</span>
                  </span>
                </div>
              ))}
            </div>
          </CardBody>
          <CardDisclosure summary="Why these are separated from run history">
            <p style={{ margin: 0 }}>
              An illustrative reject inserted between real intents makes the tape unreadable as
              evidence. The catalog is therefore a different data type entirely, so it cannot be
              appended to a run tape even by accident. If this run had rejected an intent, the row
              would appear above with its copied <span className="mono">risk_reasons</span>.
            </p>
          </CardDisclosure>
        </Card>
      </div>

      <div className="grid grid-lg-2">
        <Card>
          <CardHeader
            title="COURSE-1 soak identity"
            description="The paperbroker run this desk is reading."
          />
          <CardBody>
            <KvList
              rows={[
                { label: "Kind", value: identities.soak.kind, tone: "paper" },
                { label: "Mode", value: identities.soak.mode, tone: "paper" },
                { label: "run_id", value: identities.soak.runId },
                { label: "Source", value: identities.soak.source },
                { label: "Instrument", value: identities.soak.instrument },
                { label: "Soak seconds", value: identities.soak.soakSeconds },
                { label: "Feed", value: identities.soak.feed },
                { label: "State", value: identities.soak.state },
                {
                  label: "Venue authoritative",
                  value: identities.soak.venueAuthoritative,
                  tone: "warn",
                },
                {
                  label: "D22-B reconciliation",
                  value: identities.soak.d22bReconciliation,
                  tone: "warn",
                },
              ]}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="DATA retain identity"
            description="Separate on purpose: retained capture runs are never blended with soak PnL."
          />
          <CardBody>
            {identities.retain.error === undefined ? null : (
              <Notice state="missing" title="Retain identity incomplete">
                {identities.retain.error}
              </Notice>
            )}
            <p className="card-desc" style={{ marginBottom: "0.35rem" }}>
              {identities.retain.glanceLine}
            </p>
            <KvList
              rows={identities.retain.venues.map((venue) => ({
                label: venue.chip,
                value: `${venue.status} · ${venue.lastPartAge}`,
                tone: venue.live ? "ok" : "unknown",
                detail: venue.runId,
              }))}
            />
          </CardBody>
          <CardDisclosure summary="Overlap note">
            <p style={{ margin: 0 }}>{identities.retain.overlapNote}</p>
          </CardDisclosure>
        </Card>
      </div>
    </div>
  );
}

function CardFooterNote({ note, rejects }: { note: string; rejects: number }) {
  return (
    <div className="card-foot">
      <span className={rejects > 0 ? "tone-down" : undefined}>{note}</span>
    </div>
  );
}
