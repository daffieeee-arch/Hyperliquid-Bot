import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";
import type { DataTone } from "@/lib/data-state";

export type BadgeTone = DataTone | "paper" | "outline";

const TONE_CLASS: Record<BadgeTone, string> = {
  ok: "badge-ok",
  warn: "badge-warn",
  down: "badge-down",
  info: "badge-info",
  muted: "badge-muted",
  paper: "badge-paper",
  outline: "badge-outline",
};

function Badge({
  tone = "muted",
  dot = false,
  live = false,
  className,
  children,
  ...props
}: ComponentProps<"span"> & { tone?: BadgeTone; dot?: boolean; live?: boolean }) {
  return (
    <span data-slot="badge" className={cn("badge", TONE_CLASS[tone], className)} {...props}>
      {dot ? <span className={cn("dot", live && "dot-live")} aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export { Badge };
