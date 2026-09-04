type MetricTone = "up" | "down" | "flat" | "ok" | "warn" | "neutral" | "paper";

export function MetricTile({
  label,
  value,
  meta,
  note,
  tone,
  live,
}: {
  label: string;
  value: string;
  meta?: string;
  note?: string;
  tone?: MetricTone;
  live?: boolean;
}) {
  return (
    <article className="metric">
      <h2>{label}</h2>
      <p className={tone ? `metric-value tone-${tone}` : "metric-value"}>
        {live === true ? <span className="live-dot" aria-hidden="true" /> : null}
        {value}
      </p>
      {meta !== undefined ? <p className="metric-meta">{meta}</p> : null}
      {note !== undefined ? <p className="metric-note">{note}</p> : null}
    </article>
  );
}
