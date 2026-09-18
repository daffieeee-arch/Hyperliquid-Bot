/**
 * Clock labels for the cockpit.
 *
 * Operators read the cockpit from the Netherlands, so the primary label is
 * Europe/Amsterdam wall-clock time with its zone suffix (CET / CEST). Every
 * source timestamp stays UTC ISO in `title` tooltips and in the data itself;
 * only the presentation is local. The zone is fixed rather than taken from
 * the browser so a phone on holiday and a desktop at home show the same time.
 */
export const COCKPIT_TIME_ZONE = "Europe/Amsterdam";

export const CLOCK_UNKNOWN = "—";

const formatters = new Map<string, Intl.DateTimeFormat>();

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

/** `HH:MM:SSZ`, the exact UTC form kept for tooltips and logs; `—` when unknown. */
export function utcClockLabel(iso: string | null | undefined): string {
  const parsed = parseIso(iso);
  if (parsed === undefined) {
    return CLOCK_UNKNOWN;
  }
  return `${new Date(parsed).toISOString().slice(11, 19)}Z`;
}

/** Local clock followed by the UTC form, for places that show both at once. */
export function dualClockLabel(iso: string | null | undefined): string {
  const local = localClockLabel(iso);
  if (local === CLOCK_UNKNOWN) {
    return CLOCK_UNKNOWN;
  }
  return `${local} (${utcClockLabel(iso)})`;
}
