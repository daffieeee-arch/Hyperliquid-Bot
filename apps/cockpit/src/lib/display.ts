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

export function presentCopiedNumber(value: number | undefined): string {
  return value === undefined ? "n/a" : String(value);
}

export function presentCopiedText(value: string | undefined): string {
  return value === undefined || value === "" ? "n/a" : value;
}

export function uniqueStrings(items: string[]): string[] {
  return [...new Set(items)];
}
