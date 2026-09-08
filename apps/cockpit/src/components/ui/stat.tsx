import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";
import type { DataTone } from "@/lib/data-state";

const TONE_CLASS: Record<DataTone | "paper" | "neutral", string> = {
  ok: "tone-ok",
  warn: "tone-warn",
  down: "tone-down",
  info: "tone-info",
  muted: "tone-muted",
  paper: "tone-paper",
  neutral: "",
};

function Stat({
  label,
  value,
  meta,
  icon,
  tone = "neutral",
  compact = false,
  className,
  ...props
}: ComponentProps<"div"> & {
  label: string;
  value: ReactNode;
  meta?: ReactNode;
  icon?: ReactNode;
  tone?: DataTone | "paper" | "neutral";
  compact?: boolean;
}) {
  return (
    <div data-slot="stat" className={cn("stat", className)} {...props}>
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        {icon === undefined ? null : <span className="stat-icon">{icon}</span>}
      </div>
      <div className={cn("stat-value", compact && "stat-value-sm", TONE_CLASS[tone])}>{value}</div>
      {meta === undefined ? null : <div className="stat-meta">{meta}</div>}
    </div>
  );
}

export { Stat };
