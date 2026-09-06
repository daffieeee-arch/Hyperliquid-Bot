"use client";

import { RunPicker } from "./run-picker";
import { RunProvenance } from "./run-provenance";
import { DEFAULT_CAPTURE_FRESH_MAX_S, STALE_MTIME_REASON } from "../lib/capture-freshness";
import { DATA1A_CAPTURE_POLL_MS } from "../lib/data1a-capture-poll";
import { captureBindingSourceLabel, presentCopiedNumber, presentCopiedText } from "../lib/display";
import type { VenueCaptureQuery } from "../lib/paths";
import type { VenueCaptureChip, VenueCaptureStripResponse } from "../lib/types";
import { useVenueCapturePoll } from "../lib/use-venue-capture";

function Chip({ venue }: { venue: VenueCaptureChip }) {
  return (
    <li className={`venue-chip tone-${venue.tone}`}>
      <div className="venue-chip-id">
        <strong>{venue.chip}</strong>
        <span>
          {venue.series} · {venue.product}
        </span>
      </div>
      <p className={`venue-chip-status tone-${venue.tone}`}>
        {venue.live ? <span className="live-dot" aria-hidden="true" /> : null}
        <span>{venue.status}</span>
      </p>
      <dl className="venue-chip-metrics">
        <div>
          <dt>Age</dt>
          <dd>{venue.last_part_age}</dd>
        </div>
        <div>
          <dt>Parts</dt>
          <dd>{presentCopiedNumber(venue.part_count)}</dd>
        </div>
      </dl>
      <p className="venue-chip-run">
        <span>{presentCopiedText(venue.run_id)}</span>
        <span>{captureBindingSourceLabel(venue.binding_source)}</span>
      </p>
      <p className="venue-chip-note">
        {venue.status === "MISSING"
          ? "Fail closed · not invented"
          : venue.status === "STALE"
            ? `${presentCopiedText(venue.run_id)} · ${venue.reason ?? STALE_MTIME_REASON}`
            : `${presentCopiedText(venue.run_id)} · ${venue.status_detail}`}
      </p>
    </li>
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
          HL / Binance / Bitvavo / Kraken · reconstructable claim / parts · poll {pollSeconds}s ·
          RUNNING only if last part mtime ≤ {String(freshMaxSeconds)}s · not PnL
        </p>
      </div>
      {result.ok ? (
        <>
          <RunPicker query={query} catalog={result.strip.catalog} venues={result.strip.venues} />
          <ul className="venue-strip-chips">
            {result.strip.venues.map((venue) => (
              <Chip key={venue.id} venue={venue} />
            ))}
          </ul>
          <RunProvenance provenance={result.strip.provenance} />
        </>
      ) : (
        <p className="error">{result.error}</p>
      )}
    </section>
  );
}
