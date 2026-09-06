type KvTone = "up" | "down" | "flat" | "ok" | "warn" | "neutral" | "paper";

export type KvRow = {
  label: string;
  value: string;
  tone?: KvTone;
  title?: string;
  detail?: string;
};

export function KvTable({ rows }: { rows: KvRow[] }) {
  return (
    <dl className="kv">
      {rows.map((row) => (
        <div className="kv-row" key={row.label}>
          <dt>{row.label}</dt>
          <dd className={row.tone ? `tone-${row.tone}` : undefined} title={row.title}>
            {row.value}
            {row.detail !== undefined ? <span className="kv-detail">{row.detail}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}
