import type { DataTone } from "./data-state";
import type { VenueMarketTape } from "./market-tape-types";
import type { PaperRunLifecycle } from "./paper-bot";
import type { CaptureBindingSource, VenueCaptureChip, VenueCaptureStripResponse } from "./types";

/**
 * Where the numbers on a screen come from.
 *
 * Distinct from freshness (`DataState`): a historical run can be perfectly
 * readable and still must not be mistaken for a live desk, and the repository
 * fixture must never pass for a capture. `mixed` only occurs at screen level
 * when bound venues disagree.
 */
export const DATA_ORIGINS = ["live", "historical", "demo", "unbound", "mixed"] as const;
export type DataOrigin = (typeof DATA_ORIGINS)[number];

export type DataOriginMeta = {
  origin: DataOrigin;
  label: string;
  tone: DataTone | "paper";
  meaning: string;
};

const META: Record<DataOrigin, DataOriginMeta> = {
  live: {
    origin: "live",
    label: "LIVE CAPTURE",
    tone: "ok",
    meaning: "A collector is publishing fresh Parquet parts into this run right now.",
  },
  historical: {
    origin: "historical",
    label: "HISTORICAL RUN",
    tone: "info",
    meaning: "A real run directory that is no longer being written; values are a snapshot.",
  },
  demo: {
    origin: "demo",
    label: "DEMO FIXTURE",
    tone: "warn",
    meaning: "The repository fixture; not a capture and not a run you started.",
  },
  unbound: {
    origin: "unbound",
    label: "UNBOUND",
    tone: "muted",
    meaning: "No run is bound or its directory is missing; nothing is shown from it.",
  },
  mixed: {
    origin: "mixed",
    label: "MIXED SOURCES",
    tone: "warn",
    meaning: "Bound venues come from different origins; read each venue's own badge.",
  },
};

export function dataOriginMeta(origin: DataOrigin): DataOriginMeta {
  return META[origin];
}

function originFromBinding(
  bindingSource: CaptureBindingSource,
  live: boolean,
  missing: boolean,
): DataOrigin {
  if (bindingSource === "default-fixture") {
    return "demo";
  }
  if (missing || bindingSource === "unbound") {
    return "unbound";
  }
  return live ? "live" : "historical";
}

export function captureChipOrigin(chip: VenueCaptureChip): DataOrigin {
  return originFromBinding(chip.binding_source, chip.live, chip.status === "MISSING");
}

/**
 * Origin of a venue's stored market data. Liveness is the capture chip's
 * verdict when available; the tape alone only knows whether it could read.
 */
export function marketTapeOrigin(tape: VenueMarketTape, chip?: VenueCaptureChip): DataOrigin {
  if (tape.status !== "ok") {
    return tape.bindingSource === "default-fixture" ? "demo" : "unbound";
  }
  return originFromBinding(tape.bindingSource, chip?.live ?? false, false);
}

export function paperOrigin(lifecycle: PaperRunLifecycle): DataOrigin {
  switch (lifecycle.artifactSource) {
    case "default-fixture":
      return "demo";
    case "unavailable":
      return "unbound";
    case "paper-run-dir":
    case "path-contract":
      break;
    default: {
      const exhaustive: never = lifecycle.artifactSource;
      throw new Error(`Unhandled PAPER artifact source: ${String(exhaustive)}`);
    }
  }
  switch (lifecycle.state) {
    case "in-flight":
      return "live";
    case "historical":
      return "historical";
    case "unavailable":
      return "unbound";
    default: {
      const exhaustive: never = lifecycle.state;
      throw new Error(`Unhandled PAPER lifecycle state: ${String(exhaustive)}`);
    }
  }
}

export type OriginSummary = {
  origin: DataOrigin;
  counts: Record<Exclude<DataOrigin, "mixed">, number>;
};

/**
 * Screen-level origin across bound venues. Demo wins outright because a
 * single fixture venue next to live ones is the most misleading combination.
 */
export function summariseOrigins(origins: readonly DataOrigin[]): OriginSummary {
  const counts: OriginSummary["counts"] = { live: 0, historical: 0, demo: 0, unbound: 0 };
  for (const origin of origins) {
    if (origin !== "mixed") {
      counts[origin] += 1;
    }
  }
  const bound = counts.live + counts.historical + counts.demo;
  let origin: DataOrigin;
  if (counts.demo > 0) {
    origin = "demo";
  } else if (bound === 0) {
    origin = "unbound";
  } else if (counts.live === bound) {
    origin = "live";
  } else if (counts.live === 0) {
    origin = "historical";
  } else {
    origin = "mixed";
  }
  return { origin, counts };
}

export function stripOrigin(strip: VenueCaptureStripResponse): OriginSummary {
  if (!strip.ok) {
    return summariseOrigins([]);
  }
  return summariseOrigins(strip.strip.venues.map(captureChipOrigin));
}

export function describeOriginSummary(summary: OriginSummary): string {
  const { counts } = summary;
  const parts: string[] = [];
  if (counts.live > 0) parts.push(`${String(counts.live)} live`);
  if (counts.historical > 0) parts.push(`${String(counts.historical)} historical`);
  if (counts.demo > 0) parts.push(`${String(counts.demo)} demo fixture`);
  if (counts.unbound > 0) parts.push(`${String(counts.unbound)} unbound`);
  return parts.length === 0 ? "No venue bound." : parts.join(" · ");
}
