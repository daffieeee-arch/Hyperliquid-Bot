/**
 * Clock labels for the cockpit.
 *
 * Operators read the cockpit from the Netherlands, so every operator-facing
 * label is Europe/Amsterdam wall-clock time with its zone suffix (CET / CEST).
 * Source payloads stay UTC ISO internally. The UI does not print a trailing
 * `Z`. The zone is fixed rather than taken from the browser so a phone on
 * holiday and a desktop at home show the same time.
 */
export const COCKPIT_TIME_ZONE = "Europe/Amsterdam";

export const CLOCK_UNKNOWN = "—";

const formatters = new Map<string, Intl.DateTimeFormat>();
const dateTimeFormatters = new Map<string, Intl.DateTimeFormat>();

function formatterFor(zone: string): Intl.DateTimeFormat {
  const cached = formatters.get(zone);
  if (cached !== undefined) {
    return cached;
  }
  const created = new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    hourCycle: "h23",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
  formatters.set(zone, created);
  return created;
}

function dateTimeFormatterFor(zone: string): Intl.DateTimeFormat {
  const cached = dateTimeFormatters.get(zone);
  if (cached !== undefined) {
    return cached;
  }
  const created = new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
  dateTimeFormatters.set(zone, created);
  return created;
}

function parseIso(iso: string | null | undefined): number | undefined {
  if (iso === null || iso === undefined) {
    return undefined;
  }
  const parsed = Date.parse(iso);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** `HH:MM:SS CEST` (or `CET`) in the cockpit zone; `—` when unknown or unparseable. */
export function localClockLabel(
  iso: string | null | undefined,
  zone: string = COCKPIT_TIME_ZONE,
): string {
  const parsed = parseIso(iso);
  if (parsed === undefined) {
    return CLOCK_UNKNOWN;
  }
  return formatterFor(zone).format(new Date(parsed));
}

/** `YYYY-MM-DD HH:MM:SS CEST` (or `CET`); `—` when unknown or unparseable. */
export function localDateTimeLabel(
  iso: string | null | undefined,
  zone: string = COCKPIT_TIME_ZONE,
): string {
  const parsed = parseIso(iso);
  if (parsed === undefined) {
    return CLOCK_UNKNOWN;
  }
  const parts = dateTimeFormatterFor(zone).formatToParts(new Date(parsed));
  const pick = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? "";
  const zoneName = pick("timeZoneName");
  const clock = `${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}:${pick("second")}`;
  return zoneName === "" ? clock : `${clock} ${zoneName}`;
}

/**
 * Operator-facing clock. Same as {@link localClockLabel}: Amsterdam with
 * CET/CEST, never a trailing `Z`.
 */
export function dualClockLabel(iso: string | null | undefined): string {
  return localClockLabel(iso);
}
