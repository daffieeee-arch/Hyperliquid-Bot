import { DEFAULT_CAPTURE_FRESH_MAX_S } from "./capture-freshness";
import { paperRunSourceLabel, presentCopiedText } from "./display";
import type {
  PaperRunSnapshot,
  VenueCaptureChip,
  VenueCaptureChipStatus,
  VenueCaptureStrip,
  VenueCaptureStripResponse,
} from "./types";

export const DESK_MODE = "PAPER" as const;
export const DESK_UNAVAILABLE = "unavailable";

export type DeskPaperIdentity = {
  mode: typeof DESK_MODE;
  instrument: string;
  runId: string;
  source: string;
  signing: "off";
  jsonAvailable: boolean;
  jsonError: string | undefined;
};

export type DeskStatusCounts = {
  running: number;
  stale: number;
  stopped: number;
  degraded: number;
  missing: number;
};

export type DeskVenueGlance = {
  id: VenueCaptureChip["id"];
  chip: VenueCaptureChip["chip"];
  status: VenueCaptureChipStatus;
  live: boolean;
  tone: VenueCaptureChip["tone"];
  runId: string;
  lastPartAge: string;
};

export type DeskCaptureGlance =
  | {
      ok: true;
      freshMaxS: number;
      venues: DeskVenueGlance[];
      counts: DeskStatusCounts;
      glanceLine: string;
      provenanceNote: string;
      boundRunIds: string[];
    }
  | { ok: false; error: string };

function emptyCounts(): DeskStatusCounts {
  return { running: 0, stale: 0, stopped: 0, degraded: 0, missing: 0 };
}

export function countCaptureStatuses(venues: readonly VenueCaptureChip[]): DeskStatusCounts {
  const counts = emptyCounts();
  for (const venue of venues) {
    switch (venue.status) {
      case "RUNNING":
        counts.running += 1;
        break;
      case "STALE":
        counts.stale += 1;
        break;
      case "STOPPED":
        counts.stopped += 1;
        break;
      case "DEGRADED":
        counts.degraded += 1;
        break;
      case "MISSING":
        counts.missing += 1;
        break;
      default: {
        const exhaustive: never = venue.status;
        throw new Error(`Unhandled venue capture status: ${String(exhaustive)}`);
      }
    }
  }
  return counts;
}

export function deskGlanceLine(counts: DeskStatusCounts, freshMaxS: number): string {
  return (
    `${String(counts.running)} live · ${String(counts.stale)} stale · ` +
    `${String(counts.stopped)} stopped · ${String(counts.degraded)} degraded · ` +
    `${String(counts.missing)} missing · fresh ≤ ${String(freshMaxS)}s`
  );
}

export function deskPaperIdentity(
  snapshot: PaperRunSnapshot | undefined,
  snapshotError: string | undefined,
): DeskPaperIdentity {
  if (snapshot === undefined) {
    return {
      mode: DESK_MODE,
      instrument: DESK_UNAVAILABLE,
      runId: DESK_UNAVAILABLE,
      source: "fail-closed",
      signing: "off",
      jsonAvailable: false,
      jsonError: snapshotError ?? "PAPER run data is unavailable.",
    };
  }
  return {
    mode: DESK_MODE,
    instrument: snapshot.position.instrument_id,
    runId: snapshot.runId,
    source: paperRunSourceLabel(snapshot.source),
    signing: "off",
    jsonAvailable: true,
    jsonError: undefined,
  };
}

export function deskVenueGlance(venue: VenueCaptureChip): DeskVenueGlance {
  return {
    id: venue.id,
    chip: venue.chip,
    status: venue.status,
    live: venue.live,
    tone: venue.tone,
    runId: presentCopiedText(venue.run_id),
    lastPartAge: venue.last_part_age,
  };
}

export function deskCaptureGlanceFromStrip(strip: VenueCaptureStrip): DeskCaptureGlance {
  const counts = countCaptureStatuses(strip.venues);
  const freshMaxS = strip.fresh_max_s > 0 ? strip.fresh_max_s : DEFAULT_CAPTURE_FRESH_MAX_S;
  return {
    ok: true,
    freshMaxS,
    venues: strip.venues.map(deskVenueGlance),
    counts,
    glanceLine: deskGlanceLine(counts, freshMaxS),
    provenanceNote: strip.provenance.overlap_note,
    boundRunIds: strip.provenance.distinct_run_ids,
  };
}

export function deskCaptureGlance(result: VenueCaptureStripResponse): DeskCaptureGlance {
  if (!result.ok) {
    return { ok: false, error: result.error };
  }
  return deskCaptureGlanceFromStrip(result.strip);
}
