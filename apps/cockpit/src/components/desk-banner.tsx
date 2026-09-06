"use client";

import { ThemeToggle } from "./theme-toggle";
import { DEFAULT_CAPTURE_FRESH_MAX_S } from "../lib/capture-freshness";
import { deskCaptureGlance, deskPaperIdentity } from "../lib/desk";
import type { VenueCaptureQuery } from "../lib/paths";
import type { PaperRunSnapshot, VenueCaptureStripResponse } from "../lib/types";
import { useVenueCapturePoll } from "../lib/use-venue-capture";

export function DeskBanner({
  snapshot,
  snapshotError,
  query,
  initial,
}: {
  snapshot: PaperRunSnapshot | undefined;
  snapshotError: string | undefined;
  query: VenueCaptureQuery;
  initial: VenueCaptureStripResponse;
}) {
  const identity = deskPaperIdentity(snapshot, snapshotError);
  const glance = deskCaptureGlance(useVenueCapturePoll(query, initial));
  const freshMaxSeconds = glance.ok ? glance.freshMaxS : DEFAULT_CAPTURE_FRESH_MAX_S;
  const boundRuns =
    glance.ok && glance.boundRunIds.length > 0 ? glance.boundRunIds.join(" · ") : "n/a";

  return (
    <header className="desk-banner" aria-label="DESK">
      <div className="desk-row">
        <div className="brand">
          <span className="brand-mark">HLQ</span>
          <div className="brand-copy">
            <strong>DESK</strong>
            <span>BTC-PERP · COURSE-1 · PAPER</span>
          </div>
        </div>
        <div className="paper-badge" role="status">
          <span className="paper-badge-mode">PAPER TRADING</span>
          <span className="paper-badge-warn">NO REAL CAPITAL</span>
        </div>
        <p className="masthead-flags">
          <span>SIGNING OFF</span>
          <span>NO KEYS</span>
          <span>NO ORDERS</span>
          <span>NO LIVE</span>
          <span>FRESH ≤ {String(freshMaxSeconds)}s</span>
        </p>
        <ThemeToggle />
      </div>
      <dl className="identity">
        <div>
          <dt>Mode</dt>
          <dd className="tone-paper">{identity.mode}</dd>
        </div>
        <div>
          <dt>Instrument</dt>
          <dd>{identity.instrument}</dd>
        </div>
        <div>
          <dt>Paper run</dt>
          <dd>{identity.runId}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>{identity.source}</dd>
        </div>
        <div>
          <dt>Signing</dt>
          <dd>{identity.signing}</dd>
        </div>
        <div>
          <dt>JSON</dt>
          <dd className={identity.jsonAvailable ? "tone-ok" : "tone-warn"}>
            {identity.jsonAvailable ? "copied" : "missing"}
          </dd>
        </div>
      </dl>
      <div className="desk-glance" aria-label="Capture live versus stale">
        <span className="desk-label">CAPTURE</span>
        {glance.ok ? (
          <>
            <ul className="desk-chips">
              {glance.venues.map((venue) => (
                <li key={venue.id} className={`desk-chip tone-${venue.tone}`}>
                  {venue.live ? <span className="live-dot" aria-hidden="true" /> : null}
                  <span>
                    {venue.chip} {venue.status}
                  </span>
                  <span className="desk-chip-age">{venue.lastPartAge}</span>
                </li>
              ))}
            </ul>
            <p className="desk-glance-line">{glance.glanceLine}</p>
          </>
        ) : (
          <p className="error">{glance.error}</p>
        )}
      </div>
      <p className="desk-provenance">
        Bound {boundRuns}
        {glance.ok && glance.provenanceNote !== "" ? ` · ${glance.provenanceNote}` : ""}
        {identity.jsonError !== undefined ? ` · PAPER JSON ${identity.jsonError}` : ""}
      </p>
    </header>
  );
}
