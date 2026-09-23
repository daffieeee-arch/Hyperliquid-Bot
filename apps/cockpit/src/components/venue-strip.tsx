"use client";

import { Badge } from "./ui/badge";
import { Notice } from "./ui/notice";
import { captureChipDataState, dataStateTone } from "../lib/data-state";
import { presentCopiedText } from "../lib/display";
import { formatAgeSeconds, secondsBetween, updatedAgoLabel } from "../lib/poll-state";
import { localClockLabel } from "../lib/time-display";
import type { VenueCaptureStripResponse } from "../lib/types";
import { useNow } from "../lib/use-now";

function stripPartAge(
  mtimeUtc: string | undefined,
  nowIso: string | undefined,
  fallback: string,
): string {
  const seconds = secondsBetween(mtimeUtc, nowIso);
  return seconds === undefined ? fallback : formatAgeSeconds(seconds);
}

/**
 * Compact four-venue capture strip.
 *
 * Read-only by contract: the cockpit never starts or stops a collector, so the
 * strip only reports binding, state and age.
 */
export function VenueStrip({
  strip,
  dense = false,
  degraded = false,
}: {
  strip: VenueCaptureStripResponse;
  dense?: boolean;
  /** True when the latest read failed and these are values from an earlier read. */
  degraded?: boolean;
}) {
  const nowIso = useNow();
  if (!strip.ok) {
    return (
      <Notice state="error" title="Capture strip unavailable">
        {strip.error}
      </Notice>
    );
  }

  return (
    <div className="grid grid-sm-2 grid-lg-5" style={{ gap: "0.6rem" }}>
      {strip.strip.venues.map((venue) => {
        const state = captureChipDataState(venue.status);
        const tone = dataStateTone(state);
        return (
          <div
            key={venue.id}
            className="stat"
            style={{
              gap: "0.35rem",
              padding: dense ? "0.6rem 0.7rem" : undefined,
              opacity: degraded ? 0.72 : undefined,
            }}
            title={
              degraded ? "Last read failed; values are from an earlier successful read." : undefined
            }
          >
            <div className="stat-top">
              <span className="stat-label">
                {venue.chip} · {venue.series}
              </span>
              <span className="stat-icon">
                <Badge tone={tone} dot live={venue.live && !degraded} title={venue.status_detail}>
                  {venue.status}
                </Badge>
              </span>
            </div>
            <div className="row" style={{ gap: "0.5rem" }}>
              <span className="mono" style={{ fontSize: "0.82rem" }}>
                {venue.product}
              </span>
              <span
                className="eyebrow spacer"
                title={
                  venue.last_part_mtime_utc === undefined
                    ? "Age of the newest parquet part"
                    : `Newest part ${localClockLabel(venue.last_part_mtime_utc)} · ${updatedAgoLabel(
                        venue.last_part_mtime_utc,
                        nowIso,
                        venue.last_part_age === "n/a"
                          ? "age n/a"
                          : `updated ${venue.last_part_age} ago`,
                      )}`
                }
              >
                {stripPartAge(venue.last_part_mtime_utc, nowIso, venue.last_part_age)}
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
