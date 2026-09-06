import { COCKPIT_NAV_ZONES } from "../lib/zones";

export function ZoneNav() {
  return (
    <nav className="zone-nav" aria-label="Cockpit zones">
      {COCKPIT_NAV_ZONES.map((zone) => (
        <a key={zone.id} href={`#${zone.id}`}>
          {zone.label}
        </a>
      ))}
    </nav>
  );
}
