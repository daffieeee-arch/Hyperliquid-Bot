import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import type { DataTone } from "@/lib/data-state";

export type KvTone = DataTone | "paper" | "neutral" | "up" | "down" | "flat" | "unknown";

export type KvRow = {
  label: string;
  value: ReactNode;
  tone?: KvTone;
  /** Full-precision or provenance text shown under the value. */
  detail?: string;
  /** Native tooltip for the exact copied string. */
  title?: string;
};

export function KvList({ rows, className }: { rows: KvRow[]; className?: string }) {
  return (
    <dl className={cn("kv", className)}>
      {rows.map((row) => (
        <div className="kv-row" key={row.label}>
          <dt>{row.label}</dt>
          <dd className={row.tone ? `tone-${row.tone}` : undefined} title={row.title}>
            {row.value}
            {row.detail === undefined ? null : <span className="kv-detail">{row.detail}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}
