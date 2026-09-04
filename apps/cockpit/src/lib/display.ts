export type SignedTone = "up" | "down" | "flat" | "unknown";
export type StatusTone = "ok" | "warn" | "down" | "neutral";

const NUMERIC = /^[+-]?\d+(?:\.\d+)?$/;

export function signedTone(raw: string): SignedTone {
  const trimmed = raw.trim();
  if (!NUMERIC.test(trimmed)) {
    return "unknown";
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value)) {
    return "unknown";
  }
  if (value > 0) {
    return "up";
  }
  if (value < 0) {
    return "down";
  }
  return "flat";
}

export function formatGroupedNumber(raw: string): string {
  const trimmed = raw.trim();
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(trimmed);
  if (match === null) {
    return raw;
  }
  const sign = match[1] ?? "";
  const whole = match[2] ?? trimmed;
  const fraction = match[3];
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fraction === undefined ? `${sign}${grouped}` : `${sign}${grouped}.${fraction}`;
}

export function yesNo(value: boolean): "yes" | "no" {
  return value ? "yes" : "no";
}

export function healthTone(status: string): StatusTone {
  if (status === "COMPLETED_FLAT" || status === "COMPLETED") {
    return "ok";
  }
  if (status === "BOUNDED_TIMEOUT" || status === "OPERATOR_STOP") {
    return "warn";
  }
  if (status === "RISK_REJECTED" || status === "FAILED") {
    return "down";
  }
  return "neutral";
}

export const DATA1A_RUNNING_PENDING_HEALTH = "RUNNING (health JSON pending until stop)";

export type Data1AHealthView = {
  health?: { status: string } | undefined;
  health_missing: boolean;
  health_error?: string | undefined;
  parts: {
    raw_dir_present: boolean;
    count?: number | undefined;
    last_part_mtime_utc?: string | undefined;
  };
};

export type Data1AHealthPresentation = {
  statusLabel: string;
  tileLabel: string;
  tone: StatusTone;
  note: string;
  live: boolean;
};

export function data1aCaptureHealthPresentation(
  snapshot: Data1AHealthView,
): Data1AHealthPresentation {
  if (snapshot.health !== undefined) {
    return {
      statusLabel: snapshot.health.status,
      tileLabel: snapshot.health.status,
      tone: healthTone(snapshot.health.status),
      note: "Copied from capture-health.json",
      live: false,
    };
  }
  if (!snapshot.health_missing) {
    return {
      statusLabel: "UNREADABLE",
      tileLabel: "UNREADABLE",
      tone: "warn",
      note: snapshot.health_error ?? "capture-health.json is unreadable.",
      live: false,
    };
  }
  const partCount = snapshot.parts.count ?? 0;
  const partsLookLive =
    snapshot.parts.raw_dir_present &&
    partCount > 0 &&
    snapshot.parts.last_part_mtime_utc !== undefined;
  if (partsLookLive) {
    return {
      statusLabel: DATA1A_RUNNING_PENDING_HEALTH,
      tileLabel: "RUNNING",
      tone: "ok",
      note: "capture-health.json is written at stop; growing part count and last mtime are the live signal.",
      live: true,
    };
  }
  return {
    statusLabel: "NOT WRITTEN",
    tileLabel: "NOT WRITTEN",
    tone: "warn",
    note: snapshot.health_error ?? "capture-health.json is not written yet",
    live: false,
  };
}

export function presentCopiedNumber(value: number | undefined): string {
  return value === undefined ? "n/a" : String(value);
}

export function presentCopiedText(value: string | undefined): string {
  return value === undefined || value === "" ? "n/a" : value;
}

export function uniqueStrings(items: string[]): string[] {
  return [...new Set(items)];
}
