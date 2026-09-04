"use client";

import { useEffect, useState } from "react";

import { KvTable } from "./kv-table";
import { MetricTile } from "./metric-tile";
import {
  formatGroupedNumber,
  healthTone,
  presentCopiedNumber,
  presentCopiedText,
  yesNo,
} from "../lib/display";
import type { Data1ACaptureResponse, Data1ACaptureSnapshot } from "../lib/types";

const REFRESH_MS = 10_000;

function sourceLabel(source: Data1ACaptureSnapshot["source"]): string {
  if (source === "default-fixture") {
    return "default-fixture";
  }
  if (source === "path-contract") {
    return "path-contract";
  }
  return "data1a-run-dir";
}

function partsValue(snapshot: Data1ACaptureSnapshot): string {
  if (!snapshot.parts.raw_dir_present) {
    return "raw/ missing";
  }
  if (snapshot.parts.count === undefined) {
    return "n/a";
  }
  return String(snapshot.parts.count);
}

function healthValue(snapshot: Data1ACaptureSnapshot): string {
  if (snapshot.health !== undefined) {
    return snapshot.health.status;
  }
  if (snapshot.health_missing) {
    return "NOT WRITTEN";
  }
  return "UNREADABLE";
}

function CaptureDesk({ snapshot }: { snapshot: Data1ACaptureSnapshot }) {
  const status = healthValue(snapshot);
  const statusTone = snapshot.health === undefined ? "warn" : healthTone(snapshot.health.status);
  const duration =
    snapshot.claim.duration_seconds === undefined
      ? "n/a"
      : `${String(snapshot.claim.duration_seconds)}s`;

  return (
    <>
      <section className="metrics" aria-label="DATA-1A capture snapshot">
        <MetricTile
          label="DATA-1A health"
          value={status}
          meta={`${snapshot.runId} · ${snapshot.claim.retained ? "retained" : "smoke"}`}
          note={
            snapshot.health === undefined
              ? (snapshot.health_error ?? "capture-health.json is not written yet")
              : `24/7 claim ${yesNo(snapshot.health.twenty_four_seven)} · public BTC-PERP`
          }
          tone={statusTone}
        />
        <MetricTile
          label="Claim state"
          value={snapshot.claim.state}
          meta={`${presentCopiedText(snapshot.claim.venue)} ${presentCopiedText(snapshot.claim.product)}`}
          note={`Signing ${yesNo(snapshot.claim.signing === true)} · credentialless ${yesNo(snapshot.claim.credentialless === true)}`}
          tone="neutral"
        />
        <MetricTile
          label="Published parts"
          value={partsValue(snapshot)}
          meta={
            snapshot.parts.bytes === undefined
              ? "filesystem glob part-*.parquet only"
              : `${formatGroupedNumber(String(snapshot.parts.bytes))} bytes on disk`
          }
          note={
            snapshot.parts.last_part_mtime_utc === undefined
              ? "Last part mtime n/a — payloads are not read"
              : `Last mtime ${snapshot.parts.last_part_mtime_utc}`
          }
          tone={snapshot.parts.raw_dir_present ? "neutral" : "warn"}
        />
        <MetricTile
          label="Gaps / reconnects"
          value={
            snapshot.health === undefined
              ? "n/a"
              : `${presentCopiedNumber(snapshot.health.gaps)} / ${presentCopiedNumber(snapshot.health.reconnects)}`
          }
          meta="Copied from capture-health.json when present"
          note={
            snapshot.health === undefined
              ? "Not invented while health is missing"
              : `Health parquet_files ${presentCopiedNumber(snapshot.health.parquet_files)}`
          }
          tone={snapshot.health === undefined ? "warn" : "neutral"}
        />
      </section>

      <section className="panel capture-panel">
        <div className="panel-head">
          <h2>DATA-1A capture / health</h2>
          <p className="panel-kicker">
            Reconstructable public BTC-PERP run · not COURSE-1 soak PnL · health JSON is end-of-run
          </p>
        </div>
        <KvTable
          rows={[
            { label: "Run id", value: snapshot.runId },
            { label: "Claim state", value: snapshot.claim.state },
            {
              label: "Retained",
              value: yesNo(snapshot.claim.retained),
              tone: snapshot.claim.retained ? "ok" : "neutral",
            },
            { label: "Duration", value: duration },
            { label: "Feed", value: presentCopiedText(snapshot.claim.feed) },
            {
              label: "Health status",
              value: status,
              tone: statusTone,
            },
            {
              label: "Health events",
              value: presentCopiedNumber(snapshot.health?.events),
            },
            {
              label: "Health gaps",
              value: presentCopiedNumber(snapshot.health?.gaps),
            },
            {
              label: "Health reconnects",
              value: presentCopiedNumber(snapshot.health?.reconnects),
            },
            {
              label: "Health parquet files",
              value: presentCopiedNumber(snapshot.health?.parquet_files),
            },
            {
              label: "Filesystem parts",
              value: snapshot.parts.raw_dir_present
                ? presentCopiedNumber(snapshot.parts.count)
                : "raw/ missing",
              tone: snapshot.parts.raw_dir_present ? "neutral" : "warn",
            },
            {
              label: "Last part",
              value: presentCopiedText(snapshot.parts.last_part_name),
            },
            {
              label: "Last part mtime",
              value: presentCopiedText(snapshot.parts.last_part_mtime_utc),
            },
            {
              label: "DuckDB catalog",
              value: snapshot.duckdb_present ? "present" : "missing",
            },
            {
              label: "24/7 claim",
              value: yesNo(snapshot.claim.twenty_four_seven),
            },
            { label: "Source", value: sourceLabel(snapshot.source) },
            { label: "Observed at", value: snapshot.observed_at },
          ]}
        />
        <p className="source">
          Source {snapshot.source}: <code>{snapshot.runDir}</code>
        </p>
      </section>
    </>
  );
}

export function Data1ACapturePanel({
  queryRunId,
  initial,
}: {
  queryRunId?: string;
  initial: Data1ACaptureResponse;
}) {
  const [result, setResult] = useState<Data1ACaptureResponse>(initial);

  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const params = new URLSearchParams();
        if (queryRunId) {
          params.set("data1a_run_id", queryRunId);
        }
        const query = params.toString();
        const response = await fetch(
          query === "" ? "/api/data1a-capture" : `/api/data1a-capture?${query}`,
          { cache: "no-store" },
        );
        const payload: unknown = await response.json();
        if (
          typeof payload !== "object" ||
          payload === null ||
          !("ok" in payload) ||
          typeof payload.ok !== "boolean"
        ) {
          throw new Error("DATA-1A capture response was not a fail-closed object.");
        }
        if (!cancelled) {
          setResult(payload as Data1ACaptureResponse);
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setResult({
            ok: false,
            error:
              error instanceof Error ? error.message : "DATA-1A capture health is unavailable.",
          });
        }
      }
    }

    const timer = window.setInterval(() => {
      void refresh();
    }, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [queryRunId]);

  return (
    <section className="capture-section" aria-label="DATA-1A capture health">
      {result.ok ? (
        <CaptureDesk snapshot={result.snapshot} />
      ) : (
        <section className="panel">
          <div className="panel-head">
            <h2>DATA-1A capture / health</h2>
            <p className="panel-kicker">
              Missing reconstructable files fail closed; counts are not invented
            </p>
          </div>
          <p className="error">{result.error}</p>
        </section>
      )}
    </section>
  );
}
