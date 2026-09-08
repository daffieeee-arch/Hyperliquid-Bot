import { AlertTriangle, CircleAlert, CircleSlash, Clock, Info } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";
import { dataStateMeta, type DataState } from "@/lib/data-state";

const ICONS = {
  missing: CircleSlash,
  stale: Clock,
  error: CircleAlert,
  pending: Info,
  ok: Info,
} as const;

const CLASS: Record<DataState, string> = {
  missing: "notice-missing",
  stale: "notice-stale",
  error: "notice-error",
  pending: "notice-info",
  ok: "notice-ok",
};

/**
 * Compact explanation of why a value is not shown.
 *
 * Keeps `missing` (never written), `stale` (past the freshness bound) and
 * `error` (present but unreadable) visually distinct so the operator can tell
 * a gap from a fault at a glance.
 */
function Notice({
  state,
  title,
  children,
  className,
  ...props
}: ComponentProps<"div"> & { state: DataState; title?: string; children?: ReactNode }) {
  const meta = dataStateMeta(state);
  const Icon = state === "stale" ? Clock : state === "error" ? AlertTriangle : ICONS[state];
  return (
    <div
      data-slot="notice"
      data-state={state}
      role={state === "error" ? "alert" : undefined}
      className={cn("notice", CLASS[state], className)}
      {...props}
    >
      <Icon className="notice-icon" size={14} aria-hidden="true" />
      <div className="notice-body">
        <strong>{title ?? meta.label}</strong>
        <span>{children ?? meta.meaning}</span>
      </div>
    </div>
  );
}

export { Notice };
