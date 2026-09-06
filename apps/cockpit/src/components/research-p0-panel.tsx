"use client";

import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { KvTable } from "./kv-table";
import { researchStubView } from "../lib/research";
import {
  H1_LEADLAG_NOTE,
  PUBLIC_MID_NOT_RESEARCH,
  RESEARCH_P0_SOURCE,
  RESEARCH_UNAVAILABLE,
  RESEARCH_ZONE_KICKER,
  type ResearchHealthRow,
  type ResearchIdentityRow,
  type ResearchP0View,
  type ResearchRegistryRow,
} from "../lib/research-p0-view";

const features = tableFeatures({});
const registryHelper = createColumnHelper<typeof features, ResearchRegistryRow>();
const healthHelper = createColumnHelper<typeof features, ResearchHealthRow>();
const identityHelper = createColumnHelper<typeof features, ResearchIdentityRow>();

const registryColumns = registryHelper.columns([
  registryHelper.accessor("venue", { header: "Venue" }),
  registryHelper.accessor("product", { header: "Product" }),
  registryHelper.accessor("runId", { header: "run_id" }),
  registryHelper.accessor("retained", { header: "Retained" }),
  registryHelper.accessor("state", { header: "State" }),
  registryHelper.accessor("pathContract", { header: "path_contract" }),
  registryHelper.accessor("wallSpan", { header: "Wall span" }),
  registryHelper.accessor("status", { header: "Status" }),
]);

const healthColumns = healthHelper.columns([
  healthHelper.accessor("venue", { header: "Venue" }),
  healthHelper.accessor("gapsReconnects", { header: "G/R" }),
  healthHelper.accessor("transportProfiles", { header: "transport_profiles" }),
  healthHelper.accessor("partCount", { header: "Parts" }),
  healthHelper.accessor("bytes", { header: "Bytes" }),
  healthHelper.accessor("lastPartMtime", { header: "Last part" }),
]);

const identityColumns = identityHelper.columns([
  identityHelper.accessor("venue", { header: "Venue" }),
  identityHelper.accessor("product", { header: "Product" }),
  identityHelper.accessor("impulse", { header: "Impulse instrument" }),
]);

const EMPTY_REGISTRY: ResearchRegistryRow[] = [];
const EMPTY_HEALTH: ResearchHealthRow[] = [];
const EMPTY_IDENTITY: ResearchIdentityRow[] = [];

function warnClass(value: string): string | undefined {
  return value === RESEARCH_UNAVAILABLE || value === "n/a" ? "tone-warn" : undefined;
}

function RegistryTable({ rows }: { rows: ResearchRegistryRow[] }) {
  const table = useTable({
    features,
    columns: registryColumns,
    data: rows.length === 0 ? EMPTY_REGISTRY : rows,
  });
  if (rows.length === 0) {
    return <p className="empty">No bound venue claims. Registry stays UNAVAILABLE.</p>;
  }
  return (
    <div className="markets-scroll">
      <table className="blotter">
        <caption>Run registry · copied claims · not a live tape</caption>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th key={header.id}>
                  {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getAllCells().map((cell) => {
                const value = String(cell.getValue() ?? "");
                const mono = cell.column.id === "runId" || cell.column.id === "pathContract";
                return (
                  <td key={cell.id} className={mono ? "mono-id" : undefined}>
                    <span className={warnClass(value)}>{value}</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HealthTable({ rows }: { rows: ResearchHealthRow[] }) {
  const table = useTable({
    features,
    columns: healthColumns,
    data: rows.length === 0 ? EMPTY_HEALTH : rows,
  });
  if (rows.length === 0) {
    return <p className="empty">Capture health JSON is not pointed. Counts stay UNAVAILABLE.</p>;
  }
  return (
    <div className="markets-scroll">
      <table className="blotter">
        <caption>Capture health · mid-run missing JSON stays UNAVAILABLE</caption>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th
                  key={header.id}
                  className={
                    header.column.id === "partCount" || header.column.id === "bytes"
                      ? "num"
                      : undefined
                  }
                >
                  {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getAllCells().map((cell) => {
                const value = String(cell.getValue() ?? "");
                const numeric = cell.column.id === "partCount" || cell.column.id === "bytes";
                return (
                  <td key={cell.id} className={numeric ? "num" : undefined}>
                    <span className={warnClass(value)}>{value}</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IdentityTable({ rows }: { rows: ResearchIdentityRow[] }) {
  const table = useTable({
    features,
    columns: identityColumns,
    data: rows.length === 0 ? EMPTY_IDENTITY : rows,
  });
  return (
    <div className="markets-scroll">
      <table className="blotter">
        <caption>Instrument identity · explicit impulse · never blend Spot↔USDM</caption>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th key={header.id}>
                  {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getAllCells().map((cell) => (
                <td key={cell.id}>
                  <table.FlexRender cell={cell} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResearchP0Panel({ view }: { view: ResearchP0View }) {
  const stub = researchStubView();
  return (
    <section className="zone" aria-label="RESEARCH">
      <h2 className="zone-label">Research</h2>
      <p className="zone-kicker">{RESEARCH_ZONE_KICKER}</p>
      <Card aria-label="Research viewer bound">
        <CardHeader>
          <CardTitle>Research viewer</CardTitle>
          <CardDescription>Quant P0 artifact / summary cards · no live tape</CardDescription>
        </CardHeader>
        <CardContent>
          <KvTable
            rows={[
              { label: "Truth source", value: RESEARCH_P0_SOURCE },
              { label: "Public mid", value: PUBLIC_MID_NOT_RESEARCH, tone: "warn" },
              { label: "Candles", value: "MARKETS only · not research truth", tone: "warn" },
              { label: "Missing artifacts", value: RESEARCH_UNAVAILABLE, tone: "warn" },
              { label: "Edge / strategy PnL", value: RESEARCH_UNAVAILABLE, tone: "warn" },
            ]}
          />
        </CardContent>
      </Card>
      <Card aria-label="Run registry">
        <CardHeader>
          <CardTitle>Run registry</CardTitle>
          <CardDescription>
            venue · product · run_id · retained · state · path_contract
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RegistryTable rows={view.registry} />
        </CardContent>
      </Card>
      <Card aria-label="Research capture health">
        <CardHeader>
          <CardTitle>Capture health</CardTitle>
          <CardDescription>
            Gaps / reconnects / transport_profiles[] · BN spot · usdm_market · usdm_public
          </CardDescription>
        </CardHeader>
        <CardContent>
          <HealthTable rows={view.health} />
        </CardContent>
      </Card>
      <div className="zone-grid zone-grid-2">
        <Card aria-label="Sufficiency gates">
          <CardHeader>
            <CardTitle>Sufficiency gates</CardTitle>
            <CardDescription>{view.sufficiency.source}</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                {
                  label: "Verdict",
                  value: view.sufficiency.verdict,
                  tone: view.sufficiency.verdict === RESEARCH_UNAVAILABLE ? "warn" : "ok",
                },
                {
                  label: "Reasons",
                  value: view.sufficiency.reasons.join(" · ") || RESEARCH_UNAVAILABLE,
                  tone: "warn",
                },
                { label: "HL gap fraction", value: view.sufficiency.hlGapFraction },
                { label: "BN gap fraction", value: view.sufficiency.bnGapFraction },
                { label: "Overlap buckets", value: view.sufficiency.overlapBuckets },
                { label: "panel_version", value: view.sufficiency.panelVersion },
              ]}
            />
          </CardContent>
        </Card>
        <Card aria-label="Overlap clock">
          <CardHeader>
            <CardTitle>Overlap clock</CardTitle>
            <CardDescription>Never claim 72h mid-run · elapsed stays UNAVAILABLE</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                { label: "HL/BV/KR run", value: view.overlap.hlBvKrRun },
                { label: "BN run", value: view.overlap.bnRun },
                { label: "Overlap start", value: view.overlap.overlapStart },
                { label: "Note", value: view.overlap.note },
                { label: "Elapsed vs 72h", value: view.overlap.elapsed, tone: "warn" },
                { label: "Badge", value: view.overlap.badge },
              ]}
            />
          </CardContent>
        </Card>
      </div>
      <Card aria-label="Instrument identity">
        <CardHeader>
          <CardTitle>Instrument identity</CardTitle>
          <CardDescription>{view.identityWarning}</CardDescription>
        </CardHeader>
        <CardContent>
          <IdentityTable rows={view.identity} />
        </CardContent>
      </Card>
      <div className="zone-grid zone-grid-2">
        <Card aria-label="Hypothesis fields">
          <CardHeader>
            <CardTitle>Hypothesis / OOS</CardTitle>
            <CardDescription>{stub.reason}</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                { label: "Status", value: stub.status, tone: "warn" },
                { label: "Hypothesis", value: stub.hypothesis, tone: "warn" },
                { label: "Universe", value: stub.universe, tone: "warn" },
                { label: "Train / val / OOS", value: stub.trainValidationOos, tone: "warn" },
              ]}
            />
          </CardContent>
        </Card>
        <Card aria-label="H1 lead-lag">
          <CardHeader>
            <CardTitle>H1 lead-lag</CardTitle>
            <CardDescription>{H1_LEADLAG_NOTE}</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                { label: "Status", value: RESEARCH_UNAVAILABLE, tone: "warn" },
                { label: "promotion_decision", value: "forbidden", tone: "warn" },
                { label: "Edge", value: RESEARCH_UNAVAILABLE, tone: "warn" },
              ]}
            />
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
