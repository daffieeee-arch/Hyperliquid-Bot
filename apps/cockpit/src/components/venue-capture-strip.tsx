"use client";

import { RunPicker } from "./run-picker";
import { RunProvenance } from "./run-provenance";
import { DEFAULT_CAPTURE_FRESH_MAX_S, STALE_MTIME_REASON } from "../lib/capture-freshness";
import { DATA1A_CAPTURE_POLL_MS } from "../lib/data1a-capture-poll";
import {
  captureBindingSourceLabel,
  presentCopiedNumber,
  presentCopiedText,
  presentGapReconnect,
  presentLastPartMtime,
} from "../lib/display";
import type { VenueCaptureQuery } from "../lib/paths";
import type { VenueCaptureChip, VenueCaptureStripResponse } from "../lib/types";
import { useVenueCapturePoll } from "../lib/use-venue-capture";

function chipTitle(venue: VenueCaptureChip): string {
  if (venue.status === "MISSING") {
    return venue.error ?? "Fail closed · not invented";
  }
  if (venue.status === "STALE") {
    return `${presentCopiedText(venue.run_id)} · ${venue.reason ?? STALE_MTIME_REASON}`;
  }
  return `${presentCopiedText(venue.run_id)} · ${venue.status_detail}`;
}

function VenueRow({ venue }: { venue: VenueCaptureChip }) {
  return (
    <tr className={`venue-strip-row tone-${venue.tone}`} title={chipTitle(venue)}>
      <td className="venue-strip-chip">{venue.chip}</td>
      <td>
        {venue.series} · {venue.product}
      </td>
      <td className={`venue-chip-status tone-${venue.tone}`}>
        {venue.live ? <span className="live-dot" aria-hidden="true" /> : null}
        <span>{venue.status}</span>
      </td>
      <td>{venue.last_part_age}</td>
      <td>{presentCopiedNumber(venue.part_count)}</td>
      <td>{presentLastPartMtime(venue.last_part_mtime_utc)}</td>
      <td title="Gaps/reconnects copied from capture-health.json when present">
        {presentGapReconnect(venue.gaps, venue.reconnects)}
      </td>
      <td>{captureBindingSourceLabel(venue.binding_source)}</td>
      <td className="venue-strip-run">{presentCopiedText(venue.run_id)}</td>
    </tr>
  );
}

export function VenueCaptureStrip({
  query,
  initial,
}: {
  query: VenueCaptureQuery;
  initial: VenueCaptureStripResponse;
}) {
  const result = useVenueCapturePoll(query, initial);
  const pollSeconds = String(DATA1A_CAPTURE_POLL_MS / 1000);
  const freshMaxSeconds = result.ok ? result.strip.fresh_max_s : DEFAULT_CAPTURE_FRESH_MAX_S;

  return (
    <section className="venue-strip" aria-label="Multi-venue capture health">
      <div className="venue-strip-head">
        <h2>Capture health</h2>
        <p className="panel-kicker">
          HL/BN/BV/KR · claim + parts/mtime · poll {pollSeconds}s · RUNNING iff last mtime ≤{" "}
          {String(freshMaxSeconds)}s · G/R from health JSON only · not PnL
        </p>
      </div>
      {result.ok ? (
        <>
          <RunPicker query={query} catalog={result.strip.catalog} venues={result.strip.venues} />
          <div className="venue-strip-scroll">
            <table className="venue-strip-table">
              <thead>
                <tr>
                  <th>Venue</th>
                  <th>Feed</th>
                  <th>Status</th>
                  <th>Age</th>
                  <th>Parts</th>
                  <th>Last mtime</th>
                  <th>G/R</th>
                  <th>Bind</th>
                  <th>Run</th>
                </tr>
              </thead>
              <tbody>
                {result.strip.venues.map((venue) => (
                  <VenueRow key={venue.id} venue={venue} />
                ))}
              </tbody>
            </table>
          </div>
          <RunProvenance provenance={result.strip.provenance} />
        </>
      ) : (
        <p className="error">{result.error}</p>
      )}
    </section>
  );
}
