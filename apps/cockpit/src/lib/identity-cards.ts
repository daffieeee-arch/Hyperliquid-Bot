import { deskCaptureGlance, deskPaperIdentity } from "./desk";
import { paperRunSourceLabel, presentCopiedText } from "./display";
import type { PaperRunSnapshot, VenueCaptureChip, VenueCaptureStripResponse } from "./types";

export const IDENTITY_UNAVAILABLE = "UNAVAILABLE";
export const SOAK_IDENTITY_KIND = "course1-soak" as const;
export const DATA_RETAIN_IDENTITY_KIND = "data-retain" as const;

export type SoakIdentityCard = {
  kind: typeof SOAK_IDENTITY_KIND;
  mode: "PAPER";
  runId: string;
  source: string;
  instrument: string;
  soakSeconds: string;
  feed: string;
  state: string;
  venueAuthoritative: string;
  d22bReconciliation: string;
  jsonAvailable: boolean;
  error: string | undefined;
};

export type DataRetainVenueRow = {
  id: VenueCaptureChip["id"];
  chip: VenueCaptureChip["chip"];
  series: VenueCaptureChip["series"];
  runId: string;
  status: string;
  live: boolean;
  binding: VenueCaptureChip["binding_source"];
  lastPartAge: string;
};

export type DataRetainIdentityCard = {
  kind: typeof DATA_RETAIN_IDENTITY_KIND;
  glanceLine: string;
  overlapNote: string;
  venues: DataRetainVenueRow[];
  error: string | undefined;
};

export type SeparateIdentityCards = {
  soak: SoakIdentityCard;
  retain: DataRetainIdentityCard;
};

export function buildSoakIdentityCard(
  snapshot: PaperRunSnapshot | undefined,
  snapshotError: string | undefined,
): SoakIdentityCard {
  const identity = deskPaperIdentity(snapshot, snapshotError);
  if (snapshot === undefined) {
    return {
      kind: SOAK_IDENTITY_KIND,
      mode: "PAPER",
      runId: IDENTITY_UNAVAILABLE,
      source: identity.source,
      instrument: IDENTITY_UNAVAILABLE,
      soakSeconds: IDENTITY_UNAVAILABLE,
      feed: IDENTITY_UNAVAILABLE,
      state: IDENTITY_UNAVAILABLE,
      venueAuthoritative: IDENTITY_UNAVAILABLE,
      d22bReconciliation: IDENTITY_UNAVAILABLE,
      jsonAvailable: false,
      error: identity.jsonError,
    };
  }
  return {
    kind: SOAK_IDENTITY_KIND,
    mode: "PAPER",
    runId: snapshot.runId,
    source: paperRunSourceLabel(snapshot.source),
    instrument: snapshot.position.instrument_id,
    soakSeconds:
      snapshot.claim.seconds === undefined ? "n/a" : `${String(snapshot.claim.seconds)}s`,
    feed: snapshot.claim.feed ?? snapshot.health.feed,
    state: snapshot.claim.state ?? "n/a",
    venueAuthoritative: snapshot.position.venue_authoritative ? "yes" : "no",
    d22bReconciliation:
      snapshot.claim.d22b_venue_authoritative_reconciliation === true ? "yes" : "no",
    jsonAvailable: true,
    error: undefined,
  };
}

export function buildDataRetainIdentityCard(
  strip: VenueCaptureStripResponse,
): DataRetainIdentityCard {
  const glance = deskCaptureGlance(strip);
  if (!glance.ok) {
    return {
      kind: DATA_RETAIN_IDENTITY_KIND,
      glanceLine: IDENTITY_UNAVAILABLE,
      overlapNote: glance.error,
      venues: [],
      error: glance.error,
    };
  }
  return {
    kind: DATA_RETAIN_IDENTITY_KIND,
    glanceLine: glance.glanceLine,
    overlapNote: glance.provenanceNote,
    venues: glance.venues.map((venue) => {
      const chip = strip.ok ? strip.strip.venues.find((row) => row.id === venue.id) : undefined;
      return {
        id: venue.id,
        chip: venue.chip,
        series: chip?.series ?? "DATA-1A",
        runId: presentCopiedText(venue.runId),
        status: venue.status,
        live: venue.live,
        binding: chip?.binding_source ?? "unbound",
        lastPartAge: venue.lastPartAge,
      };
    }),
    error: undefined,
  };
}

export function buildSeparateIdentityCards(
  snapshot: PaperRunSnapshot | undefined,
  snapshotError: string | undefined,
  strip: VenueCaptureStripResponse,
): SeparateIdentityCards {
  return {
    soak: buildSoakIdentityCard(snapshot, snapshotError),
    retain: buildDataRetainIdentityCard(strip),
  };
}

export function identityCardsAreSeparate(cards: SeparateIdentityCards): boolean {
  return cards.soak.kind === SOAK_IDENTITY_KIND && cards.retain.kind === DATA_RETAIN_IDENTITY_KIND;
}
