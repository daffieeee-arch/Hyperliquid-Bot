"use client";

import { RefreshCw } from "lucide-react";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { OriginBadge } from "../ui/origin-badge";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { describeOriginSummary, stripOrigin } from "../../lib/data-origin";
import { captureChipDataState, dataStateTone, worstDataState } from "../../lib/data-state";
import type { VenueCaptureQuery } from "../../lib/paths";
import { describePollRead, formatAgeSeconds, secondsBetween } from "../../lib/poll-state";
import { describeRefreshFeedback } from "../../lib/refresh-activity";
import { localClockLabel } from "../../lib/time-display";
import type { VenueCaptureStripResponse } from "../../lib/types";
import { useNow } from "../../lib/use-now";
import { useVenueCapturePoll } from "../../lib/use-venue-capture";

const PENDING = "__pending__";
const PENDING_STRIP: VenueCaptureStripResponse = { ok: false, error: PENDING };

/**
 * Topbar freshness pulse and refresh control.
 *
 * Shows the worst capture state, the time of the last *successful* read and a
 * ticking age measured from that read. The refresh clock itself is never
 * displayed as a timestamp: a tick that failed leaves the previous values on
 * screen and turns the pulse red instead of advancing the clock.
 *
 * The refresh button is disabled and spins while any keyed poll is in flight;
 * once every request of a tick has settled the label says whether the tick
 * updated anything, was already current, or failed and why.
 */
export function CapturePulse({ query }: { query: VenueCaptureQuery }) {
  const { token, refreshNow, paused, setPaused, intervalMs, activity } = useCockpitRefresh();
  const poll = useVenueCapturePoll(query, PENDING_STRIP, token);
  const nowIso = useNow();
  const strip = poll.data;
  const pending = !strip.ok && strip.error === PENDING && poll.error === undefined;
  const read = describePollRead(poll);
  // Ticks from the last *successful* read only; a failed attempt never resets it.
  const readAgeS = secondsBetween(poll.lastSuccessAt, nowIso);
  const readLabel =
    readAgeS === undefined ? read.label : `${read.label} · ${formatAgeSeconds(readAgeS)} ago`;
  const origin = stripOrigin(strip);
  const feedback = describeRefreshFeedback(activity, nowIso);
  const settledKey = activity.lastOutcome?.settledAt ?? "none";

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
          title: `Worst capture state: ${worst}. Fresh ≤ ${String(strip.strip.fresh_max_s)}s. Newest part ${localClockLabel(
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
        key={settledKey}
        tone={read.tone}
        className={
          feedback.busy || activity.lastOutcome === null
            ? "topbar-fresh read-age"
            : "topbar-fresh read-age badge-pulse"
        }
        title={`${read.detail} Auto-refresh every ${String(intervalMs / 1000)}s${paused ? " (paused)" : ""}.`}
      >
        {readLabel}
      </Badge>
      <span
        className={`refresh-feedback tone-${feedback.tone}`}
        title={`${feedback.detail} Auto-refresh every ${String(intervalMs / 1000)}s${paused ? " (paused)" : ""}.`}
        role="status"
        aria-live="polite"
      >
        {paused && !feedback.busy ? `paused · ${feedback.label}` : feedback.label}
      </span>
      <Button
        type="button"
        variant="outline"
        size="icon"
        disabled={feedback.busy}
        aria-busy={feedback.busy}
        title={
          feedback.busy
            ? "Refreshing…"
            : paused
              ? "Auto-refresh paused — resume and refresh now"
              : "Refresh now"
        }
        aria-label={
          feedback.busy ? "Refresh in progress" : paused ? "Resume auto refresh" : "Refresh now"
        }
        onClick={() => {
          if (paused) {
            setPaused(false);
          }
          refreshNow();
        }}
      >
        <RefreshCw aria-hidden="true" className={feedback.busy ? "spin" : undefined} />
      </Button>
    </>
  );
}
