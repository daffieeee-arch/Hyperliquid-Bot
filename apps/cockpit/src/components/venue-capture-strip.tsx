"use client";

import { DATA1A_CAPTURE_POLL_MS } from "../lib/data1a-capture-poll";
import { presentCopiedNumber, presentCopiedText } from "../lib/display";
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
      <p className="venue-chip-note">
        {venue.status === "MISSING"
          ? "Fail closed · not invented"
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

  return (
    <section className="venue-strip" aria-label="Multi-venue capture health">
      <div className="venue-strip-head">
        <h2>Capture health</h2>
        <p className="panel-kicker">
          HL / Binance / Bitvavo / Kraken · reconstructable claim / parts · poll {pollSeconds}s ·
          not PnL
        </p>
      </div>
      {result.ok ? (
        <ul className="venue-strip-chips">
          {result.strip.venues.map((venue) => (
            <Chip key={venue.id} venue={venue} />
          ))}
        </ul>
      ) : (
        <p className="error">{result.error}</p>
      )}
    </section>
  );
}
