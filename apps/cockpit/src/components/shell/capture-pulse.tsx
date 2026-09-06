"use client";

import { RefreshCw } from "lucide-react";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { OriginBadge } from "../ui/origin-badge";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { describeOriginSummary, stripOrigin } from "../../lib/data-origin";
import { captureChipDataState, dataStateTone, worstDataState } from "../../lib/data-state";
import type { VenueCaptureQuery } from "../../lib/paths";
import { clockLabel, describePollRead } from "../../lib/poll-state";
import type { VenueCaptureStripResponse } from "../../lib/types";
import { useVenueCapturePoll } from "../../lib/use-venue-capture";

const PENDING = "__pending__";
const PENDING_STRIP: VenueCaptureStripResponse = { ok: false, error: PENDING };

/**
 * Topbar freshness pulse.
 *
 * Shows the worst capture state and the time of the last *successful* read.
 * The refresh clock itself is deliberately not displayed as a timestamp: a
 * tick that failed leaves the previous values on screen and turns the pulse
 * red instead of advancing the clock.
 */
export function CapturePulse({ query }: { query: VenueCaptureQuery }) {
  const { token, refreshNow, paused, setPaused, intervalMs } = useCockpitRefresh();
  const poll = useVenueCapturePoll(query, PENDING_STRIP, token);
  const strip = poll.data;
  const pending = !strip.ok && strip.error === PENDING && poll.error === undefined;
  const read = describePollRead(poll);
  const origin = stripOrigin(strip);

  const summary = strip.ok
    ? (() => {
        const worst = worstDataState(
          strip.strip.venues.map((venue) => captureChipDataState(venue.status)),
        );
        const live = strip.strip.venues.filter((venue) => venue.live).length;
        return {
          tone: dataStateTone(worst),
          label:
            live > 0
              ? `${String(live)}/${String(strip.strip.venues.length)} live`
              : `${String(strip.strip.venues.length)} bound`,
          live: live > 0 && !poll.degraded,
          title: `Worst capture state: ${worst}. Fresh ≤ ${String(strip.strip.fresh_max_s)}s. Newest part ${clockLabel(
            strip.strip.venues
              .map((venue) => venue.last_part_mtime_utc)
              .filter((value): value is string => value !== undefined)
              .sort()
              .at(-1),
          )}.`,
        };
      })()
    : null;

  return (
    <>
      {pending ? (
        <span className="skeleton" style={{ width: "4.5rem", height: "1.25rem" }} aria-hidden />
      ) : summary === null ? (
        <Badge tone="down" title={poll.error ?? (strip.ok ? undefined : strip.error)}>
          Capture error
        </Badge>
      ) : (
        <>
          <OriginBadge
            origin={origin.origin}
            detail={describeOriginSummary(origin)}
            live={!poll.degraded}
            className="topbar-wide"
          />
          <Badge tone={summary.tone} dot live={summary.live} title={summary.title}>
            {summary.label}
          </Badge>
        </>
      )}
      <Badge
        tone={read.tone}
        title={`${read.detail} Auto-refresh every ${String(intervalMs / 1000)}s${paused ? " (paused)" : ""}.`}
      >
        {paused && read.tone !== "down" ? `paused · ${read.label}` : read.label}
      </Badge>
      <Button
        type="button"
        variant="outline"
        size="icon"
        title={paused ? "Auto-refresh paused — resume" : "Refresh now"}
        aria-label={paused ? "Resume auto refresh" : "Refresh now"}
        onClick={() => {
          if (paused) {
            setPaused(false);
          }
          refreshNow();
        }}
      >
        <RefreshCw aria-hidden="true" />
      </Button>
    </>
  );
}
