import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { KvTable } from "./kv-table";
import type { DataRetainIdentityCard, SoakIdentityCard } from "../lib/identity-cards";

export function SoakIdentityCardPanel({ card }: { card: SoakIdentityCard }) {
  return (
    <Card aria-label="COURSE-1 PAPER soak identity">
      <CardHeader>
        <CardTitle>COURSE-1 soak</CardTitle>
        <CardDescription>PAPER run identity only · never blended with DATA retain</CardDescription>
      </CardHeader>
      <CardContent>
        {card.error !== undefined ? <p className="error">{card.error}</p> : null}
        <KvTable
          rows={[
            { label: "Mode", value: card.mode, tone: "paper" },
            { label: "Soak run", value: card.runId },
            { label: "Source", value: card.source },
            { label: "Instrument", value: card.instrument },
            { label: "Soak", value: card.soakSeconds },
            { label: "Feed", value: card.feed },
            { label: "State", value: card.state },
            { label: "Venue authoritative", value: card.venueAuthoritative },
            { label: "D22-B recon", value: card.d22bReconciliation },
          ]}
        />
      </CardContent>
    </Card>
  );
}

export function DataRetainIdentityCardPanel({ card }: { card: DataRetainIdentityCard }) {
  return (
    <Card aria-label="DATA retain identity">
      <CardHeader>
        <CardTitle>DATA retain</CardTitle>
        <CardDescription>MD retain binds only · not soak PnL · not one blended run</CardDescription>
      </CardHeader>
      <CardContent>
        {card.error !== undefined ? <p className="error">{card.error}</p> : null}
        <p className="panel-kicker">{card.glanceLine}</p>
        <div className="markets-scroll">
          <table className="markets-table">
            <thead>
              <tr>
                <th>Venue</th>
                <th>Series</th>
                <th>Status</th>
                <th>Age</th>
                <th>Bind</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {card.venues.map((venue) => (
                <tr key={venue.id}>
                  <td>{venue.chip}</td>
                  <td>{venue.series}</td>
                  <td className={venue.live ? "tone-ok" : "tone-warn"}>{venue.status}</td>
                  <td>{venue.lastPartAge}</td>
                  <td>{venue.binding}</td>
                  <td className="venue-strip-run">{venue.runId}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {card.overlapNote !== "" ? <p className="note">{card.overlapNote}</p> : null}
      </CardContent>
    </Card>
  );
}
