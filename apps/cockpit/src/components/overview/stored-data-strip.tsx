"use client";

import { Badge } from "../ui/badge";
import { Notice } from "../ui/notice";
import { OriginBadge } from "../ui/origin-badge";
import { dataStateTone } from "../../lib/data-state";
import { formatGroupedNumber } from "../../lib/display";
import { venueTapeSummaries } from "../../lib/market-tape-rows";
import type { InstrumentTape, MarketTapeResponse } from "../../lib/market-tape-types";
import { formatAgeSeconds, secondsBetween } from "../../lib/poll-state";
import { localClockLabel } from "../../lib/time-display";
import type { VenueCaptureStripResponse } from "../../lib/types";
import { useNow } from "../../lib/use-now";

function liveDataAge(at: string | undefined, nowIso: string | undefined, frozen: string): string {
  const seconds = secondsBetween(at, nowIso);
  return seconds === undefined ? frozen : formatAgeSeconds(seconds);
}

function InstrumentLine({ instrument }: { instrument: InstrumentTape }) {
  const last = instrument.lastTrade;
  const bbo = instrument.lastBbo;
  return (
    <div className="row" style={{ gap: "0.5rem", alignItems: "baseline" }}>
      <span className="mono" style={{ fontSize: "0.78rem" }} title={instrument.product}>
        {instrument.kind === "perpetual" ? "PERP" : instrument.kind === "spot" ? "SPOT" : "?"}{" "}
        {instrument.quote}
      </span>
      <span
        className={`mono ${last?.side === "buy" ? "tone-up" : last?.side === "sell" ? "tone-down" : "tone-muted"}`}
        style={{ fontSize: "0.95rem", fontWeight: 600 }}
        title={last === undefined ? "No trade decoded" : `last trade ${localClockLabel(last.at)}`}
      >
        {last === undefined ? "—" : formatGroupedNumber(last.price)}
      </span>
      <span
        className="eyebrow spacer"
        title={bbo === undefined ? "No BBO decoded" : `BBO ${localClockLabel(bbo.at)}`}
      >
        {bbo === undefined
          ? "no BBO"
          : `${formatGroupedNumber(bbo.bid)} / ${formatGroupedNumber(bbo.ask)} · ${bbo.spreadBps} bps`}
      </span>
    </div>
  );
}

/**
 * Compact per-venue stored-data summary for the overview.
 * Real values only; a venue without readable parts says so instead of showing zeros.
 */
export function StoredDataStrip({
  tape,
  strip,
  degraded,
}: {
  tape: MarketTapeResponse;
  strip: VenueCaptureStripResponse;
  degraded: boolean;
}) {
  const nowIso = useNow();
  if (!tape.ok) {
    return (
      <Notice state="error" title="Stored market data unavailable">
        {tape.error}
      </Notice>
    );
  }
  const summaries = venueTapeSummaries(tape, strip);
  return (
    <div className="grid grid-sm-2 grid-lg-5" style={{ gap: "0.6rem" }}>
      {summaries.map((summary) => (
        <div
          key={summary.tape.id}
          className="stat"
          style={{ gap: "0.4rem", padding: "0.6rem 0.7rem", opacity: degraded ? 0.72 : undefined }}
          title={
            degraded ? "Last read failed; values are from an earlier successful read." : undefined
          }
        >
          <div className="stat-top">
            <span className="stat-label">
              {summary.tape.chip} · {summary.tape.series}
            </span>
            <span className="stat-icon">
              <OriginBadge
                origin={summary.origin}
                live={summary.chip?.live === true && !degraded}
              />
            </span>
          </div>
          {summary.tape.status === "ok" ? (
            <>
              {summary.tape.instruments.length === 0 ? (
                <span className="stat-meta">No instrument decoded yet.</span>
              ) : (
                summary.tape.instruments.map((instrument) => (
                  <InstrumentLine key={instrument.product} instrument={instrument} />
                ))
              )}
              <div className="row" style={{ gap: "0.5rem" }}>
                <Badge
                  tone={dataStateTone(summary.dataState)}
                  title={`Last stored event ${localClockLabel(summary.tape.lastEventUtc)}`}
                >
                  {`data ${liveDataAge(summary.tape.lastEventUtc, nowIso, summary.lastDataAge)} old`}
                </Badge>
                <span className="eyebrow spacer" title="Published parts and their total size">
                  {summary.published} · {summary.volume}
                </span>
              </div>
            </>
          ) : (
            <span className="stat-meta tone-muted" title={summary.tape.error}>
              {summary.tape.status === "missing" ? "No stored data bound." : "Parts unreadable."}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
