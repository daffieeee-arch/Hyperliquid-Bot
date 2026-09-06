import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-[var(--radius)] border px-1.5 py-0.5 text-[0.62rem] font-semibold tracking-[0.08em] uppercase",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        paper: "border-transparent bg-[var(--paper)] text-[var(--paper-ink)]",
        outline: "border-border text-muted-foreground",
        warn: "border-transparent bg-[var(--warn)]/15 text-[var(--warn)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant,
  ...props
}: ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
