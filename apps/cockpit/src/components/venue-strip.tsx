"use client";

import { Badge } from "./ui/badge";
import { Notice } from "./ui/notice";
import { captureChipDataState, dataStateTone } from "../lib/data-state";
import { presentCopiedText } from "../lib/display";
import type { VenueCaptureStripResponse } from "../lib/types";

/**
 * Compact four-venue capture strip.
 *
 * Read-only by contract: the cockpit never starts or stops a collector, so the
 * strip only reports binding, state and age.
 */
export function VenueStrip({
  strip,
  dense = false,
}: {
  strip: VenueCaptureStripResponse;
  dense?: boolean;
}) {
  if (!strip.ok) {
    return (
      <Notice state="error" title="Capture strip unavailable">
        {strip.error}
      </Notice>
    );
  }

  return (
    <div className="grid grid-sm-2 grid-lg-4" style={{ gap: "0.6rem" }}>
      {strip.strip.venues.map((venue) => {
        const state = captureChipDataState(venue.status);
        const tone = dataStateTone(state);
        return (
          <div
            key={venue.id}
            className="stat"
            style={{ gap: "0.35rem", padding: dense ? "0.6rem 0.7rem" : undefined }}
          >
            <div className="stat-top">
              <span className="stat-label">
                {venue.chip} · {venue.series}
              </span>
              <span className="stat-icon">
                <Badge tone={tone} dot live={venue.live} title={venue.status_detail}>
                  {venue.status}
                </Badge>
              </span>
            </div>
            <div className="row" style={{ gap: "0.5rem" }}>
              <span className="mono" style={{ fontSize: "0.82rem" }}>
                {venue.product}
              </span>
              <span className="eyebrow spacer" title="Age of the newest parquet part">
                {venue.last_part_age}
              </span>
            </div>
            <div className="stat-meta cell-id truncate-1" title={presentCopiedText(venue.run_id)}>
              {presentCopiedText(venue.run_id)}
            </div>
          </div>
        );
      })}
    </div>
  );
}
