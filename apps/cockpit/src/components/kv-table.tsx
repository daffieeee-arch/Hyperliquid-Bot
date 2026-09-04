type KvTone = "up" | "down" | "flat" | "ok" | "warn" | "neutral" | "paper";

export type KvRow = {
  label: string;
  value: string;
  tone?: KvTone;
};

export function KvTable({ rows }: { rows: KvRow[] }) {
  return (
    <dl className="kv">
      {rows.map((row) => (
        <div className="kv-row" key={row.label}>
          <dt>{row.label}</dt>
          <dd className={row.tone ? `tone-${row.tone}` : undefined}>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
