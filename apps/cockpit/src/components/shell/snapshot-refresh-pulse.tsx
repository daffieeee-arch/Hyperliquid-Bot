"use client";

import { RefreshCw } from "lucide-react";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { describeRefreshFeedback } from "../../lib/refresh-activity";
import { useNow } from "../../lib/use-now";

/** The native panel owns its snapshot read; this control never polls capture data. */
export function SnapshotRefreshPulse() {
  const { refreshNow, paused, setPaused, intervalMs, activity } = useCockpitRefresh();
  const feedback = describeRefreshFeedback(activity, useNow());
  return (
    <>
      <Badge
        tone="muted"
        title="Snapshot reads do not establish an active feed or continuous engine operation."
      >
        Snapshot · liveness unverified
      </Badge>
      <span
        className={`refresh-feedback tone-${feedback.tone}`}
        role="status"
        aria-live="polite"
        title={`${feedback.detail} Snapshot refresh every ${String(intervalMs / 1000)}s${paused ? " (paused)" : ""}.`}
      >
        {paused && !feedback.busy ? `paused · ${feedback.label}` : feedback.label}
      </span>
      <Button
        type="button"
        variant="outline"
        size="icon"
        disabled={feedback.busy}
        aria-busy={feedback.busy}
        aria-label={
          feedback.busy ? "Refresh in progress" : paused ? "Resume auto refresh" : "Refresh now"
        }
        title="Reread the bound native PAPER snapshot"
        onClick={() => {
          if (paused) setPaused(false);
          refreshNow();
        }}
      >
        <RefreshCw aria-hidden="true" className={feedback.busy ? "spin" : undefined} />
      </Button>
      <Button
        type="button"
        variant="outline"
        aria-pressed={paused}
        onClick={() => {
          setPaused(!paused);
        }}
      >
        {paused ? "Resume refresh" : "Pause refresh"}
      </Button>
    </>
  );
}
