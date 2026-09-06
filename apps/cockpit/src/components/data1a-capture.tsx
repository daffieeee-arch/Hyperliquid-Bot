"use client";

import { KvTable } from "./kv-table";
import { MetricTile } from "./metric-tile";
import { STALE_MTIME_REASON } from "../lib/capture-freshness";
import {
  captureRunSourceLabel,
  data1aCaptureHealthPresentation,
  formatGroupedNumber,
  presentCopiedNumber,
  presentCopiedText,
  presentData1ADuration,
  yesNo,
} from "../lib/display";
import { DATA1A_CAPTURE_POLL_MS } from "../lib/data1a-capture-poll";
import { useData1ACapturePoll } from "../lib/use-data1a-capture";
import type { Data1ACaptureResponse, Data1ACaptureSnapshot } from "../lib/types";

function sourceLabel(source: Data1ACaptureSnapshot["source"]): string {
  return captureRunSourceLabel(source);
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

function CaptureDesk({ snapshot }: { snapshot: Data1ACaptureSnapshot }) {
  const healthView = data1aCaptureHealthPresentation(snapshot);
  const pollSeconds = String(DATA1A_CAPTURE_POLL_MS / 1000);
  const duration = presentData1ADuration(snapshot);

  return (
    <>
      <section className="metrics" aria-label="DATA-1A capture snapshot">
        <MetricTile
          label="DATA-1A health"
          value={healthView.tileLabel}
          meta={
            healthView.live
              ? `health JSON pending until stop · poll ${pollSeconds}s · ${snapshot.runId}`
              : healthView.reason === STALE_MTIME_REASON
                ? `${STALE_MTIME_REASON} · fresh max ${String(snapshot.fresh_max_s)}s · ${snapshot.runId}`
                : `${snapshot.runId} · ${snapshot.claim.retained ? "retained" : "smoke"}`
          }
          note={
            snapshot.health === undefined
              ? healthView.note
              : `24/7 claim ${yesNo(snapshot.health.twenty_four_seven)} · public BTC-PERP`
          }
          tone={healthView.tone}
          live={healthView.live}
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
          live={healthView.live && snapshot.parts.raw_dir_present}
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
            Reconstructable public BTC-PERP run · not COURSE-1 soak PnL · health JSON is written at
            stop · filesystem poll {pollSeconds}s
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
              value: healthView.statusLabel,
              tone: healthView.tone,
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
              label: "Bytes on disk",
              value:
                snapshot.parts.bytes === undefined
                  ? "n/a"
                  : formatGroupedNumber(String(snapshot.parts.bytes)),
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
              label: "Fresh max",
              value: `${String(snapshot.fresh_max_s)}s`,
            },
            {
              label: "Freshness",
              value: healthView.reason ?? (healthView.live ? "fresh" : "n/a"),
              tone: healthView.reason === STALE_MTIME_REASON ? "warn" : "neutral",
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
  const result = useData1ACapturePoll(queryRunId, initial);

  return (
    <section className="capture-section" aria-label="DATA-1A capture health">
      {result.ok ? (
        <CaptureDesk snapshot={result.snapshot} />
      ) : (
        <section className="panel">
          <div className="panel-head">
            <h2>DATA-1A capture / health</h2>
            <p className="panel-kicker">
              Missing artifact root or run directory fails closed. Part counts and PnL are not
              invented. Filesystem poll {String(DATA1A_CAPTURE_POLL_MS / 1000)}s when a run is
              readable.
            </p>
          </div>
          <p className="error">{result.error}</p>
        </section>
      )}
    </section>
  );
}
